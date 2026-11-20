from __future__ import annotations

import fcntl
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from michiflin import runtime
from michiflin.config import Settings

core = pytest.importorskip("deltachat2")
types = pytest.importorskip("deltachat2.types")
Event, EventTypeIncomingMsg, EventTypeWebxdcStatusUpdate = types.Event, types.EventTypeIncomingMsg, types.EventTypeWebxdcStatusUpdate


def test_native_events_keep_account_boundaries_and_dispatch_actual_message_types():
    adapter, rpc = Mock(), Mock()
    event = Event(context_id=1, event=EventTypeIncomingMsg(chat_id=20, msg_id=42))
    runtime.dispatch(adapter, rpc, 2, event)
    rpc.get_message.assert_not_called()
    runtime.dispatch(adapter, rpc, 1, event)
    rpc.get_message.assert_called_once_with(1, 42)
    adapter.new_message.assert_called_once_with(rpc.get_message.return_value)
    update = Event(context_id=1, event=EventTypeWebxdcStatusUpdate(msg_id=42, status_update_serial=3))
    runtime.dispatch(adapter, rpc, 1, update)
    adapter.status_update.assert_called_once_with(42)


def test_event_failures_never_log_private_native_exception_text(caplog):
    adapter, rpc = Mock(), Mock()
    adapter.new_message.side_effect = RuntimeError("synthetic-private-native-address-and-key")
    runtime.dispatch(adapter, rpc, 1, Event(context_id=1, event=EventTypeIncomingMsg(chat_id=20, msg_id=42)))
    assert "could not be processed safely" in caplog.text
    assert "synthetic-private" not in caplog.text


def test_shutdown_acknowledges_single_account_io_before_terminating_a_pending_event_reader():
    transport = Mock()
    transport.writer_thread.is_alive.return_value = False
    transport.process.wait.side_effect = [subprocess.TimeoutExpired("synthetic", 1), 0]
    transport.process.poll.return_value = 0
    runtime.close_transport(transport, 7, timeout=1)
    transport.call.assert_called_once_with("stop_io", 7)
    transport.process.stdin.close.assert_called_once()
    transport.process.terminate.assert_called_once()
    transport.process.kill.assert_not_called()
    transport.reader_thread.join.assert_called_once_with(timeout=3)


def test_unacknowledged_native_shutdown_is_bounded_and_reported_as_unclean():
    transport = Mock()
    transport.call.side_effect = RuntimeError("synthetic-private-native-error")
    transport.writer_thread.is_alive.return_value = False
    transport.process.poll.return_value = None
    with pytest.raises(RuntimeError, match="did not acknowledge"):
        runtime.close_transport(transport, 7, timeout=0.1)
    transport.process.kill.assert_called_once()
    transport.process.stdin.close.assert_not_called()


def test_blocked_native_shutdown_does_not_wait_without_a_deadline():
    release = threading.Event()
    transport = Mock()
    transport.call.side_effect = lambda *_: release.wait(3)
    transport.writer_thread.is_alive.return_value = False
    transport.process.poll.return_value = None
    try:
        with pytest.raises(RuntimeError, match="did not acknowledge"):
            runtime.close_transport(transport, 7, timeout=0.02)
        transport.process.kill.assert_called_once()
    finally:
        release.set()


