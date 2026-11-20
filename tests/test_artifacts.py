import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from michiflin.artifacts import MAX_FILE, ArtifactStore, Limits
from michiflin.errors import AppError
from michiflin.storage import Ledger


@pytest.fixture
def store(tmp_path):
    ledger = Ledger(tmp_path / "state")
    ledger.bind(1, "owner-a", 10)
    ledger.bind(2, "owner-b", 20)
    ledger.bind(3, "owner-a", 10)
    return ArtifactStore(ledger)


def put(store, body=b"synthetic content", number=1):
    return store.put("owner-a", 10, 1, f"synthetic-import-{number:04d}", "example.txt", body)


def get(store, artifact):
    return store.get("owner-a", 10, 1, artifact["id"])


def test_immutable_copy_digest_private_modes_and_no_paths(store):
    file = put(store)
    assert file["state"] == "ready" and file["bytes"] == len(b"synthetic content")
    assert file["sha256"] == hashlib.sha256(b"synthetic content").hexdigest()
    with store.open("owner-a", 10, 1, file["id"]) as stream:
        assert stream.read() == b"synthetic content"
    assert store.directory.stat().st_mode & 0o777 == 0o700
    assert (store.directory / (file["id"] + ".blob")).stat().st_mode & 0o777 == 0o600
    assert not {"owner", "chat", "instance", "request_id", "readers", "digest", "path"} & file.keys()
    assert str(store.directory) not in str(file)


def test_exact_replay_after_reopen_is_not_a_second_file(store):
    first = put(store)
    reopened = ArtifactStore(Ledger(store.ledger.path.parent))
    assert put(reopened) == first
    assert len(list(store.directory.iterdir())) == 1
    with pytest.raises(AppError, match="request_identity_conflict"):
        put(reopened, body=b"different content")


def test_parallel_exact_retries_have_one_identity_and_one_blob(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: put(store), range(16)))
    assert len({row["id"] for row in results}) == 1
    assert len(list(store.directory.glob("*.blob"))) == 1
    assert not list(store.directory.glob("*.partial"))


@pytest.mark.parametrize("binding", [("owner-b", 10, 1), ("owner-a", 20, 1), ("owner-a", 10, 2)])
def test_owner_chat_instance_and_key_boundaries(store, binding):
    file = put(store)
    for operation in (store.get, store.delete):
        with pytest.raises(AppError, match="instance_not_authorized"):
            operation(*binding, file["id"])
    with pytest.raises(AppError, match="instance_not_authorized"), store.open(*binding, file["id"]):
        pass


def test_another_authorized_instance_still_cannot_access_the_file(store):
    file = put(store)
    assert store.list("owner-a", 10, 3)["files"] == []
    with pytest.raises(AppError, match="artifact_not_found"):
        store.get("owner-a", 10, 3, file["id"])


def test_revocation_stops_read_delete_and_ingestion(store):
    file = put(store)
    store.ledger.revoke(1)
    with pytest.raises(AppError, match="instance_not_authorized"):
        get(store, file)
    with pytest.raises(AppError, match="instance_not_authorized"):
        put(store, number=2)
    with pytest.raises(AppError, match="instance_not_authorized"), store.open("owner-a", 10, 1, file["id"]):
        pass


def test_open_pins_block_deletion_and_exception_releases_pin(store):
    file = put(store)
    with pytest.raises(RuntimeError, match="synthetic"), store.open("owner-a", 10, 1, file["id"]):
        with pytest.raises(AppError, match="artifact_busy"):
            store.delete("owner-a", 10, 1, file["id"])
        raise RuntimeError("synthetic")
    assert store.delete("owner-a", 10, 1, file["id"])["deleted"]
    assert get(store, file)["state"] == "deleted"
    assert put(store)["state"] == "deleted"  # A deleted request never recreates a file.
    assert store.delete("owner-a", 10, 1, file["id"])["deleted"]


def test_imported_copy_never_edits_or_deletes_original(store, tmp_path):
    source = tmp_path / "phone-copy.xdc"
    source.write_bytes(b"not executed")
    file = store.import_file("owner-a", 10, 1, "synthetic-native-0001", "phone-copy.xdc", source)
    source.write_bytes(b"changed original")
    with store.open("owner-a", 10, 1, file["id"]) as stream:
        assert stream.read() == b"not executed"
    store.delete("owner-a", 10, 1, file["id"])
    assert source.read_bytes() == b"changed original"


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "parent_symlink"])
def test_unsafe_native_or_worker_sources_are_refused(store, tmp_path, kind):
    original = tmp_path / "original"
    original.write_bytes(b"preserved")
    source = tmp_path / "source"
    if kind == "symlink":
        source.symlink_to(original)
    elif kind == "hardlink":
        os.link(original, source)
    elif kind == "fifo":
        os.mkfifo(source)
    else:
        source.symlink_to(tmp_path, target_is_directory=True)
        source = source / "original"
    with pytest.raises((AppError, OSError)):
        store.import_file("owner-a", 10, 1, "synthetic-native-0001", "file", source)
    assert not store.list("owner-a", 10, 1)["files"]


