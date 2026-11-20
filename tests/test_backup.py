from __future__ import annotations

import hashlib
import shutil
import sqlite3

import pytest

pytest.importorskip("cryptography")
from michiflin.backup import create, restore, seal, unseal
from michiflin.storage import Ledger

PASSPHRASE = "synthetic-snapshot-passphrase-not-production"


def test_streaming_snapshot_encryption_authentication_and_private_permissions(tmp_path):
    source, encrypted, restored = (tmp_path / name for name in ("source", "encrypted", "restored"))
    source.write_bytes(b"Private synthetic content" * 100000)
    seal(source, encrypted, PASSPHRASE)
    assert encrypted.stat().st_mode & 0o777 == 0o600
    assert b"Private synthetic content" not in encrypted.read_bytes()
    unseal(encrypted, restored, PASSPHRASE)
    assert hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(restored.read_bytes()).digest()
    with pytest.raises(ValueError, match="authentication failed"):
        unseal(encrypted, tmp_path / "wrong-key", "a-different-synthetic-passphrase")
    assert not (tmp_path / "wrong-key").exists()
    modified = bytearray(encrypted.read_bytes())
    modified[-1] ^= 1
    encrypted.write_bytes(modified)
    with pytest.raises(ValueError):
        unseal(encrypted, tmp_path / "corrupt", PASSPHRASE)
    assert not (tmp_path / "corrupt").exists()


def test_snapshot_failure_never_overwrites_or_deletes_an_existing_file(tmp_path):
    source, existing = tmp_path / "invalid", tmp_path / "existing"
    source.write_bytes(b"invalid" * 100)
    existing.write_bytes(b"preserve this file")
    with pytest.raises(ValueError):
        unseal(source, existing, PASSPHRASE)
    assert existing.read_bytes() == b"preserve this file"


def test_native_snapshot_restores_account_ledger_and_pending_delivery_without_io(tmp_path):
    core = pytest.importorskip("deltachat2")
    if not shutil.which("deltachat-rpc-server"):
        pytest.skip("Native core is not installed")
    ledger = Ledger(tmp_path / "state")
    ledger.bind(42, "synthetic-owner", 20)
    ledger.add_note("synthetic-owner", "Preserve this note")
    deliveries = ledger.path.parent / "deliveries"
    deliveries.mkdir()
    receipt = "c" * 32
    payload = deliveries / (receipt + ".txt")
    payload.write_text("Synthetic public-domain ebook")
    ledger.queue_delivery(receipt, "synthetic-owner", 20, 42, payload.name, "Book", payload.stat().st_size)
    encrypted = tmp_path / "next.snapshot"
    with core.IOTransport(accounts_dir=str(tmp_path / "original-accounts")) as transport:
        rpc = core.Rpc(transport)
        account = rpc.add_account()
        for key, value in (("addr", "synthetic@example.invalid"), ("configured_addr", "synthetic@example.invalid"), ("configured", "1")):
            rpc.set_config(account, key, value)
        rpc.get_chat_securejoin_qr_code(account, None)
        metadata = create(rpc, account, ledger, encrypted, PASSPHRASE)
        assert metadata["bytes"] == encrypted.stat().st_size
    destination = tmp_path / "restored"
    assert restore(encrypted, destination, PASSPHRASE) == {"identity_verified": True, "workspace_verified": True, "io_started": False}
    with sqlite3.connect(destination / "state" / "workspace.sqlite3") as db:
        assert db.execute("SELECT text FROM notes").fetchone()[0] == "Preserve this note"
        assert db.execute("SELECT state FROM outbox").fetchone()[0] == "queued"
    assert (destination / "state" / "deliveries" / payload.name).read_bytes() == payload.read_bytes()
    with pytest.raises(ValueError, match="new, empty"):
        restore(encrypted, destination, PASSPHRASE)


def test_snapshot_cli_refuses_an_active_runtime_lock(tmp_path, monkeypatch, capsys):
    import fcntl
    import sys

    from michiflin.cli import main

    pytest.importorskip("deltachat2")
    state, accounts = tmp_path / "state", tmp_path / "accounts"
    Ledger(state)
    accounts.mkdir()
    (accounts / "accounts.toml").write_text("synthetic-placeholder")
    key = tmp_path / "backup.key"
    key.write_text(PASSPHRASE)
    key.chmod(0o600)
    monkeypatch.setattr(sys, "argv", ["michiflin", "snapshot", "--accounts", str(accounts), "--state", str(state),
                                    "--output", str(tmp_path / "snapshot"), "--passphrase-file", str(key)])
    with (state / "runtime.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SystemExit):
            main()
    assert "Stop the runtime" in capsys.readouterr().err
    assert not (tmp_path / "snapshot").exists()