def test_exclusive_runtime_lock_is_acquired_before_the_native_core_starts(tmp_path, monkeypatch):
    (tmp_path / "accounts").mkdir()
    (tmp_path / "accounts" / "accounts.toml").write_text("synthetic")
    state = tmp_path / "state"
    state.mkdir()
    factory = Mock()
    monkeypatch.setattr(core, "IOTransport", factory)
    with (state / "runtime.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="already owns"):
            runtime.serve(tmp_path, tmp_path, threading.Event(), Settings(state_dir=state))
    factory.assert_not_called()


def test_startup_signal_prevents_mail_io_and_core_inherits_supervisor_process_group(tmp_path, monkeypatch):
    (tmp_path / "accounts").mkdir()
    (tmp_path / "accounts" / "accounts.toml").write_text("synthetic")
    stop = threading.Event()
    stop.set()
    rpc = Mock()
    rpc.get_all_account_ids.return_value = [1]
    rpc.is_configured.return_value = True
    factory = Mock()
    monkeypatch.setattr(core, "IOTransport", factory)
    monkeypatch.setattr(core, "Rpc", lambda _: rpc)
    monkeypatch.setattr(runtime, "DeltaAdapter", Mock())
    monkeypatch.setattr(runtime, "Service", lambda _: SimpleNamespace(ledger=Mock()))
    monkeypatch.setattr(runtime, "build", lambda *_: tmp_path / "synthetic.xdc")
    closer = Mock()
    monkeypatch.setattr(runtime, "close_transport", closer)
    runtime.serve(tmp_path, tmp_path, stop, Settings(state_dir=tmp_path / "state"))
    assert factory.call_args.kwargs["process_group"] is None
    rpc.start_io.assert_not_called()
    closer.assert_called_once_with(factory.return_value, 1)


def test_runtime_starts_workers_after_recovery_and_joins_before_unlock_and_native_close(tmp_path, monkeypatch):
    (tmp_path / 'accounts').mkdir()
    (tmp_path / 'accounts' / 'accounts.toml').write_text('synthetic')
    state = tmp_path / 'state'
    stop, order = threading.Event(), []
    rpc = Mock()
    rpc.get_all_account_ids.return_value = [1]
    rpc.is_configured.return_value = True
    rpc.start_io.side_effect = lambda *_: stop.set()
    service, workers = Mock(), Mock()

    def locked(label):
        with (state / 'runtime.lock').open('a') as lock, pytest.raises(BlockingIOError):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        order.append(label)

    service.ledger.recover_interrupted.side_effect = lambda: locked('recover')
    service.start_workers.side_effect = lambda _stop: locked('start') or workers
    workers.close.side_effect = lambda: locked('join')
    monkeypatch.setattr(core, 'IOTransport', Mock())
    monkeypatch.setattr(core, 'Rpc', lambda _: rpc)
    monkeypatch.setattr(runtime, 'Service', lambda _: service)
    monkeypatch.setattr(runtime, 'DeltaAdapter', Mock())
    monkeypatch.setattr(runtime, 'build', lambda *_: tmp_path / 'synthetic.xdc')
    monkeypatch.setattr(runtime, 'close_transport', lambda *_: locked('native-close'))
    runtime.serve(tmp_path, tmp_path, stop, Settings(state_dir=state))
    assert order == ['recover', 'start', 'join', 'native-close']
    with (state / 'runtime.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_runtime_worker_failure_still_closes_native_transport_before_unlock(tmp_path, monkeypatch):
    from michiflin.errors import AppError

    (tmp_path / 'accounts').mkdir()
    (tmp_path / 'accounts' / 'accounts.toml').write_text('synthetic')
    rpc, service, workers = Mock(), Mock(), Mock()
    rpc.get_all_account_ids.return_value = [1]
    rpc.is_configured.return_value = True
    workers.check.side_effect = AppError('job_workers_failed')
    workers.close.side_effect = AppError('job_workers_failed')
    service.start_workers.return_value = workers
    monkeypatch.setattr(core, 'IOTransport', Mock())
    monkeypatch.setattr(core, 'Rpc', lambda _: rpc)
    monkeypatch.setattr(runtime, 'Service', lambda _: service)
    monkeypatch.setattr(runtime, 'DeltaAdapter', Mock())
    monkeypatch.setattr(runtime, 'build', lambda *_: tmp_path / 'synthetic.xdc')
    closer = Mock()
    monkeypatch.setattr(runtime, 'close_transport', closer)
    with pytest.raises(AppError, match='job_workers_failed'):
        runtime.serve(tmp_path, tmp_path, threading.Event(), Settings(state_dir=tmp_path / 'state'))
    rpc.start_io.assert_not_called()
    workers.close.assert_called_once()
    closer.assert_called_once()
