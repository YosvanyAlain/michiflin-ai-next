from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from michiflin.adapter import DeltaAdapter
from michiflin.config import Settings
from michiflin.package import build
from michiflin.service import Service

core = pytest.importorskip("deltachat2")
pytestmark = pytest.mark.skipif(not shutil.which("deltachat-rpc-server"), reason="Native Delta Chat core is not installed")


def configure_offline(rpc, account: int, address: str) -> None:
    # Synthetic .invalid accounts never start IO and never connect to a mail provider.
    for key, value in (("addr", address), ("configured_addr", address), ("configured", "1")):
        rpc.set_config(account, key, value)
    rpc.get_chat_securejoin_qr_code(account, None)


def test_native_core_identity_package_status_and_encrypted_restore(tmp_path):
    """Native local integration, explicitly not a remote encrypted roundtrip."""
    with core.IOTransport(accounts_dir=str(tmp_path / "accounts")) as transport:
        rpc = core.Rpc(transport)
        bot, peer = rpc.add_account(), rpc.add_account()
        configure_offline(rpc, bot, "bot@example.invalid")
        configure_offline(rpc, peer, "peer@example.invalid")
        contacts = rpc.import_vcard_contents(bot, rpc.make_vcard(peer, [1]))
        assert len(contacts) == 1
        chat = rpc.create_chat_by_contact_id(bot, contacts[0])
        service = Service(Settings(state_dir=tmp_path / "state"))
        package = build(Path(__file__).resolve().parents[1] / "miniapp", tmp_path / "packages")
        adapter = DeltaAdapter(rpc, bot, service, package)
        principal = adapter.principal(chat, encrypted=True)
        assert principal.fingerprint and not principal.verified
        native_link = rpc.get_chat_securejoin_qr_code(peer, None)
        assert principal.fingerprint == native_link.split("#", 1)[1].split("&", 1)[0].upper()
        rpc.change_contact_name(bot, contacts[0], "Untrusted " + adapter.own_fingerprint)
        assert adapter.principal(chat, encrypted=True).fingerprint == principal.fingerprint
        instance = rpc.send_msg(bot, chat, core.MessageData(file=str(package), text="Synthetic offline test"))
        assert rpc.get_message(bot, instance).show_padlock
        assert rpc.get_webxdc_info(bot, instance).name == "Michiflin AI"
        service.register_instance(principal, instance)
        request = {"type": "michiflin.request", "protocol": 1, "id": "native-test-123456",
                   "op": "utilities.calculate", "session": service.session, "args": {"expression": "17 * 23"}}
        rpc.send_webxdc_status_update(bot, instance, json.dumps({"payload": request}), "")
        adapter.status_update(instance)
        updates = json.loads(rpc.get_webxdc_status_updates(bot, instance, 0))
        results = [item["payload"]["body"] for item in updates if item["payload"].get("type") == "michiflin.response"]
        assert len(results) == 1 and results[0]["data"]["value"] == 391
        adapter.status_update(instance)
        assert len(json.loads(rpc.get_webxdc_status_updates(bot, instance, 0))) == len(updates)

        delivery_dir = service.settings.state_dir / "deliveries"
        delivery_dir.mkdir(mode=0o700)
        book = delivery_dir / "synthetic.txt"
        book.write_text("Synthetic public-domain text for native delivery testing.")
        receipt = "d" * 32
        service.ledger.queue_delivery(receipt, principal.storage_key, chat, instance, book.name, "Synthetic book", book.stat().st_size)
        adapter.resume_deliveries()
        status = service.ledger.delivery_status(receipt, principal.storage_key)
        assert status["status"] == "sent" and status["message_id"]
        delivered = rpc.get_message(bot, status["message_id"])
        assert delivered.show_padlock and delivered.file and not book.exists()

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir(mode=0o700)
        passphrase = "synthetic-test-only-not-a-production-secret"
        rpc.export_backup(bot, str(backup_dir), passphrase)
        backups = list(backup_dir.glob("*.tar"))
        assert len(backups) == 1
        restored = rpc.add_account()
        rpc.import_backup(restored, str(backups[0]), passphrase)
        restored_adapter = DeltaAdapter(rpc, restored, service, package)
        assert restored_adapter.own_fingerprint == adapter.own_fingerprint
        assert restored_adapter.principal(chat, encrypted=True).storage_key == principal.storage_key
        assert rpc.get_webxdc_info(restored, instance).name == "Michiflin AI"
        assert json.loads(rpc.get_webxdc_status_updates(restored, instance, 0)) == updates
