from __future__ import annotations

import json
import signal
import subprocess
from unittest.mock import Mock

import pytest

from michiflin import deployment

SEED = {"format": 1, "asset_id": 10, "sha256": "a" * 64}
STEM = "session-" + "b" * 32


class Store:
    def __init__(self, assets=(), manifest=None):
        self.inventory = list(assets)
        self.manifest = manifest
        self.downloads = []

    def assets(self, _release):
        return self.inventory

    def download(self, asset, destination, maximum=0):
        self.downloads.append(asset)
        assert maximum == 4096
        destination.write_text(json.dumps(self.manifest))


def test_initial_restore_uses_seed_without_an_unfinished_live_session(tmp_path):
    store = Store([{"id": 20, "name": "validation-" + "c" * 32 + ".verified.json"}])
    assert deployment.latest_checkpoint(store, {}, SEED, tmp_path) == SEED
    assert not store.downloads


@pytest.mark.parametrize("assets", [
    [{"id": 20, "name": STEM + ".running.json"}],
    [{"id": 20, "name": STEM + ".running.json"}, {"id": 19, "name": STEM + ".verified.json"}],
    [{"id": 20, "name": STEM + ".running.json"},
     {"id": 21, "name": "session-" + "d" * 32 + ".verified.json"}],
])
def test_incomplete_shutdown_blocks_automatic_stale_state_restore(tmp_path, assets):
    store = Store(assets)
    with pytest.raises(ValueError, match="automatic restart is blocked"):
        deployment.latest_checkpoint(store, {}, SEED, tmp_path)
    assert not store.downloads


def test_newest_session_requires_its_own_verified_checkpoint(tmp_path):
    old = "session-" + "e" * 32
    assets = [{"id": 1, "name": old + ".running.json"}, {"id": 3, "name": old + ".verified.json"},
              {"id": 4, "name": STEM + ".running.json"}, {"id": 6, "name": STEM + ".verified.json"}]
    manifest = {**SEED, "asset_id": 5, "session": STEM}
    store = Store(assets, manifest)
    assert deployment.latest_checkpoint(store, {}, SEED, tmp_path) == manifest
    assert store.downloads == [6]


def test_verified_checkpoint_cannot_silently_change_session(tmp_path):
    assets = [{"id": 1, "name": STEM + ".running.json"}, {"id": 3, "name": STEM + ".verified.json"}]
    with pytest.raises(ValueError, match="different session"):
        deployment.latest_checkpoint(Store(assets, {**SEED, "session": "different"}), {}, SEED, tmp_path)


@pytest.mark.parametrize("invalid", [None, {}, {**SEED, "asset_id": True}, {**SEED, "sha256": "bad"},
                                      {**SEED, "asset_id": -1}, {**SEED, "format": 2}])
def test_checkpoint_references_require_valid_schema(invalid):
    with pytest.raises(ValueError):
        deployment.validate_checkpoint(invalid)


