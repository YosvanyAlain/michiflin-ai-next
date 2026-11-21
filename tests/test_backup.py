from __future__ import annotations

import hashlib
import shutil
import sqlite3

import pytest

pytest.importorskip("cryptography")
from michiflin.backup import create, restore, seal, unseal
from michiflin.errors import AppError
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


@pytest.mark.parametrize("include_catalog", [False, True])
def test_native_snapshot_restores_account_ledger_and_pending_delivery_without_io(tmp_path, monkeypatch, include_catalog):
    core = pytest.importorskip("deltachat2")
    if not shutil.which("deltachat-rpc-server"):
        pytest.skip("Native core is not installed")
    ledger = Ledger(tmp_path / "state")
    ledger.bind(42, "synthetic-owner", 20)
    ledger.add_note("synthetic-owner", "Preserve this note")
    from michiflin.jobs import JobQueue, Kind

    jobs = JobQueue(ledger, kinds={"synthetic.v1": Kind(lambda _payload: None)})
    completed = jobs.enqueue("synthetic-owner", 20, 42, "synthetic-job-completed", "synthetic.v1", {})
    done_claim = jobs.claim()
    jobs.finish(done_claim, result={"receipt": "confirmed-synthetic-result"})
    jobs.worker_stopped(done_claim)
    interrupted = jobs.enqueue("synthetic-owner", 20, 42, "synthetic-job-interrupted", "synthetic.v1", {})
    interrupted_claim = jobs.claim()
    jobs.heartbeat(interrupted_claim, current=1, total=2, checkpoint={"part": 1})
    queued = jobs.enqueue("synthetic-owner", 20, 42, "synthetic-job-queued", "synthetic.v1", {})
    deliveries = ledger.path.parent / "deliveries"
    deliveries.mkdir()
    receipt = "c" * 32
    payload = deliveries / (receipt + ".txt")
    payload.write_text("Synthetic public-domain ebook")
    ledger.queue_delivery(receipt, "synthetic-owner", 20, 42, payload.name, "Book", payload.stat().st_size)
    if include_catalog:
        import gzip
        import os
        import time

        from test_gutenberg import CSV

        cached = ledger.path.parent / "catalog" / "catalog.csv.gz"
        cached.parent.mkdir()
        cached.write_bytes(gzip.compress(CSV.encode()))
        fetched_at = int(time.time()) - 3600
        os.utime(cached, (fetched_at, fetched_at))
        from michiflin.artifacts import ArtifactStore

        store = ArtifactStore(ledger)
        artifact = store.put("synthetic-owner", 20, 42, "synthetic-artifact-ready", "private-copy.txt", b"Private synthetic file")
        missing = store.put("synthetic-owner", 20, 42, "synthetic-artifact-uncertain", "uncertain.txt", b"Uncertain synthetic file")
        import tempfile

        worker_body = b'Synthetic private worker output\x00\xff' * 1000
        with tempfile.TemporaryFile() as output:
            output.write(worker_body)
            output.flush()
            worker_file = store.import_output('synthetic-owner', 20, 42, 'synthetic-worker-output', 'result.bin', output,
                                              {'bytes': len(worker_body), 'sha256': hashlib.sha256(worker_body).hexdigest()})
        (store.directory / (missing["id"] + ".blob")).unlink()
        with ledger.transaction() as db:
            db.execute("UPDATE artifacts SET state='uncertain' WHERE id=?", (missing["id"],))
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
    restored_ledger = Ledger(destination / "state")
    restored_jobs = JobQueue(restored_ledger, kinds=jobs.kinds)
    restored_ledger.recover_interrupted()
    assert restored_jobs.get("synthetic-owner", 20, 42, completed["id"])["result"] == {"receipt": "confirmed-synthetic-result"}
    assert restored_jobs.get("synthetic-owner", 20, 42, interrupted["id"])["state"] == "uncertain"
    assert not restored_jobs.get("synthetic-owner", 20, 42, interrupted["id"])["worker_stopped"]
    assert restored_jobs.get("synthetic-owner", 20, 42, queued["id"])["state"] == "queued"
    assert restored_jobs.claim() is None  # A restored lease never proves the old worker has stopped.
    with pytest.raises(AppError, match="job_lease_expired"):
        restored_jobs.finish(interrupted_claim, result={"late": True})
    with restored_ledger.connection() as db:
        assert db.execute("SELECT checkpoint FROM jobs WHERE id=?", (interrupted["id"],)).fetchone()[0] == '{"part":1}'
    if include_catalog:
        from michiflin import gutenberg

        restored_store = ArtifactStore(restored_ledger)
        with restored_store.open("synthetic-owner", 20, 42, artifact["id"]) as stream:
            assert stream.read() == b"Private synthetic file"
        with restored_store.open('synthetic-owner', 20, 42, worker_file['id']) as stream:
            assert stream.read() == worker_body
        assert restored_store.directory.stat().st_mode & 0o777 == 0o700
        assert restored_store.get("synthetic-owner", 20, 42, missing["id"])["state"] == "uncertain"
        with pytest.raises(AppError, match="artifact_not_ready"), restored_store.open("synthetic-owner", 20, 42, missing["id"]):
            pass
        restored_cache = destination / "state" / "catalog" / "catalog.csv.gz"
        assert restored_cache.read_bytes() == cached.read_bytes()
        assert int(restored_cache.stat().st_mtime) == fetched_at
        assert restored_cache.stat().st_mode & 0o777 == 0o600
        monkeypatch.setattr(gutenberg, "fetch", lambda *_a, **_k: pytest.fail("A restored fresh feed must not be downloaded again"))
        client = gutenberg.GutenbergClient(restored_cache.parent)
        client.sync()
        assert client.database.is_file()
    else:
        assert not (destination / "state" / "catalog").exists()
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


