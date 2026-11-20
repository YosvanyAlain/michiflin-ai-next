from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from .errors import AppError
from .storage import Ledger

ACTIVE = frozenset({"running", "pausing", "cancelling"})
PENDING = frozenset({"queued", "running", "pausing", "cancelling", "paused", "uncertain"})
TERMINAL = frozenset({"completed", "failed", "cancelled", "expired"})


def _json(value: object, maximum: int) -> str:
    if not isinstance(value, dict):
        raise AppError("invalid_job_data")
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise AppError("invalid_job_data") from None
    if len(encoded.encode()) > maximum:
        raise AppError("job_data_limit")
    return encoded


@dataclass(frozen=True)
class Kind:
    validate: Callable[[dict], None] = field(repr=False)
    seconds: int = 60

    def __post_init__(self):
        if not callable(self.validate) or type(self.seconds) is not int or not 1 <= self.seconds <= 900:
            raise ValueError("Invalid trusted job kind")


@dataclass(frozen=True)
class Limits:
    pending_owner: int = 3
    pending_global: int = 64
    workers_owner: int = 1
    workers_global: int = 2
    bytes_owner: int = 64 * 1024 * 1024
    bytes_global: int = 256 * 1024 * 1024
    records_owner: int = 256
    records_global: int = 4096
    lease_seconds: int = 30

    def __post_init__(self):
        for name, maximum in (("pending_owner", 64), ("pending_global", 1024), ("workers_owner", 8),
                              ("workers_global", 16), ("bytes_owner", 1024**3), ("bytes_global", 4 * 1024**3),
                              ("records_owner", 4096), ("records_global", 65536), ("lease_seconds", 60)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("Invalid trusted job limits")
        if any(getattr(self, name + "_owner") > getattr(self, name + "_global")
               for name in ("pending", "workers", "bytes", "records")):
            raise ValueError("Per-owner limits cannot exceed service limits")


@dataclass(frozen=True)
class Claim:
    identifier: str
    kind: str
    deadline: float
    token: str = field(repr=False)
    payload: dict = field(repr=False)
    checkpoint: dict = field(repr=False)


class JobQueue:
    """Durable admission and worker fencing; not an executor or an OS sandbox."""

    def __init__(self, ledger: Ledger, *, kinds: Mapping[str, Kind] | None = None,
                 limits: Limits | None = None, clock: Callable[[], float] = time.time) -> None:
        self.ledger, self.kinds, self.limits, self.clock = ledger, dict(kinds or {}), limits or Limits(), clock
        if any(not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_.]{1,63}\.v[1-9][0-9]?", key)
               or not isinstance(value, Kind) for key, value in self.kinds.items()):
            raise ValueError("Job handlers require reviewed versioned kind names")
        with ledger.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, chat INTEGER NOT NULL,
                    instance INTEGER NOT NULL, request_id TEXT NOT NULL, digest TEXT NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('queued','running','pausing','cancelling',
                        'paused','completed','failed','cancelled','expired','uncertain')),
                    created REAL NOT NULL, updated REAL NOT NULL, expires REAL NOT NULL,
                    available REAL NOT NULL, remaining REAL NOT NULL, started REAL,
                    deadline REAL, lease_until REAL, lease_hash TEXT,
                    slot INTEGER NOT NULL DEFAULT 0 CHECK(slot IN (0,1)), attempts INTEGER NOT NULL DEFAULT 0,
                    reserved_bytes INTEGER NOT NULL, current INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL DEFAULT 0, checkpoint TEXT NOT NULL DEFAULT '{}',
                    result TEXT, error TEXT,
                    UNIQUE(instance,request_id)
                );
                CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(state,available,created);
                CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(owner,created);
                CREATE INDEX IF NOT EXISTS jobs_slots ON jobs(slot,owner);
                CREATE TABLE IF NOT EXISTS job_turns(owner TEXT PRIMARY KEY, turn INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS job_clock(id INTEGER PRIMARY KEY CHECK(id=1), turn INTEGER NOT NULL);
                INSERT OR IGNORE INTO job_clock VALUES(1,0);
                CREATE TABLE IF NOT EXISTS job_resolutions (
                    job TEXT PRIMARY KEY REFERENCES jobs(id), resolved REAL NOT NULL,
                    outcome TEXT NOT NULL, evidence_sha256 TEXT NOT NULL
                );
            """)

    def _now(self) -> float:
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
            raise AppError("job_clock_invalid")
        return now

    @staticmethod
    def _identifier(identifier: object) -> None:
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise AppError("invalid_job")

    @staticmethod
    def _binding(db, instance, owner, chat):
        row = db.execute("SELECT * FROM instances WHERE id=?", (instance,)).fetchone()
        if not row or row["owner"] != owner or row["chat"] != chat or row["revoked"]:
            raise AppError("instance_not_authorized")

    def _owned(self, db, identifier, owner, chat, instance):
        self._identifier(identifier)
        self._binding(db, instance, owner, chat)
        row = db.execute("SELECT * FROM jobs WHERE id=? AND owner=? AND chat=? AND instance=?",
                         (identifier, owner, chat, instance)).fetchone()
        if row is None:
            raise AppError("job_not_found")
        return row

    @staticmethod
    def _public(row, *, result=False):
        value = {key: row[key] for key in ("id", "kind", "state", "created", "updated", "expires", "current", "total", "error")}
        value["worker_stopped"] = not bool(row["slot"])
        if result and row["state"] == "completed":
            value["result"] = json.loads(row["result"])
        return value

    def enqueue(self, owner: str, chat: int, instance: int, request_id: str, kind: str, payload: dict,
                *, reserved_bytes: int = 0, ttl: int = 3600, delay: int = 0) -> dict:
        if not isinstance(kind, str) or kind not in self.kinds:
            raise AppError("job_kind_unavailable")
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{12,80}", request_id):
            raise AppError("invalid_request_id")
        if (type(ttl) is not int or not 1 <= ttl <= 86400 or type(delay) is not int or not 0 <= delay < ttl
                or type(reserved_bytes) is not int or not 0 <= reserved_bytes <= self.limits.bytes_owner):
            raise AppError("invalid_job_budget")
        encoded = _json(payload, 16000)
        self.kinds[kind].validate(json.loads(encoded))
        digest = hashlib.sha256(_json({"kind": kind, "payload": json.loads(encoded), "bytes": reserved_bytes,
                                      "ttl": ttl, "delay": delay}, 20000).encode()).hexdigest()
        now = self._now()
        with self.ledger.transaction() as db:
            self._binding(db, instance, owner, chat)
            previous = db.execute("SELECT * FROM jobs WHERE instance=? AND request_id=?", (instance, request_id)).fetchone()
            if previous:
                if previous["owner"] != owner or previous["chat"] != chat or previous["digest"] != digest:
                    raise AppError("request_identity_conflict")
                return self._public(previous)
            for scope, clause, values in (("owner", " WHERE owner=?", (owner,)), ("global", "", ())):
                counts = db.execute("SELECT COUNT(*),COALESCE(SUM(reserved_bytes),0) FROM jobs" + clause, values).fetchone()
                if counts[0] >= getattr(self.limits, "records_" + scope):
                    raise AppError("job_history_limit")
                if counts[1] + reserved_bytes > getattr(self.limits, "bytes_" + scope):
                    raise AppError("job_storage_limit")
                pending = db.execute("SELECT COUNT(*) FROM jobs" + clause + (" AND " if clause else " WHERE ")
                                     + "state IN ('queued','running','pausing','cancelling','paused','uncertain')", values).fetchone()[0]
                if pending >= getattr(self.limits, "pending_" + scope):
                    raise AppError("job_capacity_limit", retryable=True)
            identifier = secrets.token_hex(16)
            db.execute("""INSERT INTO jobs(id,owner,chat,instance,request_id,digest,kind,payload,state,
                created,updated,expires,available,remaining,reserved_bytes)
                VALUES(?,?,?,?,?,?,?,?,'queued',?,?,?,?,?,?)""",
                       (identifier, owner, chat, instance, request_id, digest, kind, encoded, now, now,
                        now + ttl, now + delay, self.kinds[kind].seconds, reserved_bytes))
            db.execute("INSERT OR IGNORE INTO job_turns SELECT ?,turn FROM job_clock WHERE id=1", (owner,))
            return self._public(db.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone())

    def recover(self, *, startup: bool = False) -> None:
        """Startup requires the exclusive runtime lock; uncertainty never releases a worker slot."""
        now = self._now()
        with self.ledger.transaction() as db:
            db.execute("""UPDATE jobs SET state='expired',updated=?,error='job_expired',
                reserved_bytes=CASE WHEN attempts=0 THEN 0 ELSE reserved_bytes END
                WHERE state IN ('queued','paused') AND expires<=?""", (now, now))
            db.execute("""UPDATE jobs SET state='uncertain',updated=?,error='job_outcome_uncertain'
                WHERE state IN ('running','pausing','cancelling') AND (? OR lease_until<=? OR deadline<=?)""",
                       (now, int(startup), now, now))
            db.execute("""UPDATE jobs SET state='cancelled',updated=?,error='instance_not_authorized',
                reserved_bytes=CASE WHEN attempts=0 THEN 0 ELSE reserved_bytes END
                WHERE state IN ('queued','paused') AND NOT EXISTS
                (SELECT 1 FROM instances i WHERE i.id=jobs.instance AND i.owner=jobs.owner
                 AND i.chat=jobs.chat AND i.revoked=0)""", (now,))

    def claim(self) -> Claim | None:
        self.recover()
        if not self.kinds:
            return None
        now = self._now()
        with self.ledger.transaction() as db:
            if db.execute("SELECT COUNT(*) FROM jobs WHERE slot=1").fetchone()[0] >= self.limits.workers_global:
                return None
            kinds = sorted(self.kinds)
            placeholders = ",".join("?" for _ in kinds)
            row = db.execute("""SELECT j.* FROM jobs j JOIN job_turns t ON j.owner=t.owner
                JOIN instances i ON i.id=j.instance AND i.owner=j.owner AND i.chat=j.chat AND i.revoked=0
                WHERE j.state='queued' AND j.slot=0 AND j.available<=? AND j.expires>? AND j.remaining>0
                AND (SELECT COUNT(*) FROM jobs a WHERE a.owner=j.owner AND a.slot=1)<?
                AND j.kind IN (""" + placeholders + ") ORDER BY t.turn,j.created,j.rowid LIMIT 1",
                             (now, now, self.limits.workers_owner, *kinds)).fetchone()
            if row is None:
                return None
            token = secrets.token_hex(32)
            deadline = min(row["expires"], now + row["remaining"], now + self.kinds[row["kind"]].seconds)
            lease_until = min(deadline, now + self.limits.lease_seconds)
            db.execute("""UPDATE jobs SET state='running',updated=?,started=?,deadline=?,lease_until=?,
                lease_hash=?,slot=1,attempts=attempts+1 WHERE id=?""",
                       (now, now, deadline, lease_until, hashlib.sha256(token.encode()).hexdigest(), row["id"]))
            db.execute("UPDATE job_clock SET turn=turn+1 WHERE id=1")
            db.execute("UPDATE job_turns SET turn=(SELECT turn FROM job_clock WHERE id=1) WHERE owner=?", (row["owner"],))
            return Claim(row["id"], row["kind"], deadline, token, json.loads(row["payload"]), json.loads(row["checkpoint"]))

    def _leased(self, db, claim: Claim, *, live=True):
        if not isinstance(claim, Claim) or not isinstance(claim.token, str) or len(claim.token) != 64:
            raise AppError("job_lease_invalid")
        row = db.execute("SELECT * FROM jobs WHERE id=?", (claim.identifier,)).fetchone()
        if (row is None or not row["slot"] or not row["lease_hash"]
                or not secrets.compare_digest(row["lease_hash"], hashlib.sha256(claim.token.encode()).hexdigest())):
            raise AppError("job_lease_invalid")
        if live and (row["state"] not in ACTIVE or row["lease_until"] <= self._now() or row["deadline"] <= self._now()):
            raise AppError("job_lease_expired")
        return row

    def artifact_input(self, claim: Claim, field: str) -> tuple[str, int, int, object]:
        """Trusted worker binding from the durable lease, never member-supplied owner or host paths."""
        with self.ledger.connection() as db:
            row = self._leased(db, claim)
            self._binding(db, row["instance"], row["owner"], row["chat"])
            return row["owner"], row["chat"], row["instance"], json.loads(row["payload"]).get(field)

    def heartbeat(self, claim: Claim, *, current: int, total: int, checkpoint: dict | None = None) -> dict:
        if type(current) is not int or type(total) is not int or not 0 <= current <= total <= 10**12:
            raise AppError("invalid_job_progress")
        encoded = None if checkpoint is None else _json(checkpoint, 4000)
        now = self._now()
        with self.ledger.transaction() as db:
            row = self._leased(db, claim)
            if current < row["current"] or row["total"] not in (0, total):
                raise AppError("invalid_job_progress")
            state = row["state"]
            try:
                self._binding(db, row["instance"], row["owner"], row["chat"])
            except AppError:
                state = "cancelling"
            db.execute("UPDATE jobs SET state=?,updated=?,current=?,total=?,checkpoint=?,lease_until=? WHERE id=?",
                       (state, now, current, total, row["checkpoint"] if encoded is None else encoded,
                        min(row["deadline"], now + self.limits.lease_seconds), row["id"]))
            return {"state": state, "deadline": row["deadline"], "current": current, "total": total}

    def acknowledge(self, claim: Claim, action: str) -> None:
        if action not in {"pause", "cancel"}:
            raise AppError("invalid_job_control")
        now = self._now()
        with self.ledger.transaction() as db:
            row = self._leased(db, claim)
            if row["state"] != {"pause": "pausing", "cancel": "cancelling"}[action]:
                raise AppError("job_control_not_requested")
            remaining = max(0, row["remaining"] - max(0, now - row["started"]))
            state = "paused" if action == "pause" and remaining > 0 else "cancelled" if action == "cancel" else "expired"
            db.execute("UPDATE jobs SET state=?,remaining=?,updated=? WHERE id=?", (state, remaining, now, row["id"]))

    def finish(self, claim: Claim, *, result: dict | None = None, error: str | None = None) -> None:
        self._finish(claim, result=result, error=error, offline=False)

    def finish_offline(self, claim: Claim, *, result: dict | None = None, error: str | None = None) -> None:
        """Discard side-effect-free output if a control/revocation won the publication race."""
        self._finish(claim, result=result, error=error, offline=True)

    def _finish(self, claim: Claim, *, result: dict | None, error: str | None, offline: bool) -> None:
        if error is not None and (not isinstance(error, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error)):
            raise AppError("invalid_job_error")
        if (result is None) == (error is None):
            raise AppError("invalid_job_result")
        encoded = _json(result, 64000) if result is not None else None
        with self.ledger.transaction() as db:
            row = self._leased(db, claim)
            now = self._now()
            if offline:
                state = row["state"]
                try:
                    self._binding(db, row["instance"], row["owner"], row["chat"])
                except AppError:
                    state = "cancelling"
                if state in {"pausing", "cancelling"}:
                    remaining = max(0, row["remaining"] - max(0, now - row["started"]))
                    state = "cancelled" if state == "cancelling" else "paused" if remaining > 0 else "expired"
                    db.execute("UPDATE jobs SET state=?,remaining=?,updated=? WHERE id=?", (state, remaining, now, row["id"]))
                    return
                if error is None:
                    db.execute("UPDATE jobs SET current=MAX(total,1),total=MAX(total,1) WHERE id=?", (row["id"],))
            db.execute("UPDATE jobs SET state=?,result=?,error=?,updated=? WHERE id=?",
                       ("completed" if error is None else "failed", encoded, error, now, row["id"]))

    def worker_stopped(self, claim: Claim) -> None:
        """Only a trusted parent may call this after actually reaping the worker process."""
        with self.ledger.transaction() as db:
            row = self._leased(db, claim, live=False)
            state = "uncertain" if row["state"] in ACTIVE else row["state"]
            db.execute("UPDATE jobs SET state=?,slot=0,lease_hash=NULL,updated=?,error=? WHERE id=?",
                       (state, self._now(), "job_outcome_uncertain" if state == "uncertain" else row["error"], row["id"]))

    def reconcile(self, identifier: str, *, evidence_sha256: str, outcome: str,
                  result: dict | None = None) -> None:
        """Operator-only: verified process exit and external outcome, never a request to retry.

        The caller must first verify that the old supervisor and its entire worker namespace
        no longer exist. An evidence digest identifies that private check without storing it.
        No member/admin operation exposes this method and no automatic recovery calls it.
        """
        self._identifier(identifier)
        if (not isinstance(evidence_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", evidence_sha256)
                or not isinstance(outcome, str) or outcome not in TERMINAL
                or (outcome == "completed") != (result is not None)):
            raise AppError("invalid_job_resolution")
        encoded = _json(result, 64000) if result is not None else None
        with self.ledger.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
            if row is None or row["state"] != "uncertain":
                raise AppError("job_not_uncertain")
            db.execute("INSERT INTO job_resolutions VALUES(?,?,?,?)",
                       (identifier, self._now(), outcome, evidence_sha256))
            db.execute("UPDATE jobs SET state=?,result=?,error=?,slot=0,lease_hash=NULL,updated=? WHERE id=?",
                       (outcome, encoded, None if outcome == "completed" else "job_reconciled",
                        self._now(), identifier))

    def release_storage(self, identifier: str) -> None:
        """Trusted cleanup hook; call only after staged files have actually been removed."""
        self._identifier(identifier)
        with self.ledger.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
            if row is None or row["state"] not in TERMINAL or row["slot"]:
                raise AppError("job_cleanup_not_safe")
            db.execute("UPDATE jobs SET reserved_bytes=0 WHERE id=?", (identifier,))

    def get(self, owner: str, chat: int, instance: int, identifier: object) -> dict:
        with self.ledger.connection() as db:
            return self._public(self._owned(db, identifier, owner, chat, instance), result=True)

    def list(self, owner: str, chat: int, instance: int) -> dict:
        with self.ledger.connection() as db:
            self._binding(db, instance, owner, chat)
            rows = db.execute("SELECT * FROM jobs WHERE owner=? AND chat=? AND instance=? ORDER BY created DESC,id LIMIT 50",
                              (owner, chat, instance)).fetchall()
            return {"jobs": [self._public(row) for row in rows]}

    def control(self, owner: str, chat: int, instance: int, identifier: object, action: object) -> dict:
        if not isinstance(action, str) or action not in {"pause", "resume", "cancel"}:
            raise AppError("invalid_job_control")
        self.recover()
        now = self._now()
        with self.ledger.transaction() as db:
            row = self._owned(db, identifier, owner, chat, instance)
            state = row["state"]
            if action == "resume":
                if state != "paused" or row["slot"]:
                    raise AppError("job_not_resumable")
                if row["kind"] not in self.kinds:
                    raise AppError("job_kind_unavailable")
                state = "queued"
            elif action == "pause":
                if state == "queued":
                    state = "paused"
                elif state == "running":
                    state = "pausing"
            elif state in {"queued", "paused"}:
                state = "cancelled"
            elif state in {"running", "pausing"}:
                state = "cancelling"
            reservation = 0 if state == "cancelled" and row["attempts"] == 0 else row["reserved_bytes"]
            db.execute("UPDATE jobs SET state=?,updated=?,reserved_bytes=? WHERE id=?", (state, now, reservation, row["id"]))
            return self._public(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())
