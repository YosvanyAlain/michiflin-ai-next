from __future__ import annotations

import argparse
import fcntl
import logging
import os
import queue
import signal
import subprocess
import threading
from pathlib import Path

from .adapter import DeltaAdapter
from .config import Settings
from .package import build
from .service import Service


def close_transport(transport, account: int | None = None, timeout: float = 60) -> None:
    failures = []

    def stop_io() -> None:
        try:
            if account is None:
                transport.call("stop_io_for_all_accounts")
            else:
                transport.call("stop_io", account)
        except Exception:
            failures.append(True)

    stopper = threading.Thread(target=stop_io, daemon=True)
    stopper.start()
    stopper.join(timeout)
    transport.closing = True
    transport.request_queue.put(None)
    transport.writer_thread.join(timeout=3)
    acknowledged = not stopper.is_alive() and not failures
    if acknowledged and not transport.writer_thread.is_alive():
        transport.process.stdin.close()
        try:
            transport.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            # A pending get_next_event RPC can outlive stdin EOF even after IO is fully stopped.
            transport.process.terminate()
            try:
                transport.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                acknowledged = False
    else:
        acknowledged = False
    if transport.process.poll() is None:
        transport.process.kill()
        transport.process.wait(timeout=5)
    transport.reader_thread.join(timeout=3)
    transport.writer_thread.join(timeout=3)
    if not acknowledged:
        raise RuntimeError("The native transport did not acknowledge a clean IO shutdown")


def dispatch(adapter: DeltaAdapter, rpc, account: int, event) -> None:
    from deltachat2.types import EventTypeIncomingMsg, EventTypeWebxdcStatusUpdate

    if event.context_id != account:
        return
    try:
        if isinstance(event.event, EventTypeIncomingMsg):
            adapter.new_message(rpc.get_message(account, event.event.msg_id))
        elif isinstance(event.event, EventTypeWebxdcStatusUpdate):
            adapter.status_update(event.event.msg_id)
    except Exception:
        logging.warning("A native event could not be processed safely")


def serve(config_dir: Path, source: Path, stop: threading.Event, settings: Settings,
          account_id: int | None = None) -> None:
    from deltachat2 import IOTransport, Rpc

    if not (config_dir / "accounts" / "accounts.toml").is_file():
        raise RuntimeError("An existing restored account directory is required")
    settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (settings.state_dir / "runtime.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another runtime already owns this state directory") from None
        # Keep the native core inside the supervisor's process group for forced-shutdown containment.
        transport = IOTransport(accounts_dir=str(config_dir / "accounts"), stderr=subprocess.DEVNULL,
                                process_group=None)
        transport.start()
        account = None
        try:
            rpc = Rpc(transport)
            accounts = rpc.get_all_account_ids()
            if (len(accounts) != 1 or account_id is not None and account_id != accounts[0]
                    or not rpc.is_configured(accounts[0])):
                raise RuntimeError("Exactly one configured account is required per state directory")
            account = accounts[0]
            service = Service(settings)
            service.ledger.recover_interrupted()
            package = build(source, settings.state_dir / "packages")
            adapter = DeltaAdapter(rpc, account, service, package)
            incoming: queue.Queue = queue.Queue(maxsize=256)
            reader_failed = threading.Event()

            def read() -> None:
                try:
                    while not stop.is_set():
                        event = rpc.get_next_event()
                        while not stop.is_set():
                            try:
                                incoming.put(event, timeout=0.25)
                                break
                            except queue.Full:
                                continue
                except Exception:
                    if not stop.is_set():
                        reader_failed.set()

            if not stop.is_set():
                adapter.resume_deliveries()
            if not stop.is_set():
                threading.Thread(target=read, daemon=True).start()
                rpc.start_io(account)
            while not stop.is_set():
                if reader_failed.is_set():
                    raise RuntimeError("The native event reader stopped unexpectedly")
                try:
                    event = incoming.get(timeout=0.25)
                except queue.Empty:
                    continue
                if not stop.is_set():
                    dispatch(adapter, rpc, account, event)
        finally:
            stop.set()
            close_transport(transport, account)


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog="michiflin")
    parser.add_argument("--config-dir", "-c", type=Path, default=Path.home() / ".config" / "michiflin")
    parser.add_argument("--miniapp-source", type=Path, default=Path("miniapp"))
    parser.add_argument("--account", "-a", type=int)
    parser.add_argument("command", choices=["serve"])
    args = parser.parse_args()
    stop = threading.Event()
    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, lambda _number, _frame: stop.set())
    try:
        serve(args.config_dir, args.miniapp_source, stop, Settings.from_environment(), args.account)
    except Exception:
        logging.error("The private runtime stopped safely; account details were not logged")
        raise SystemExit(1) from None
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    main()
