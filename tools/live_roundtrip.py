from __future__ import annotations

import argparse
import json
import queue
import threading
import time
from pathlib import Path

from deltachat2 import IOTransport, MessageData, Rpc
from deltachat2.types import EventTypeIncomingMsg, EventTypeWebxdcStatusUpdate

from michiflin.adapter import DeltaAdapter
from michiflin.config import Settings
from michiflin.package import build
from michiflin.service import Service


def run(directory: Path, source: Path, *, resume: bool = False) -> None:
    marker = directory / "live-test.marker"
    if directory.exists() and (not resume or not marker.is_file()):
        raise ValueError("Use a new private directory or explicitly resume a marked test directory")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker.write_text("Michiflin separate-account live validation")
    marker.chmod(0o600)
    with IOTransport(accounts_dir=str(directory / "accounts")) as transport:
        rpc = Rpc(transport)
        incoming = queue.Queue()

        stopping = threading.Event()

        def reader():
            try:
                while not stopping.is_set():
                    incoming.put(rpc.get_next_event())
            except Exception:
                return

        event_reader = threading.Thread(target=reader, daemon=True)
        event_reader.start()
        if resume:
            accounts = rpc.get_all_account_ids()
            bots = [account for account in accounts if rpc.get_config(account, "bot") == "1"]
            if len(accounts) != 2 or len(bots) != 1:
                raise ValueError("The marked test directory has an unexpected account layout")
            bot, peer = bots[0], next(account for account in accounts if account != bots[0])
        else:
            bot, peer = rpc.add_account(), rpc.add_account()
        adapter = None

        def wait(predicate, timeout=70):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                result = predicate()
                if result:
                    return result
                try:
                    event = incoming.get(timeout=min(1, max(0.01, deadline - time.monotonic())))
                except queue.Empty:
                    continue
                if adapter is not None and event.context_id == bot:
                    if isinstance(event.event, EventTypeIncomingMsg):
                        adapter.new_message(rpc.get_message(bot, event.event.msg_id))
                    elif isinstance(event.event, EventTypeWebxdcStatusUpdate):
                        adapter.status_update(event.event.msg_id)
            raise TimeoutError("Live test stage timed out; account details are not included in this error")

        try:
            rpc.set_config(bot, "bot", "1")
            for account in (bot, peer):
                if not rpc.is_configured(account):
                    rpc.add_transport_from_qr(account, "DCACCOUNT:nine.testrun.org")
                wait(lambda account=account: rpc.is_configured(account))
            print("LIVE_ACCOUNTS_CONFIGURED", flush=True)
            service = Service(Settings(state_dir=directory / "state"))
            package = build(source, directory / "packages")
            adapter = DeltaAdapter(rpc, bot, service, package)
            rpc.start_io(bot)
            rpc.start_io(peer)
            chat = rpc.secure_join(peer, rpc.get_chat_securejoin_qr_code(bot, None))
            def joined():
                contacts = [identifier for identifier in rpc.get_chat_contacts(peer, chat) if identifier > 9]
                return len(contacts) == 1 and rpc.get_contact(peer, contacts[0]).e2ee_avail and rpc.get_contact(peer, contacts[0]).is_verified

            wait(joined)
            rpc.send_msg(peer, chat, MessageData(text="/app"))

            def received_app():
                for identifier in rpc.get_message_ids(peer, chat, False, False):
                    message = rpc.get_message(peer, identifier)
                    if getattr(message.view_type, "value", message.view_type) == "Webxdc":
                        if not message.show_padlock:
                            raise ValueError("Received miniapp was not encrypted")
                        return identifier
                return None

            instance = wait(received_app)
            print("LIVE_ENCRYPTED_MINIAPP_RECEIVED", flush=True)

            def request(identifier, operation, args, session=""):
                payload = {"type": "michiflin.request", "protocol": 1, "id": identifier,
                           "op": operation, "args": args, "session": session, "locale": "es"}
                floor = max((item["serial"] for item in json.loads(rpc.get_webxdc_status_updates(peer, instance, 0))), default=0)
                rpc.send_webxdc_status_update(peer, instance, json.dumps({"payload": payload}), "")

                def response():
                    updates = json.loads(rpc.get_webxdc_status_updates(peer, instance, 0))
                    for update in updates:
                        frame = update.get("payload", {})
                        if update.get("serial", 0) > floor and frame.get("type") == "michiflin.response" and frame.get("body", {}).get("id") == identifier:
                            return frame["body"]
                    return None

                return wait(response)

            hello = request("live-handshake-0001", "hello", {})
            assert hello["ok"] and hello["data"]["role"] == "member"
            response = request("live-arithmetic-0001", "utilities.calculate", {"expression": "17 * 23"}, hello["data"]["session"])
            assert response["ok"] and response["data"]["value"] == 391
            print("LIVE_PUBLIC_MEMBER_ROUNDTRIP_391", flush=True)
            note = request("live-note-request-0001", "notes.add", {"text": "Synthetic encrypted roundtrip note"}, hello["data"]["session"])
            assert note["ok"]
            repeated = request("live-note-request-0001", "notes.add", {"text": "Synthetic encrypted roundtrip note"}, hello["data"]["session"])
            assert repeated == note
            with service.ledger.connection() as db:
                assert db.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
            print("LIVE_REQUEST_REPLAY_DID_NOT_DUPLICATE_NOTE", flush=True)
        finally:
            stopping.set()
            rpc.stop_io_for_all_accounts()
            event_reader.join(timeout=3)
    print("LIVE_TEST_COMPLETE_MAIL_IO_STOPPED", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Opt-in encrypted mail roundtrip using two new temporary chatmail accounts")
    parser.add_argument("--accept-create-test-accounts", action="store_true", required=True)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=Path("miniapp"))
    parser.add_argument("--resume-test-accounts", action="store_true")
    args = parser.parse_args()
    try:
        run(args.directory, args.source, resume=args.resume_test_accounts)
    except Exception as error:
        print("LIVE_TEST_FAILED_" + type(error).__name__, flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