def test_production_cannot_start_without_exact_revision_arm(tmp_path, monkeypatch):
    monkeypatch.setenv("MICHIFLIN_DEPLOY_REVISION", "a" * 40)
    monkeypatch.setenv("MICHIFLIN_LAUNCH_REVISION", "b" * 40)
    with pytest.raises(ValueError, match="not armed"):
        deployment.execute(validate_only=False, duration=1, directory=tmp_path, source=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_private_storage_must_never_target_a_public_repository(monkeypatch):
    session = Mock()
    response = Mock()
    response.json.return_value = {"private": False, "permissions": {"push": True}}
    session.request.return_value = response
    monkeypatch.setattr(deployment.requests, "Session", lambda: session)
    with pytest.raises(ValueError, match="must be private"):
        deployment.PrivateStore("synthetic/recovery", "synthetic-token")


def test_shutdown_waits_for_runtime_before_backup_and_excludes_backup_secrets(tmp_path, monkeypatch):
    process = Mock()
    process.wait.side_effect = [subprocess.TimeoutExpired("synthetic", 1), 0]
    process.poll.return_value = 0
    start = Mock(return_value=process)
    monkeypatch.setattr(deployment.subprocess, "Popen", start)
    monkeypatch.setenv("MICHIFLIN_BACKUP_TOKEN", "private-backup-token")
    monkeypatch.setenv("MICHIFLIN_BACKUP_PASSPHRASE", "private-backup-key")
    monkeypatch.setenv("GITHUB_TOKEN", "private-github-token")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/synthetic/python/lib")
    result = deployment.run_bounded(tmp_path, {"admin_keys": {}, "providers": []}, 1, tmp_path)
    assert result == 0
    process.send_signal.assert_called_once_with(signal.SIGINT)
    assert process.wait.call_args.kwargs["timeout"] == 180
    environment = start.call_args.kwargs["env"]
    assert not any("BACKUP" in key or "GITHUB" in key for key in environment)
    assert environment["LD_LIBRARY_PATH"] == "/synthetic/python/lib"
    assert start.call_args.kwargs["start_new_session"] is True


def test_unresponsive_runtime_is_killed_as_a_group_before_checkpoint(tmp_path, monkeypatch):
    process = Mock()
    process.pid = 12345
    process.wait.side_effect = [subprocess.TimeoutExpired("synthetic", 1),
                                subprocess.TimeoutExpired("synthetic", 180), -9]
    process.poll.return_value = None
    monkeypatch.setattr(deployment.subprocess, "Popen", Mock(return_value=process))
    kill = Mock()
    monkeypatch.setattr(deployment.os, "killpg", kill)
    with pytest.raises(subprocess.TimeoutExpired):
        deployment.run_bounded(tmp_path, {"admin_keys": {}, "providers": []}, 1, tmp_path)
    kill.assert_called_once_with(12345, signal.SIGKILL)


def test_validate_mode_uses_real_checkpoint_without_starting_io(tmp_path, monkeypatch):
    _execute_fixture(tmp_path, monkeypatch, validate_only=True)


def test_failed_live_checkpoint_preserves_private_state_and_blocks_restart(tmp_path, monkeypatch):
    _execute_fixture(tmp_path, monkeypatch, validate_only=False, fail_checkpoint=True)


def _execute_fixture(tmp_path, monkeypatch, *, validate_only, fail_checkpoint=False, runtime_exit=0):
    revision = "a" * 40
    config = {"providers": [{"id": "synthetic"}], "admin_keys": {"synthetic": "b" * 40},
              "backup_passphrase": "private-passphrase-" * 3}
    for key, value in {"MICHIFLIN_DEPLOY_REVISION": revision, "MICHIFLIN_LAUNCH_REVISION": revision,
                       "MICHIFLIN_DEPLOY_CONFIG": json.dumps(config), "MICHIFLIN_BACKUP_PASSPHRASE": config["backup_passphrase"],
                       "MICHIFLIN_BACKUP_REPOSITORY": "synthetic/recovery", "MICHIFLIN_BACKUP_TOKEN": "synthetic-token",
                       "MICHIFLIN_SEED_ASSET_ID": "10", "MICHIFLIN_SEED_SHA256": "a" * 64}.items():
        monkeypatch.setenv(key, value)
    store = Mock()
    store.assets.return_value = []
    monkeypatch.setattr(deployment, "PrivateStore", lambda *_: store)
    monkeypatch.setattr(deployment, "checksum", lambda *_: "a" * 64)
    monkeypatch.setattr(deployment, "restore", lambda _snapshot, destination, _key: destination.mkdir())
    run = Mock(return_value=runtime_exit)
    checkpoint = Mock(side_effect=ValueError("synthetic backup failure")) if fail_checkpoint else Mock(return_value=SEED)
    monkeypatch.setattr(deployment, "run_bounded", run)
    monkeypatch.setattr(deployment, "checkpoint", checkpoint)
    if fail_checkpoint:
        with pytest.raises(ValueError, match="synthetic backup failure"):
            deployment.execute(validate_only=validate_only, duration=1, directory=tmp_path, source=tmp_path)
        assert list(tmp_path.glob("next-session-*/runtime"))
        assert store.upload.call_args.args[1].name.endswith(".running.json")
    elif runtime_exit:
        with pytest.raises(RuntimeError, match="latest state was nevertheless verified"):
            deployment.execute(validate_only=False, duration=1, directory=tmp_path, source=tmp_path)
        assert not list(tmp_path.glob("next-session-*"))
    else:
        result = deployment.execute(validate_only=validate_only, duration=1, directory=tmp_path, source=tmp_path)
        assert result["io_started"] is False
        run.assert_not_called()
        assert not list(tmp_path.glob("next-session-*"))
    checkpoint.assert_called_once()


def test_real_synthetic_runtime_shutdown_and_restored_restart(tmp_path):
    import fcntl
    import shutil
    from pathlib import Path

    core = pytest.importorskip("deltachat2")
    if not shutil.which("deltachat-rpc-server"):
        pytest.skip("Native Delta Chat core is not installed")
    from michiflin.backup import create, restore
    from michiflin.storage import Ledger

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    with core.IOTransport(accounts_dir=str(runtime / "accounts")) as transport:
        rpc = core.Rpc(transport)
        account = rpc.add_account()
        for key, value in (("addr", "session-test@example.invalid"),
                           ("configured_addr", "session-test@example.invalid"), ("configured", "1")):
            rpc.set_config(account, key, value)
        rpc.get_chat_securejoin_qr_code(account, None)
    source = Path(__file__).resolve().parents[1] / "miniapp"
    config = {"admin_keys": {}, "providers": []}
    assert deployment.run_bounded(runtime, config, 1, source) == 0
    assert list((runtime / "state" / "packages").glob("*.xdc"))
    with (runtime / "state" / "runtime.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    encrypted = tmp_path / "synthetic.snapshot"
    passphrase = "synthetic-private-session-checkpoint-only"
    with core.IOTransport(accounts_dir=str(runtime / "accounts")) as transport:
        rpc = core.Rpc(transport)
        create(rpc, rpc.get_all_account_ids()[0], Ledger(runtime / "state"), encrypted, passphrase)
    restored = tmp_path / "restored"
    result = restore(encrypted, restored, passphrase)
    assert result["identity_verified"] and result["io_started"] is False
    assert deployment.run_bounded(restored, config, 1, source) == 0
    with (restored / "state" / "runtime.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_nonzero_runtime_exit_preserves_checkpoint_but_does_not_claim_success(tmp_path, monkeypatch):
    _execute_fixture(tmp_path, monkeypatch, validate_only=False, runtime_exit=1)
