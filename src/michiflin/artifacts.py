from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from .errors import AppError
from .parts import _directory, _file, _publish, _same
from .storage import Ledger

MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_FILES = 64
CHUNK = 65536


def identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise AppError("invalid_artifact")
    return value


def filename(value: object) -> str:
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)
            or "/" in value or "\\" in value or len(value.encode()) > 240):
        raise AppError("invalid_artifact_name")
    return value


def _sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


@dataclass(frozen=True)
class Limits:
    file_bytes: int = MAX_FILE
    owner_bytes: int = 32 * 1024 * 1024
    total_bytes: int = MAX_TOTAL
    owner_files: int = 16
    total_files: int = MAX_FILES
    owner_records: int = 256
    total_records: int = 4096

    def __post_init__(self):
        for name, maximum in (("file_bytes", MAX_FILE), ("owner_bytes", MAX_TOTAL),
                              ("total_bytes", MAX_TOTAL), ("owner_files", MAX_FILES), ("total_files", MAX_FILES),
                              ("owner_records", 4096), ("total_records", 65536)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("Invalid artifact limits")
        if (not self.file_bytes <= self.owner_bytes <= self.total_bytes
                or self.owner_files > self.total_files or self.owner_records > self.total_records):
            raise ValueError("Invalid artifact limit ordering")


class ArtifactStore:
    """Identity-bound immutable server copies. Native/worker source paths are trusted inputs, never API args."""

    def __init__(self, ledger: Ledger, *, limits: Limits | None = None) -> None:
        self.ledger, self.limits = ledger, limits or Limits()
        self.directory = ledger.path.parent / "artifacts"
        self.directory.mkdir(mode=0o700, exist_ok=True)
        with self._vault():
            pass
        with ledger.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, chat INTEGER NOT NULL, instance INTEGER NOT NULL,
                    request_id TEXT NOT NULL, digest TEXT NOT NULL, name TEXT NOT NULL,
                    bytes INTEGER NOT NULL CHECK(bytes>=0), sha256 TEXT NOT NULL,
                    reserved INTEGER NOT NULL CHECK(reserved>=0),
                    state TEXT NOT NULL CHECK(state IN ('importing','ready','deleting','deleted','failed','uncertain')),
                    readers INTEGER NOT NULL DEFAULT 0 CHECK(readers>=0), created REAL NOT NULL,
                    updated REAL NOT NULL, error TEXT, UNIQUE(instance,request_id)
                );
                CREATE INDEX IF NOT EXISTS artifacts_owner ON artifacts(owner,instance,created);
                CREATE TABLE IF NOT EXISTS artifact_resolutions (
                    serial INTEGER PRIMARY KEY, artifact TEXT NOT NULL, resolved REAL NOT NULL,
                    outcome TEXT NOT NULL, evidence_sha256 TEXT NOT NULL
                );
            """)

    @contextmanager
    def _vault(self):
        with _directory(self.directory) as directory:
            info = os.fstat(directory)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise AppError("artifact_storage_unsafe")
            yield directory

    @staticmethod
    def _binding(db, owner, chat, instance):
        row = db.execute("SELECT * FROM instances WHERE id=?", (instance,)).fetchone()
        if not row or row["owner"] != owner or row["chat"] != chat or row["revoked"]:
            raise AppError("instance_not_authorized")

    def _owned(self, db, owner, chat, instance, artifact):
        self._binding(db, owner, chat, instance)
        identifier(artifact)
        row = db.execute("SELECT * FROM artifacts WHERE id=? AND owner=? AND chat=? AND instance=?",
                         (artifact, owner, chat, instance)).fetchone()
        if row is None:
            raise AppError("artifact_not_found")
        return row

    @staticmethod
    def _public(row):
        return {key: row[key] for key in ("id", "name", "bytes", "sha256", "state", "created", "updated", "error")}

    def _capacity(self, db, owner, size):
        for clause, values, records, files, budget in (
            (" WHERE owner=?", (owner,), self.limits.owner_records, self.limits.owner_files, self.limits.owner_bytes),
            ("", (), self.limits.total_records, self.limits.total_files, self.limits.total_bytes),
        ):
            row = db.execute("SELECT COUNT(*),COALESCE(SUM(reserved),0),"
                             "COALESCE(SUM(state NOT IN ('deleted','failed')),0) FROM artifacts" + clause, values).fetchone()
            if row[0] >= records:
                raise AppError("artifact_history_limit")
            if row[1] + size > budget or row[2] >= files:
                raise AppError("artifact_storage_limit")
        Ledger.check_snapshot_capacity(db)

    def _admit(self, owner, chat, instance, request_id, name, size, digest):
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{12,80}", request_id):
            raise AppError("invalid_request_id")
        fingerprint = hashlib.sha256(json.dumps([name, size, digest], ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
        with self.ledger.transaction() as db:
            self._binding(db, owner, chat, instance)
            old = db.execute("SELECT * FROM artifacts WHERE instance=? AND request_id=?", (instance, request_id)).fetchone()
            if old:
                if old["owner"] != owner or old["chat"] != chat or old["digest"] != fingerprint:
                    raise AppError("request_identity_conflict")
                return old, False
            self._capacity(db, owner, size)
            artifact, now = secrets.token_hex(16), time.time()
            db.execute("""INSERT INTO artifacts(id,owner,chat,instance,request_id,digest,name,bytes,sha256,
                reserved,state,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,'importing',?,?)""",
                       (artifact, owner, chat, instance, request_id, fingerprint, name, size, digest, size, now, now))
            return db.execute("SELECT * FROM artifacts WHERE id=?", (artifact,)).fetchone(), True

    def put(self, owner: str, chat: int, instance: int, request_id: str, name: str, body: bytes) -> dict:
        filename(name)
        if not isinstance(body, bytes) or len(body) > self.limits.file_bytes:
            raise AppError("artifact_file_limit")
        return self._copy(owner, chat, instance, request_id, name, io.BytesIO(body), len(body), hashlib.sha256(body).hexdigest())

    def import_file(self, owner: str, chat: int, instance: int, request_id: str, name: str, source: Path) -> dict:
        """Trusted ingestion only. Never expose `source` as a member-supplied filesystem path."""
        filename(name)
        source = Path(source)
        self.ledger.authorize(instance, owner, chat)
        with _directory(source.parent) as parent, _file(parent, source.name, maximum=self.limits.file_bytes) as (stream, before):
            digest = hashlib.sha256()
            remaining = before.st_size
            deadline = time.monotonic() + 15
            while remaining:
                body = stream.read(min(CHUNK, remaining))
                if not body or time.monotonic() > deadline:
                    raise AppError("artifact_source_changed")
                digest.update(body)
                remaining -= len(body)
            if stream.read(1):
                raise AppError("artifact_source_changed")
            _same(before, os.fstat(stream.fileno()))
            stream.seek(0)
            return self._copy(owner, chat, instance, request_id, name, stream, before.st_size, digest.hexdigest())

    def _copy(self, owner, chat, instance, request_id, name, stream, size, expected):
        row, fresh = self._admit(owner, chat, instance, request_id, name, size, expected)
        if not fresh:
            return self._public(row)
        artifact, published = row["id"], False
        temporary, final = artifact + ".partial", artifact + ".blob"
        with self._vault() as directory:
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                with os.fdopen(fd, "wb") as target:
                    digest, remaining, deadline = hashlib.sha256(), size, time.monotonic() + 15
                    while remaining:
                        body = stream.read(min(CHUNK, remaining))
                        if not body or time.monotonic() > deadline:
                            raise AppError("artifact_source_changed")
                        remaining -= len(body)
                        digest.update(body)
                        target.write(body)
                    if stream.read(1) or digest.hexdigest() != expected:
                        raise AppError("artifact_source_changed")
                    target.flush()
                    os.fsync(target.fileno())
                with self.ledger.transaction() as db:
                    self._binding(db, owner, chat, instance)
                    current = db.execute("SELECT state FROM artifacts WHERE id=?", (artifact,)).fetchone()
                    if current[0] != "importing":
                        raise AppError("artifact_outcome_uncertain")
                    _publish(directory, temporary, final)
                    published = True
                    os.fsync(directory)
                    db.execute("UPDATE artifacts SET state='ready',updated=? WHERE id=?", (time.time(), artifact))
                    result = self._public(db.execute("SELECT * FROM artifacts WHERE id=?", (artifact,)).fetchone())
                return result
            except BaseException:
                # An interruption can arrive after rename succeeds but before the Python flag changes.
                if not published:
                    try:
                        os.stat(final, dir_fd=directory, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        published = True
                if not published:
                    with suppress(FileNotFoundError):
                        os.unlink(temporary, dir_fd=directory)
                with self.ledger.transaction() as db:
                    db.execute("UPDATE artifacts SET state=?,reserved=?,updated=?,error=? WHERE id=? AND state='importing'",
                               ("uncertain" if published else "failed", size if published else 0, time.time(),
                                "artifact_outcome_uncertain" if published else "artifact_import_failed", artifact))
                raise

    def get(self, owner, chat, instance, artifact):
        with self.ledger.connection() as db:
            return self._public(self._owned(db, owner, chat, instance, artifact))

    def list(self, owner, chat, instance):
        with self.ledger.connection() as db:
            self._binding(db, owner, chat, instance)
            rows = db.execute("SELECT * FROM artifacts WHERE owner=? AND chat=? AND instance=? AND state!='deleted' "
                              "ORDER BY (state='failed'),created DESC,rowid DESC LIMIT 50", (owner, chat, instance)).fetchall()
            return {"files": [self._public(row) for row in rows]}

    @contextmanager
    def open(self, owner, chat, instance, artifact):
        with self.ledger.transaction() as db:
            row = self._owned(db, owner, chat, instance, artifact)
            if row["state"] != "ready":
                raise AppError("artifact_not_ready")
            db.execute("UPDATE artifacts SET readers=readers+1 WHERE id=?", (artifact,))
        try:
            with self._vault() as directory, _file(directory, artifact + ".blob", maximum=row["bytes"]) as (stream, before):
                if before.st_size != row["bytes"]:
                    raise AppError("artifact_hash_mismatch")
                digest, remaining = hashlib.sha256(), row["bytes"]
                while remaining:
                    body = stream.read(min(CHUNK, remaining))
                    if not body:
                        raise AppError("artifact_hash_mismatch")
                    digest.update(body)
                    remaining -= len(body)
                if stream.read(1) or digest.hexdigest() != row["sha256"]:
                    raise AppError("artifact_hash_mismatch")
                _same(before, os.fstat(stream.fileno()))
                stream.seek(0)
                yield stream
        finally:
            with self.ledger.transaction() as db:
                db.execute("UPDATE artifacts SET readers=readers-1 WHERE id=? AND readers>0", (artifact,))

    def delete(self, owner, chat, instance, artifact):
        with self.ledger.transaction() as db:
            row = self._owned(db, owner, chat, instance, artifact)
            if row["state"] == "deleted":
                return {"id": artifact, "deleted": True}
            if row["state"] != "ready" or row["readers"]:
                raise AppError("artifact_busy")
            db.execute("UPDATE artifacts SET state='deleting',updated=? WHERE id=?", (time.time(), artifact))
        try:
            with self._vault() as directory:
                with suppress(FileNotFoundError):
                    os.unlink(artifact + ".blob", dir_fd=directory)
                os.fsync(directory)
            with self.ledger.transaction() as db:
                db.execute("UPDATE artifacts SET state='deleted',reserved=0,updated=? WHERE id=? AND state='deleting'",
                           (time.time(), artifact))
        except BaseException:
            with self.ledger.transaction() as db:
                db.execute("UPDATE artifacts SET state='uncertain',updated=?,error='artifact_outcome_uncertain' "
                           "WHERE id=? AND state='deleting'", (time.time(), artifact))
            raise
        return {"id": artifact, "deleted": True}

    def reconcile(self, artifact, *, outcome, evidence_sha256):
        """Operator-only after verified supervisor/worker exit; no automatic replay or permission restoration."""
        identifier(artifact)
        if not isinstance(outcome, str) or outcome not in {"ready", "deleted"} or not _sha(evidence_sha256):
            raise AppError("invalid_artifact_resolution")
        with self.ledger.transaction() as db:
            row = db.execute("SELECT * FROM artifacts WHERE id=?", (artifact,)).fetchone()
            if row is None or row["state"] != "uncertain":
                raise AppError("artifact_not_uncertain")
            with self._vault() as directory:
                if outcome == "ready":
                    with _file(directory, artifact + ".blob", maximum=row["bytes"]) as (stream, before):
                        digest, size = hashlib.sha256(), 0
                        for body in iter(lambda: stream.read(CHUNK), b""):
                            size += len(body)
                            if size > row["bytes"]:
                                raise AppError("artifact_hash_mismatch")
                            digest.update(body)
                        _same(before, os.fstat(stream.fileno()))
                        if before.st_size != row["bytes"] or digest.hexdigest() != row["sha256"]:
                            raise AppError("artifact_hash_mismatch")
                for suffix in ((".partial",) if outcome == "ready" else (".partial", ".blob")):
                    with suppress(FileNotFoundError):
                        os.unlink(artifact + suffix, dir_fd=directory)
                os.fsync(directory)
            db.execute("INSERT INTO artifact_resolutions(artifact,resolved,outcome,evidence_sha256) VALUES(?,?,?,?)",
                       (artifact, time.time(), outcome, evidence_sha256))
            db.execute("UPDATE artifacts SET state=?,readers=0,reserved=?,error=NULL,updated=? WHERE id=?",
                       (outcome, row["bytes"] if outcome == "ready" else 0, time.time(), artifact))


def _snapshot_rows(workspace: Path) -> dict:
    import sqlite3

    try:
        with sqlite3.connect(workspace) as db:
            db.row_factory = sqlite3.Row
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifacts'").fetchone():
                return {}
            if db.execute("SELECT 1 FROM artifacts WHERE state IN ('importing','deleting') LIMIT 1").fetchone():
                raise ValueError("Stop artifact imports/deletions before creating a snapshot")
            rows = db.execute("SELECT * FROM artifacts WHERE state IN ('ready','uncertain')").fetchmany(MAX_FILES + 1)
        if len(rows) > MAX_FILES:
            raise ValueError("Artifact snapshot inventory exceeds its count limit")
        result, total = {}, 0
        for row in rows:
            identifier(row['id'])
            if (type(row['bytes']) is not int or not 0 <= row['bytes'] <= MAX_FILE
                    or row['reserved'] != row['bytes'] or not _sha(row['sha256'])):
                raise ValueError("Invalid artifact snapshot metadata")
            total += row['bytes']
            result[row['id'] + '.blob'] = dict(row)
        if total > MAX_TOTAL:
            raise ValueError("Artifact snapshot exceeds its byte budget")
        return result
    except (sqlite3.Error, AppError) as error:
        raise ValueError("Artifact snapshot metadata could not be verified") from error


def snapshot_files(workspace: Path, state: Path, destination: Path) -> dict:
    """Copy only authenticated-inventory candidates while the operator owns the runtime lock."""
    rows = _snapshot_rows(workspace)
    if not rows:
        return {}
    destination.mkdir(mode=0o700)
    inventory = {}
    with _directory(state / 'artifacts') as directory:
        for name, row in rows.items():
            try:
                with _file(directory, name, maximum=row['bytes']) as (stream, before):
                    if before.st_size != row['bytes']:
                        raise ValueError('Artifact snapshot size mismatch')
                    descriptor = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    digest, remaining = hashlib.sha256(), row['bytes']
                    with os.fdopen(descriptor, 'wb') as target:
                        while remaining:
                            body = stream.read(min(CHUNK, remaining))
                            if not body:
                                raise ValueError('Artifact snapshot was truncated')
                            digest.update(body)
                            target.write(body)
                            remaining -= len(body)
                        if stream.read(1) or digest.hexdigest() != row['sha256']:
                            raise ValueError('Artifact snapshot checksum mismatch')
                        _same(before, os.fstat(stream.fileno()))
                inventory[name] = {'bytes': row['bytes'], 'sha256': row['sha256']}
            except FileNotFoundError:
                if row['state'] != 'uncertain':
                    raise ValueError('A ready artifact is missing') from None
    return inventory


def verify_snapshot_files(workspace: Path, directory: Path, inventory: object) -> None:
    """Must complete before native identity import. Older snapshots without this table stay supported."""
    if not isinstance(inventory, dict) or len(inventory) > MAX_FILES:
        raise ValueError('Invalid artifact snapshot inventory')
    rows = _snapshot_rows(workspace)
    required = {name for name, row in rows.items() if row['state'] == 'ready'}
    if not required <= inventory.keys() or not inventory.keys() <= rows.keys():
        raise ValueError('Artifact snapshot inventory does not match its database')
    for name, metadata in inventory.items():
        row = rows[name]
        if (not isinstance(metadata, dict) or set(metadata) != {'bytes', 'sha256'}
                or type(metadata['bytes']) is not int or metadata['bytes'] != row['bytes']
                or metadata['sha256'] != row['sha256']):
            raise ValueError('Artifact snapshot metadata mismatch')
        digest, size = hashlib.sha256(), 0
        with _directory(directory) as parent, _file(parent, name, maximum=row['bytes']) as (stream, before):
            for body in iter(lambda: stream.read(CHUNK), b''):
                size += len(body)
                if size > row['bytes']:
                    raise ValueError('Artifact snapshot exceeds its declared size')
                digest.update(body)
            _same(before, os.fstat(stream.fileno()))
        if size != row['bytes'] or digest.hexdigest() != row['sha256']:
            raise ValueError('Artifact snapshot checksum mismatch')
