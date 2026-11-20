from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .adapter import key_fingerprint

MAGIC = b"MICHIFLIN-NEXT-SNAPSHOT-1\0"
LIMIT = 512 * 1024 * 1024


def _key(passphrase: str, salt: bytes) -> bytes:
    if not isinstance(passphrase, str) or len(passphrase) < 24:
        raise ValueError("A private backup passphrase of at least 24 characters is required")
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seal(source: Path, destination: Path, passphrase: str) -> None:
    if source.stat().st_size > LIMIT:
        raise ValueError("Snapshot exceeds the encrypted backup size limit")
    salt, nonce = os.urandom(16), os.urandom(12)
    encryptor = Cipher(algorithms.AES(_key(passphrase, salt)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(MAGIC)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as stream:
            output.write(MAGIC + salt + nonce)
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                output.write(encryptor.update(chunk))
            output.write(encryptor.finalize() + encryptor.tag)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def unseal(source: Path, destination: Path, passphrase: str) -> None:
    size = source.stat().st_size
    header = len(MAGIC) + 28
    if not header + 16 <= size <= LIMIT + header + 16:
        raise ValueError("Invalid encrypted snapshot size")
    created, verified = False, False
    try:
        with source.open("rb") as stream:
            if stream.read(len(MAGIC)) != MAGIC:
                raise ValueError("Unsupported snapshot format")
            salt, nonce = stream.read(16), stream.read(12)
            stream.seek(-16, 2)
            decryptor = Cipher(algorithms.AES(_key(passphrase, salt)), modes.GCM(nonce, stream.read(16))).decryptor()
            decryptor.authenticate_additional_data(MAGIC)
            stream.seek(header)
            remaining = size - header - 16
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(descriptor, "wb") as output:
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("Truncated snapshot")
                    output.write(decryptor.update(chunk))
                    remaining -= len(chunk)
                output.write(decryptor.finalize())
                verified = True
    except (InvalidTag, ValueError):
        raise ValueError("Snapshot authentication failed; nothing was restored") from None
    finally:
        if created and not verified:
            destination.unlink(missing_ok=True)


def create(rpc, account: int, ledger, destination: Path, passphrase: str) -> dict:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=destination.parent) as temporary:
        directory = Path(temporary)
        rpc.export_backup(account, str(directory), passphrase)
        native = list(directory.glob("*.tar"))
        if len(native) != 1 or native[0].stat().st_size > LIMIT - 10_000_000:
            raise ValueError("Native account backup is missing or too large")
        workspace = directory / "workspace.sqlite3"
        with ledger.connection() as source, sqlite3.connect(workspace) as target:
            source.backup(target)
        os.chmod(workspace, 0o600)
        deliveries = {}
        with sqlite3.connect(workspace) as db:
            rows = db.execute("SELECT filename,state FROM outbox WHERE state IN ('queued','sending','uncertain')").fetchall()
        for filename, state in rows:
            if not re.fullmatch(r"[a-f0-9]{32}\.(?:epub|txt|mobi|pdf)", filename):
                raise ValueError("Unexpected staged delivery filename")
            file = ledger.path.parent / "deliveries" / filename
            if file.is_symlink() or (not file.is_file() and state != "uncertain"):
                raise ValueError("A pending delivery file is missing or unsafe")
            if file.is_file():
                deliveries[filename] = _hash(file)
        manifest = {"format": 1, "deliveries": deliveries, "identity": key_fingerprint(rpc.make_vcard(account, [1])),
                    "account_sha256": _hash(native[0]), "workspace_sha256": _hash(workspace)}
        info = directory / "manifest.json"
        info.write_text(json.dumps(manifest))
        archive = directory / "bundle.tar"
        with tarfile.open(archive, "w") as bundle:
            for name, path in (("account.tar", native[0]), ("workspace.sqlite3", workspace), ("manifest.json", info)):
                bundle.add(path, arcname=name, recursive=False)
            for filename in deliveries:
                bundle.add(ledger.path.parent / "deliveries" / filename, arcname="deliveries/" + filename, recursive=False)
        seal(archive, destination, passphrase)
    return {"bytes": destination.stat().st_size, "sha256": _hash(destination)}


def restore(source: Path, destination: Path, passphrase: str) -> dict:
    from deltachat2 import IOTransport, Rpc

    if destination.exists():
        raise ValueError("Restoration requires a new, empty destination")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=destination.parent) as temporary:
        directory = Path(temporary)
        archive = directory / "bundle.tar"
        unseal(source, archive, passphrase)
        with tarfile.open(archive) as bundle:
            items = bundle.getmembers()
            names = {item.name for item in items}
            if (len(items) > 103 or len(names) != len(items)
                    or not {"account.tar", "workspace.sqlite3", "manifest.json"} <= names
                    or sum(item.size for item in items) > LIMIT):
                raise ValueError("Unexpected snapshot entries")
            for item in items:
                allowed = item.name in {"account.tar", "workspace.sqlite3", "manifest.json"} or re.fullmatch(r"deliveries/[a-f0-9]{32}\.(?:epub|txt|mobi|pdf)", item.name)
                if not item.isfile() or not allowed or item.size > LIMIT:
                    raise ValueError("Unsafe snapshot entries")
                path = directory / item.name
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with bundle.extractfile(item) as stream, path.open("xb") as target:
                    shutil.copyfileobj(stream, target, length=1024 * 1024)
                path.chmod(0o600)
        manifest = json.loads((directory / "manifest.json").read_text())
        if (manifest.get("format") != 1 or _hash(directory / "account.tar") != manifest.get("account_sha256")
                or _hash(directory / "workspace.sqlite3") != manifest.get("workspace_sha256")):
            raise ValueError("Snapshot contents failed verification")
        deliveries = manifest.get("deliveries", {})
        if not isinstance(deliveries, dict) or {"deliveries/" + name for name in deliveries} != names - {"account.tar", "workspace.sqlite3", "manifest.json"}:
            raise ValueError("Delivery inventory does not match the snapshot")
        for filename, digest in deliveries.items():
            if _hash(directory / "deliveries" / filename) != digest:
                raise ValueError("A delivery failed its snapshot checksum")
        with sqlite3.connect(directory / "workspace.sqlite3") as workspace:
            if workspace.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Workspace database integrity check failed")
        staged = directory / "runtime"
        staged.mkdir(mode=0o700)
        with IOTransport(accounts_dir=str(staged / "accounts")) as transport:
            rpc = Rpc(transport)
            account = rpc.add_account()
            rpc.import_backup(account, str(directory / "account.tar"), passphrase)
            if not rpc.is_configured(account) or key_fingerprint(rpc.make_vcard(account, [1])) != manifest.get("identity"):
                raise ValueError("Restored native identity does not match the snapshot")
        state = staged / "state"
        state.mkdir(mode=0o700)
        shutil.copy2(directory / "workspace.sqlite3", state / "workspace.sqlite3")
        if deliveries:
            shutil.copytree(directory / "deliveries", state / "deliveries")
        staged.rename(destination)
    return {"identity_verified": True, "workspace_verified": True, "io_started": False}
