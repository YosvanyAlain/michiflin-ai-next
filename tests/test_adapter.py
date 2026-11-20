from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

from michiflin.adapter import DeltaAdapter, key_fingerprint
from michiflin.config import Settings
from michiflin.errors import AppError
from michiflin.service import Operation, Service


def synthetic_card(seed: int) -> str:
    packet = bytes([0xC6, 8, 4, 0, 0, 0, seed, 22, 0, 0])
    return "BEGIN:VCARD\nVERSION:4.0\nFN:Synthetic\nKEY:data:application/pgp-keys;base64\\," + base64.b64encode(packet).decode() + "\nEND:VCARD"


OWN = key_fingerprint(synthetic_card(1))
PEER = key_fingerprint(synthetic_card(2))


class FakeRpc:
    def __init__(self):
        self.chat = SimpleNamespace(chat_type="Single", is_encrypted=True)
        self.contact = SimpleNamespace(id=10, address="reader@example.invalid", is_key_contact=True,
                                       e2ee_avail=True, is_blocked=False, is_verified=False)
        self.message = SimpleNamespace(chat_id=20, from_id=1, is_forwarded=False, show_padlock=True)
        self.updates = []
        self.sent = []
        self.issued = []
        self.card = synthetic_card(2)

    def make_vcard(self, _account, contacts):
        return synthetic_card(1) if contacts == [1] else self.card

    def get_full_chat_by_id(self, *_):
        return self.chat

    def get_chat_contacts(self, *_):
        return [1, 10]

    def get_contact(self, *_):
        return self.contact

    def get_message(self, *_):
        return self.message

    def get_webxdc_status_updates(self, _account, _instance, cursor):
        return json.dumps([update for update in self.updates if update["serial"] > cursor])

    def send_webxdc_status_update(self, _account, instance, update, _description):
        self.sent.append((instance, json.loads(update)["payload"]))

    def accept_chat(self, *_):
        pass

    def send_msg(self, _account, _chat, data):
        self.issued.append(data)
        return 42


@pytest.fixture
def connected(tmp_path):
    rpc = FakeRpc()
    service = Service(Settings(state_dir=tmp_path))
    adapter = DeltaAdapter(rpc, 1, service, tmp_path / "synthetic.xdc")
    principal = adapter.principal(20, encrypted=True)
    service.register_instance(principal, 42)
    return adapter, rpc, service


def test_fingerprint_uses_public_key_not_display_name():
    card = synthetic_card(2)
    assert key_fingerprint(card.replace("FN:Synthetic", "FN:" + OWN)) == PEER
    assert key_fingerprint(card.replace("FN:Synthetic", "FN:" + "C" * 40)) == PEER


@pytest.mark.parametrize("card", ["", "KEY:untrusted", synthetic_card(1).replace("KEY:", "OTHER:"),
                                  synthetic_card(1) + "\n" + synthetic_card(2), "KEY:data:application/pgp-keys;base64,AAAA"])
def test_malformed_or_ambiguous_exported_keys_fail_closed(card):
    with pytest.raises(AppError, match="identity_unavailable"):
        key_fingerprint(card)


def test_bound_wire_request_uses_service_and_durable_cursor(connected):
    adapter, rpc, service = connected
    calls = []
    service.operations["synthetic.test"] = Operation(lambda *_: calls.append(1) or {"value": 391})
    payload = {"type": "michiflin.request", "protocol": 1, "id": "synthetic-123456", "op": "synthetic.test",
               "session": service.session, "args": {}}
    rpc.updates = [{"serial": 1, "payload": payload}]
    adapter.status_update(42)
    assert rpc.sent[0][1]["body"]["data"]["value"] == 391
    assert service.ledger.cursor(42) == 1
    adapter.status_update(42)
    assert calls == [1] and len(rpc.sent) == 1
    rpc.updates.append({"serial": 2, "payload": payload})
    adapter.status_update(42)
    assert calls == [1] and len(rpc.sent) == 2


def test_uploaded_or_forwarded_instance_cannot_bind_itself(connected):
    adapter, rpc, service = connected
    adapter.status_update(999)
    assert not rpc.sent and not service.ledger.has_instance(999)
    rpc.message.is_forwarded = True
    adapter.status_update(42)
    assert not rpc.sent


@pytest.mark.parametrize("change", ["plaintext", "group", "key_change", "blocked"])
def test_instance_identity_and_chat_guards(connected, change):
    adapter, rpc, _ = connected
    if change == "plaintext":
        rpc.message.show_padlock = False
    elif change == "group":
        rpc.chat.chat_type = "Group"
    elif change == "key_change":
        rpc.card = synthetic_card(3)
    else:
        rpc.contact.is_blocked = True
    with pytest.raises(AppError):
        adapter.status_update(42)
    assert not rpc.sent


def test_failed_delivery_does_not_checkpoint_and_replay_does_not_duplicate(connected):
    adapter, rpc, service = connected
    rpc.updates = [{"serial": 1, "payload": {"type": "michiflin.request", "protocol": 1,
                   "id": "synthetic-123456", "op": "notes.add", "args": {"text": "Do not duplicate"},
                   "session": service.session}}]
    original = rpc.send_webxdc_status_update
    rpc.send_webxdc_status_update = lambda *_: (_ for _ in ()).throw(RuntimeError("synthetic delivery failure"))
    with pytest.raises(RuntimeError):
        adapter.status_update(42)
    assert service.ledger.cursor(42) == 0
    rpc.send_webxdc_status_update = original
    adapter.status_update(42)
    assert len(service.ledger.notes(adapter.principal(20, encrypted=True).storage_key)) == 1


def test_constructing_another_service_does_not_corrupt_active_work(connected):
    _, _, service = connected
    principal = principal_key()
    service.ledger.reserve(42, "synthetic-123456", principal, "notes.list", {}, 40, 3)
    other = Service(service.settings)
    with pytest.raises(AppError, match="request_pending"):
        other.ledger.reserve(42, "synthetic-123456", principal, "notes.list", {}, 40, 3)
    other.ledger.recover_interrupted()
    with pytest.raises(AppError, match="previous_outcome_uncertain"):
        other.ledger.reserve(42, "synthetic-123456", principal, "notes.list", {}, 40, 3)


def principal_key():
    from michiflin.identity import Principal
    return Principal("reader@example.invalid", PEER, 20, True, True).storage_key
