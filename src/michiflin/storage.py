from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .errors import AppError


class Ledger:
    def __init__(self, directory: Path) -> None:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / "workspace.sqlite3"
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS instances (
                    id INTEGER PRIMARY KEY, owner TEXT NOT NULL, chat INTEGER NOT NULL,
                    created REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS requests (
                    instance INTEGER NOT NULL, id TEXT NOT NULL, owner TEXT NOT NULL,
                    digest TEXT NOT NULL, operation TEXT NOT NULL, state TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL, response TEXT,
                    PRIMARY KEY (instance,id)
                );
                CREATE INDEX IF NOT EXISTS requests_owner_time ON requests(owner,created);
                CREATE INDEX IF NOT EXISTS requests_created ON requests(created);
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL,
                    text TEXT NOT NULL, created REAL NOT NULL
                );
            """)
            db.executescript("""
                CREATE TABLE IF NOT EXISTS outbox (
                    receipt TEXT PRIMARY KEY, owner TEXT NOT NULL, chat INTEGER NOT NULL,
                    instance INTEGER NOT NULL, filename TEXT NOT NULL, title TEXT NOT NULL,
                    bytes INTEGER NOT NULL, state TEXT NOT NULL, created REAL NOT NULL,
                    message_id INTEGER
                );
                CREATE TABLE IF NOT EXISTS cursors(instance INTEGER PRIMARY KEY, serial INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS deliveries(owner TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS deliveries_owner_time ON deliveries(owner,created);
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.rollback()
                raise
            else:
                db.commit()

    def bind(self, instance: int, owner: str, chat: int) -> None:
        if type(instance) is not int or instance < 1 or type(chat) is not int or chat < 1:
            raise AppError("invalid_instance")
        with self.transaction() as db:
            old = db.execute("SELECT * FROM instances WHERE id=?", (instance,)).fetchone()
            if old and (old["owner"] != owner or old["chat"] != chat or old["revoked"]):
                raise AppError("instance_binding_conflict")
            db.execute("INSERT OR IGNORE INTO instances(id,owner,chat,created) VALUES(?,?,?,?)",
                       (instance, owner, chat, time.time()))

    def authorize(self, instance: int, owner: str, chat: int) -> None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM instances WHERE id=?", (instance,)).fetchone()
        if not row or row["owner"] != owner or row["chat"] != chat or row["revoked"]:
            raise AppError("instance_not_authorized")

    def reserve(self, instance: int, identifier: str, owner: str, operation: str,
                args: dict, daily_limit: int, pending_limit: int, global_limit: int = 1000) -> dict | None:
        digest = hashlib.sha256(json.dumps([operation, args], sort_keys=True, ensure_ascii=True,
                                           separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        now = time.time()
        midnight = int(now // 86400) * 86400
        with self.transaction() as db:
            old = db.execute("SELECT * FROM requests WHERE instance=? AND id=?", (instance, identifier)).fetchone()
            if old:
                if old["owner"] != owner or old["digest"] != digest:
                    raise AppError("request_identity_conflict")
                if old["state"] == "uncertain":
                    raise AppError("previous_outcome_uncertain")
                if old["response"]:
                    return json.loads(old["response"])
                raise AppError("request_pending")
            pending = db.execute("SELECT COUNT(*) FROM requests WHERE owner=? AND state='running'", (owner,)).fetchone()[0]
            if pending >= pending_limit:
                raise AppError("pending_limit")
            count = db.execute("SELECT COUNT(*) FROM requests WHERE owner=? AND created>=?", (owner, midnight)).fetchone()[0]
            if daily_limit and count >= daily_limit:
                raise AppError("daily_limit")
            global_count = db.execute("SELECT COUNT(*) FROM requests WHERE created>=?", (midnight,)).fetchone()[0]
            if global_count >= global_limit:
                raise AppError("service_daily_limit")
            db.execute("INSERT INTO requests VALUES(?,?,?,?,?,'running',?,?,NULL)",
                       (instance, identifier, owner, digest, operation, now, now))
        return None

    def settle(self, instance: int, identifier: str, response: dict) -> None:
        encoded = json.dumps(response, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode()) > 1_000_000:
            raise AppError("result_too_large")
        with self.transaction() as db:
            changed = db.execute("UPDATE requests SET state=?,response=?,updated=? "
                                 "WHERE instance=? AND id=? AND state='running'",
                                 ("done" if response.get("ok") else "failed", encoded, time.time(), instance, identifier))
            if changed.rowcount != 1:
                raise AppError("request_checkpoint_failed")

    def complete_note(self, instance: int, identifier: str, owner: str, operation: str, args: dict) -> dict:
        digest = hashlib.sha256(json.dumps([operation, args], sort_keys=True, ensure_ascii=True,
                                           separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        with self.transaction() as db:
            request = db.execute("SELECT * FROM requests WHERE instance=? AND id=?", (instance, identifier)).fetchone()
            if (not request or request["owner"] != owner or request["digest"] != digest
                    or request["state"] != "running" or operation not in {"notes.add", "notes.delete"}):
                raise AppError("request_checkpoint_failed")
            if operation == "notes.add":
                text = args.get("text")
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= 6000:
                    raise AppError("invalid_note")
                if db.execute("SELECT COUNT(*) FROM notes WHERE owner=?", (owner,)).fetchone()[0] >= 200:
                    raise AppError("notes_limit")
                row = db.execute("INSERT INTO notes(owner,text,created) VALUES(?,?,?)", (owner, text.strip(), time.time()))
                data = {"id": int(row.lastrowid)}
            else:
                note = args.get("id")
                if type(note) is not int or note < 1:
                    raise AppError("invalid_note")
                data = {"deleted": db.execute("DELETE FROM notes WHERE id=? AND owner=?", (note, owner)).rowcount == 1}
            response = {"id": identifier, "ok": True, "data": data}
            db.execute("UPDATE requests SET state='done',response=?,updated=? WHERE instance=? AND id=?",
                       (json.dumps(response, allow_nan=False), time.time(), instance, identifier))
        return response

    def notes(self, owner: str) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT id,text,created FROM notes WHERE owner=? ORDER BY id DESC LIMIT 200", (owner,))]

    def add_note(self, owner: str, text: str) -> int:
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 6000:
            raise AppError("invalid_note")
        with self.transaction() as db:
            if db.execute("SELECT COUNT(*) FROM notes WHERE owner=?", (owner,)).fetchone()[0] >= 200:
                raise AppError("notes_limit")
            row = db.execute("INSERT INTO notes(owner,text,created) VALUES(?,?,?)", (owner, text.strip(), time.time()))
            return int(row.lastrowid)

    def delete_note(self, owner: str, identifier: int) -> bool:
        if type(identifier) is not int or identifier < 1:
            raise AppError("invalid_note")
        with self.transaction() as db:
            return db.execute("DELETE FROM notes WHERE id=? AND owner=?", (identifier, owner)).rowcount == 1

    def recover_interrupted(self) -> None:
        """Call once after acquiring the exclusive runtime lock, before serving."""
        with self.transaction() as db:
            db.execute("UPDATE requests SET state='uncertain',updated=? WHERE state='running'", (time.time(),))
            db.execute("UPDATE outbox SET state='uncertain' WHERE state='sending'")
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
                db.execute("UPDATE jobs SET state='uncertain',updated=?,error='job_outcome_uncertain' "
                           "WHERE state IN ('running','pausing','cancelling')", (time.time(),))

    def has_instance(self, instance: int) -> bool:
        with self.connection() as db:
            return db.execute("SELECT 1 FROM instances WHERE id=? AND revoked=0", (instance,)).fetchone() is not None

    def cursor(self, instance: int) -> int:
        with self.connection() as db:
            row = db.execute("SELECT serial FROM cursors WHERE instance=?", (instance,)).fetchone()
            return row[0] if row else 0

    def checkpoint(self, instance: int, serial: int) -> None:
        if type(serial) is not int or serial < 1:
            raise AppError("invalid_update_batch")
        with self.transaction() as db:
            db.execute("INSERT INTO cursors VALUES(?,?) ON CONFLICT(instance) "
                       "DO UPDATE SET serial=MAX(serial,excluded.serial)", (instance, serial))

    def allow_delivery(self, owner: str) -> bool:
        now = time.time()
        with self.transaction() as db:
            db.execute("DELETE FROM deliveries WHERE created<?", (now - 86400,))
            rows = db.execute("SELECT created FROM deliveries WHERE owner=?", (owner,)).fetchall()
            if len(rows) >= 5 or any(row[0] > now - 60 for row in rows):
                return False
            db.execute("INSERT INTO deliveries VALUES(?,?)", (owner, now))
            return True

    def revoke(self, instance: int) -> None:
        with self.transaction() as db:
            db.execute("UPDATE instances SET revoked=1 WHERE id=?", (instance,))

    def _check_delivery_capacity(self, db, owner: str) -> None:
        since = time.time() - 86400
        own = db.execute("SELECT COUNT(*) FROM outbox WHERE owner=? AND created>?", (owner, since)).fetchone()[0]
        total = db.execute("SELECT COUNT(*) FROM outbox WHERE created>?", (since,)).fetchone()[0]
        pending = db.execute("SELECT COUNT(*) FROM outbox WHERE state IN ('queued','sending')").fetchone()[0]
        if own >= 8 or total >= 100 or pending >= 20:
            raise AppError("delivery_limit")

    def check_delivery_capacity(self, owner: str) -> None:
        with self.connection() as db:
            self._check_delivery_capacity(db, owner)

    def queue_delivery(self, receipt: str, owner: str, chat: int, instance: int,
                       filename: str, title: str, size: int) -> None:
        with self.transaction() as db:
            self._check_delivery_capacity(db, owner)
            db.execute("INSERT INTO outbox VALUES(?,?,?,?,?,?,?,'queued',?,NULL)",
                       (receipt, owner, chat, instance, filename, title, size, time.time()))

    def claim_delivery(self, receipt: str, owner: str) -> dict | None:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM outbox WHERE receipt=? AND owner=? AND state='queued'",
                             (receipt, owner)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE outbox SET state='sending' WHERE receipt=?", (receipt,))
            return dict(row)

    def delivery_status(self, receipt: object, owner: str) -> dict:
        if not isinstance(receipt, str) or len(receipt) != 32:
            raise AppError("invalid_delivery")
        with self.connection() as db:
            row = db.execute("SELECT receipt,state AS status,title,bytes,message_id FROM outbox WHERE receipt=? AND owner=?",
                             (receipt, owner)).fetchone()
            if row is None:
                raise AppError("invalid_delivery")
            return dict(row)

    def finish_delivery(self, receipt: str, message_id: int | None) -> None:
        with self.transaction() as db:
            db.execute("UPDATE outbox SET state=?,message_id=? WHERE receipt=? AND state='sending'",
                       ("sent" if message_id else "uncertain", message_id, receipt))

    def queued_deliveries(self) -> list[dict]:
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM outbox WHERE state='queued' ORDER BY created LIMIT 20")]

    def abandon_delivery(self, receipt: str) -> None:
        with self.transaction() as db:
            db.execute("UPDATE outbox SET state='uncertain' WHERE receipt=? AND state='queued'", (receipt,))