def test_corrupt_missing_and_linked_stored_blobs_are_not_read(store, tmp_path):
    file = put(store)
    path = store.directory / (file["id"] + ".blob")
    path.write_bytes(b"!" * file["bytes"])
    with pytest.raises(AppError, match="artifact_hash_mismatch"), store.open("owner-a", 10, 1, file["id"]):
        pass
    path.unlink()
    with pytest.raises(FileNotFoundError), store.open("owner-a", 10, 1, file["id"]):
        pass
    path.symlink_to(tmp_path)
    with pytest.raises(OSError), store.open("owner-a", 10, 1, file["id"]):
        pass
    with store.ledger.connection() as db:
        assert db.execute("SELECT readers FROM artifacts").fetchone()[0] == 0


def test_owner_global_and_history_quotas_reserve_before_copy(store):
    store.limits = Limits(file_bytes=10, owner_bytes=10, total_bytes=15, owner_files=1, total_files=2,
                          owner_records=2, total_records=3)
    first = put(store, b"1234567890")
    with pytest.raises(AppError, match="artifact_storage_limit"):
        put(store, b"x", number=2)
    with pytest.raises(AppError, match="artifact_storage_limit"):
        store.put("owner-b", 20, 2, "synthetic-import-0001", "file", b"123456")
    store.delete("owner-a", 10, 1, first["id"])
    second = put(store, b"x", number=2)
    store.delete("owner-a", 10, 1, second["id"])
    with pytest.raises(AppError, match="artifact_history_limit"):
        put(store, b"x", number=3)
    assert put(store, b"1234567890")["state"] == "deleted"


def test_empty_files_still_consume_record_and_file_quota(store):
    store.limits = replace(store.limits, owner_files=1)
    put(store, b"")
    with pytest.raises(AppError, match="artifact_storage_limit"):
        put(store, b"", number=2)


def test_import_fsync_failure_cleans_own_stage_and_keeps_failed_replay_guard(store, monkeypatch):
    monkeypatch.setattr(os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("synthetic disk full")))
    with pytest.raises(OSError):
        put(store)
    assert not list(store.directory.iterdir())
    assert put(store)["state"] == "failed"
    with store.ledger.connection() as db:
        assert db.execute("SELECT reserved FROM artifacts").fetchone()[0] == 0


def test_published_import_with_failed_directory_sync_is_uncertain_not_retried(store, monkeypatch):
    original = os.fsync
    inode = store.directory.stat().st_ino

    def fail(fd):
        if os.fstat(fd).st_ino == inode:
            raise OSError("synthetic directory sync failure")
        original(fd)

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError):
        put(store)
    file = put(store)
    assert file["state"] == "uncertain"
    assert (store.directory / (file["id"] + ".blob")).is_file()
    with pytest.raises(AppError, match="artifact_not_ready"), store.open("owner-a", 10, 1, file["id"]):
        pass
    monkeypatch.setattr(os, "fsync", original)
    store.reconcile(file["id"], outcome="ready", evidence_sha256="a" * 64)
    assert get(store, file)["state"] == "ready"


def test_interrupted_import_delete_and_reader_pins_recover_conservatively(store):
    first = put(store)
    second = put(store, number=2)
    with store.ledger.transaction() as db:
        db.execute("UPDATE artifacts SET state='importing',readers=1 WHERE id=?", (first["id"],))
        db.execute("UPDATE artifacts SET state='deleting' WHERE id=?", (second["id"],))
    reopened = ArtifactStore(Ledger(store.ledger.path.parent))
    assert get(reopened, first)["state"] == "importing"  # Constructor is not restart recovery.
    reopened.ledger.recover_interrupted()
    assert get(reopened, first)["state"] == "uncertain"
    with reopened.ledger.connection() as db:
        assert db.execute("SELECT readers FROM artifacts WHERE id=?", (first["id"],)).fetchone()[0] == 1
    reopened.reconcile(first["id"], outcome="ready", evidence_sha256="a" * 64)
    reopened.reconcile(second["id"], outcome="deleted", evidence_sha256="b" * 64)
    assert get(reopened, first)["state"] == "ready"
    assert get(reopened, second)["state"] == "deleted"
    assert not (store.directory / (second["id"] + ".blob")).exists()


def test_pending_artifact_and_delivery_inventory_cannot_exceed_snapshot_cap(store):
    with store.ledger.transaction() as db:
        for number in range(100):
            db.execute("INSERT INTO outbox VALUES(?,?,?,?,?,?,?,'uncertain',?,NULL)",
                       (f"{number:032x}", "other-owner", 20, 2, f"{number:032x}.txt", "file", 1, 1))
    with pytest.raises(AppError, match="snapshot_file_limit"):
        put(store)
    with pytest.raises(AppError, match="snapshot_file_limit"):
        store.ledger.check_delivery_capacity("owner-a")


@pytest.mark.parametrize("name", ["../escape", "a/b", "a\\b", "", "..", "bad\x00", "bad\ud800", "x" * 241])
def test_invalid_display_names_are_not_paths(store, name):
    with pytest.raises(AppError, match="invalid_artifact_name"):
        store.put("owner-a", 10, 1, "synthetic-import-0001", name, b"content")


