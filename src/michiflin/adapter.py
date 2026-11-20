from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from pathlib import Path

from .errors import AppError
from .identity import Principal
from .protocol import decode_request, encode_response
from .service import Service


def key_fingerprint(vcard: str) -> str:
    """Hash the exported primary OpenPGP packet, never human-readable identity text."""
    if not isinstance(vcard, str) or len(vcard) > 100000:
        raise AppError("identity_unavailable")
    unfolded = re.sub(r"\r?\n[ \t]", "", vcard)
    keys = [line[4:] for line in unfolded.splitlines() if line.startswith("KEY:")]
    prefix = "data:application/pgp-keys;base64,"
    if len(keys) != 1 or not keys[0].replace("\\,", ",").startswith(prefix):
        raise AppError("identity_unavailable")
    try:
        packet = base64.b64decode(keys[0].replace("\\,", ",")[len(prefix):], validate=True)
        if len(packet) < 3 or packet[0] != 0xC6:
            raise ValueError("Expected a new-format primary public-key packet")
        length, offset = packet[1], 2
        if 192 <= length <= 223:
            length, offset = ((length - 192) << 8) + packet[2] + 192, 3
        elif length == 255:
            length, offset = int.from_bytes(packet[2:6], "big"), 6
        elif length >= 224:
            raise ValueError("Partial primary keys are unsupported")
        body = packet[offset:offset + length]
        if len(body) != length or not 6 <= length <= 65535 or body[0] != 4:
            raise ValueError("Expected a complete version-four public key")
        return hashlib.sha1(b"\x99" + length.to_bytes(2, "big") + body, usedforsecurity=False).hexdigest().upper()
    except (ValueError, IndexError, binascii.Error):
        raise AppError("identity_unavailable") from None