@pytest.mark.parametrize('case', ['missing_payload', 'unlisted_payload', 'wrong_hash', 'negative_time', 'boolean_time', 'oversized', 'symlink'])
def test_invalid_optional_catalogue_never_reaches_native_identity_import(tmp_path, monkeypatch, case):
    import io
    import json
    import tarfile
    from unittest.mock import Mock

    import deltachat2

    from michiflin.gutenberg import FEED_LIMIT

    payload = b'synthetic public cache'
    manifest = {'format': 1, 'deliveries': {},
                'account_sha256': hashlib.sha256(b'account').hexdigest(),
                'workspace_sha256': hashlib.sha256(b'workspace').hexdigest(),
                'catalog': {'sha256': hashlib.sha256(payload).hexdigest(), 'fetched_at': 1}}
    if case == 'unlisted_payload':
        manifest.pop('catalog')
    elif case == 'wrong_hash':
        manifest['catalog']['sha256'] = '0' * 64
    elif case == 'negative_time':
        manifest['catalog']['fetched_at'] = -1
    elif case == 'boolean_time':
        manifest['catalog']['fetched_at'] = True
    elif case == 'oversized':
        payload = b'x' * (FEED_LIMIT + 1)
    archive = tmp_path / 'malformed.tar'
    with tarfile.open(archive, 'w') as bundle:
        entries = {'account.tar': b'account', 'workspace.sqlite3': b'workspace', 'manifest.json': json.dumps(manifest).encode()}
        if case != 'missing_payload':
            entries['catalog.csv.gz'] = payload
        for name, body in entries.items():
            item = tarfile.TarInfo(name)
            item.size = len(body)
            if name == 'catalog.csv.gz' and case == 'symlink':
                item.type = tarfile.SYMTYPE
                item.linkname = '/private/identity'
                item.size = 0
                bundle.addfile(item)
            else:
                bundle.addfile(item, io.BytesIO(body))
    encrypted = tmp_path / 'malformed.snapshot'
    seal(archive, encrypted, PASSPHRASE)
    native = Mock(side_effect=AssertionError('Invalid cache must fail before importing any native identity'))
    monkeypatch.setattr(deltachat2, 'IOTransport', native)
    with pytest.raises(ValueError):
        restore(encrypted, tmp_path / 'restored', PASSPHRASE)
    native.assert_not_called()
    assert not (tmp_path / 'restored').exists()


@pytest.mark.parametrize('case', ['missing', 'unlisted', 'wrong_hash', 'wrong_size', 'boolean_size',
                                  'traversal', 'symlink', 'pending_import', 'extra_field'])
def test_artifact_snapshot_failures_never_reach_native_identity_import(tmp_path, monkeypatch, case):
    import io
    import json
    import tarfile
    from unittest.mock import Mock

    import deltachat2

    from michiflin.artifacts import ArtifactStore

    ledger = Ledger(tmp_path / 'state')
    ledger.bind(42, 'owner', 20)
    store = ArtifactStore(ledger)
    artifact = store.put('owner', 20, 42, 'synthetic-artifact-0001', 'file.txt', b'content')
    if case == 'pending_import':
        with ledger.transaction() as db:
            db.execute("UPDATE artifacts SET state='importing'")
    workspace = tmp_path / 'workspace.sqlite3'
    with ledger.connection() as db, sqlite3.connect(workspace) as target:
        db.backup(target)
    name, body = artifact['id'] + '.blob', b'content'
    inventory = {name: {'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}}
    if case == 'unlisted':
        inventory = {}
    elif case == 'wrong_hash':
        inventory[name]['sha256'] = '0' * 64
    elif case == 'wrong_size':
        inventory[name]['bytes'] = len(body) + 1
    elif case == 'boolean_size':
        inventory[name]['bytes'] = True
    elif case == 'extra_field':
        inventory[name]['path'] = '/untrusted'
    elif case == 'traversal':
        inventory = {'../outside.blob': inventory[name]}
    manifest = {'format': 1, 'deliveries': {}, 'artifacts': inventory, 'identity': 'synthetic',
                'account_sha256': hashlib.sha256(b'account').hexdigest(),
                'workspace_sha256': hashlib.sha256(workspace.read_bytes()).hexdigest()}
    archive = tmp_path / 'bundle.tar'
    with tarfile.open(archive, 'w') as bundle:
        entries = {'account.tar': b'account', 'workspace.sqlite3': workspace.read_bytes(),
                   'manifest.json': json.dumps(manifest).encode()}
        if case != 'missing':
            entries['artifacts/' + name] = body
        for filename, data in entries.items():
            item = tarfile.TarInfo(filename)
            item.size = len(data)
            if case == 'symlink' and filename.startswith('artifacts/'):
                item.type, item.linkname, item.size = tarfile.SYMTYPE, '/private/secret', 0
                bundle.addfile(item)
            else:
                bundle.addfile(item, io.BytesIO(data))
    encrypted = tmp_path / 'snapshot'
    seal(archive, encrypted, PASSPHRASE)
    native = Mock(side_effect=AssertionError('Malformed artifact must fail before native identity import'))
    monkeypatch.setattr(deltachat2, 'IOTransport', native)
    with pytest.raises(ValueError):
        restore(encrypted, tmp_path / 'restored', PASSPHRASE)
    native.assert_not_called()
    assert not (tmp_path / 'restored').exists()