def test_oversized_sources_and_public_body_are_refused(store, tmp_path):
    source = tmp_path / "large"
    with source.open("wb") as stream:
        stream.truncate(MAX_FILE + 1)
    with pytest.raises(AppError):
        store.import_file("owner-a", 10, 1, "synthetic-import-0001", "large", source)
    store.limits = Limits(file_bytes=1)
    with pytest.raises(AppError, match="artifact_file_limit"):
        put(store, b"xx")


def test_unsafe_vault_permissions_are_not_silently_changed(store):
    store.directory.chmod(0o755)
    with pytest.raises(AppError, match="artifact_storage_unsafe"):
        ArtifactStore(store.ledger)
    assert store.directory.stat().st_mode & 0o777 == 0o755


def test_ready_file_with_abandoned_read_pin_is_quarantined_only_by_startup_recovery(store):
    file = put(store)
    with store.ledger.transaction() as db:
        db.execute('UPDATE artifacts SET readers=1 WHERE id=?', (file['id'],))
    assert get(ArtifactStore(store.ledger), file)['state'] == 'ready'
    store.ledger.recover_interrupted()
    assert get(store, file)['state'] == 'uncertain'
    with pytest.raises(AppError, match='artifact_not_ready'), store.open('owner-a', 10, 1, file['id']):
        pass
    store.reconcile(file['id'], outcome='ready', evidence_sha256='a' * 64)
    assert store.delete('owner-a', 10, 1, file['id'])['deleted']


def test_interruption_immediately_after_atomic_rename_does_not_release_reserved_bytes(store, monkeypatch):
    from michiflin import artifacts

    original = artifacts._publish

    def interrupted(*args):
        original(*args)
        raise RuntimeError('synthetic interruption after successful rename')

    monkeypatch.setattr(artifacts, '_publish', interrupted)
    with pytest.raises(RuntimeError):
        put(store)
    file = put(store)
    assert file['state'] == 'uncertain'
    with store.ledger.connection() as db:
        assert db.execute('SELECT reserved FROM artifacts').fetchone()[0] == file['bytes']
    assert (store.directory / (file['id'] + '.blob')).is_file()


def test_repeated_operator_resolutions_append_evidence_instead_of_overwriting_it(store):
    file = put(store)
    for digest in ('a' * 64, 'b' * 64):
        with store.ledger.transaction() as db:
            db.execute("UPDATE artifacts SET state='uncertain' WHERE id=?", (file['id'],))
        store.reconcile(file['id'], outcome='ready', evidence_sha256=digest)
    with store.ledger.connection() as db:
        assert [row[0] for row in db.execute('SELECT evidence_sha256 FROM artifact_resolutions ORDER BY serial')] == ['a' * 64, 'b' * 64]


def test_delete_sync_failure_retains_uncertainty_and_storage_until_operator_proof(store, monkeypatch):
    file = put(store)
    original = os.fsync
    monkeypatch.setattr(os, 'fsync', lambda _fd: (_ for _ in ()).throw(OSError('synthetic sync failure')))
    with pytest.raises(OSError):
        store.delete('owner-a', 10, 1, file['id'])
    assert get(store, file)['state'] == 'uncertain'
    with store.ledger.connection() as db:
        assert db.execute('SELECT reserved FROM artifacts').fetchone()[0] == file['bytes']
    with pytest.raises(AppError, match='artifact_busy'):
        store.delete('owner-a', 10, 1, file['id'])
    monkeypatch.setattr(os, 'fsync', original)
    store.reconcile(file['id'], outcome='deleted', evidence_sha256='a' * 64)
    assert get(store, file)['state'] == 'deleted'


def test_native_ingestion_revoked_between_hashing_and_publication_does_not_publish(store, tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.write_bytes(b'original source')
    original = os.fsync
    revoked = False

    def revoke_after_copy(fd):
        nonlocal revoked
        original(fd)
        if not revoked:
            store.ledger.revoke(1)
            revoked = True

    monkeypatch.setattr(os, 'fsync', revoke_after_copy)
    with pytest.raises(AppError, match='instance_not_authorized'):
        store.import_file('owner-a', 10, 1, 'synthetic-revoked-import', 'file', source)
    assert not list(store.directory.iterdir())
    assert source.read_bytes() == b'original source'


def test_failed_import_history_cannot_hide_ready_files_from_bounded_listing(store):
    file = put(store)
    with store.ledger.transaction() as db:
        for index in range(55):
            row = dict(db.execute('SELECT * FROM artifacts WHERE id=?', (file['id'],)).fetchone())
            row.update(id=f'{index + 100:032x}', request_id=f'synthetic-failed-{index:04d}',
                       state='failed', reserved=0, created=file['created'] + index + 1)
            db.execute('INSERT INTO artifacts (' + ','.join(row) + ') VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))
    files = store.list('owner-a', 10, 1)['files']
    assert len(files) == 50
    assert files[0]['id'] == file['id']