class DeltaAdapter:
    def __init__(self, rpc, account: int, service: Service, package: Path) -> None:
        self.rpc = rpc
        self.account = account
        self.service = service
        self.package = package.resolve()
        self.own_fingerprint = key_fingerprint(rpc.make_vcard(account, [1]))

    def principal(self, chat_id: int, *, encrypted: bool) -> Principal:
        chat = self.rpc.get_full_chat_by_id(self.account, chat_id)
        kind = getattr(chat.chat_type, "value", chat.chat_type)
        if kind != "Single" or not chat.is_encrypted or not encrypted:
            raise AppError("encrypted_private_chat_required")
        contacts = [identifier for identifier in self.rpc.get_chat_contacts(self.account, chat_id) if identifier > 9]
        if len(contacts) != 1:
            raise AppError("encrypted_private_chat_required")
        contact = self.rpc.get_contact(self.account, contacts[0])
        if not contact.is_key_contact or not contact.e2ee_avail or contact.is_blocked:
            raise AppError("identity_unavailable")
        fingerprint = key_fingerprint(self.rpc.make_vcard(self.account, [contact.id]))
        if fingerprint == self.own_fingerprint:
            raise AppError("identity_unavailable")
        return Principal(contact.address, fingerprint, chat_id, True, True, contact.is_verified)

    def new_message(self, message) -> None:
        from deltachat2 import MessageData

        if message.is_info or message.is_forwarded or message.from_id <= 9:
            return
        words = (message.text or "").split(maxsplit=1)
        if not words or words[0].lower() not in {"/start", "/app", "/help", "/inicio", "/ayuda"}:
            return
        principal = self.principal(message.chat_id, encrypted=message.show_padlock)
        contact_ids = self.rpc.get_chat_contacts(self.account, message.chat_id)
        if message.from_id not in contact_ids:
            raise AppError("identity_unavailable")
        principal.require_access(self.service.settings)
        if not self.service.ledger.allow_delivery(principal.storage_key):
            return
        self.rpc.accept_chat(self.account, message.chat_id)
        instance = self.rpc.send_msg(self.account, message.chat_id, MessageData(
            file=str(self.package), text="Michiflin AI · English / Español"))
        # Bind only instances issued here. Forwarded or uploaded copies cannot acquire an owner.
        self.service.register_instance(principal, instance)

    def status_update(self, instance: int) -> None:
        if not self.service.ledger.has_instance(instance):
            return
        message = self.rpc.get_message(self.account, instance)
        if message.from_id != 1 or message.is_forwarded:
            return
        principal = self.principal(message.chat_id, encrypted=message.show_padlock)
        self.service.ledger.authorize(instance, principal.storage_key, principal.chat_id)
        cursor = self.service.ledger.cursor(instance)
        # Core returns all newer updates; revoke oversized streams instead of rereading forever.
        raw = self.rpc.get_webxdc_status_updates(self.account, instance, cursor)
        if not isinstance(raw, str) or len(raw) > 8_000_000:
            self.service.ledger.revoke(instance)
            raise AppError("invalid_update_batch")
        updates = json.loads(raw)
        if not isinstance(updates, list) or len(updates) > 1000:
            self.service.ledger.revoke(instance)
            raise AppError("invalid_update_batch")
        for update in updates:
            if not isinstance(update, dict):
                raise AppError("invalid_update_batch")
            serial = update.get("serial")
            if type(serial) is not int or serial <= cursor:
                continue
            payload = update.get("payload")
            if isinstance(payload, dict) and payload.get("type") == "michiflin.request":
                try:
                    request = decode_request(payload)
                except AppError:
                    self.service.ledger.checkpoint(instance, serial)
                    cursor = serial
                    continue
                response = self.service.handle(principal, instance, request)
                if request.get("op") == "library.deliver" and response.get("ok"):
                    receipt = response["data"]["receipt"]
                    self.deliver(receipt, principal, instance)
                    response = {**response, "data": {**response["data"], **self.service.ledger.delivery_status(receipt, principal.storage_key)}}
                for frame in encode_response(response):
                    self.rpc.send_webxdc_status_update(
                        self.account, instance, json.dumps({"payload": frame}, allow_nan=False), "")
            self.service.ledger.checkpoint(instance, serial)
            cursor = serial

    def deliver(self, receipt: str, principal: Principal, instance: int) -> None:
        from deltachat2 import MessageData

        row = self.service.ledger.claim_delivery(receipt, principal.storage_key)
        if row is None:
            return
        try:
            current = self.principal(principal.chat_id, encrypted=True)
            if current.storage_key != principal.storage_key or row["instance"] != instance or row["chat"] != principal.chat_id:
                raise AppError("identity_unavailable")
            directory = (self.service.settings.state_dir / "deliveries").resolve()
            file = directory / row["filename"]
            if file.is_symlink() or not file.is_file() or file.resolve().parent != directory or file.stat().st_size != row["bytes"]:
                raise AppError("download_invalid")
            message_id = self.rpc.send_msg(self.account, principal.chat_id, MessageData(
                file=str(file), filename="Michiflin-book" + file.suffix,
                text=row["title"] + "\nProject Gutenberg · Public domain in the USA; check local rights."))
        except Exception:
            self.service.ledger.finish_delivery(receipt, None)
            return
        self.service.ledger.finish_delivery(receipt, message_id)
        file.unlink(missing_ok=True)

    def resume_deliveries(self) -> None:
        for row in self.service.ledger.queued_deliveries():
            try:
                message = self.rpc.get_message(self.account, row["instance"])
                if message.from_id != 1 or message.is_forwarded:
                    raise AppError("identity_unavailable")
                principal = self.principal(message.chat_id, encrypted=message.show_padlock)
                self.service.ledger.authorize(row["instance"], principal.storage_key, principal.chat_id)
                if principal.storage_key != row["owner"] or principal.chat_id != row["chat"]:
                    raise AppError("identity_unavailable")
                self.deliver(row["receipt"], principal, row["instance"])
            except Exception:
                self.service.ledger.abandon_delivery(row["receipt"])
