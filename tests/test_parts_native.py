import hashlib
import shutil
from pathlib import Path

import pytest

from michiflin.package import build
from michiflin.parts import join, split

core = pytest.importorskip("deltachat2")
pytestmark = pytest.mark.skipif(not shutil.which("deltachat-rpc-server"), reason="Native Delta Chat core is not installed")


def test_reconstructed_webxdc_is_recognized_by_native_core_without_mail_io(tmp_path):
    """Local native format check, not an encrypted receive or remote delivery test."""
    original = build(Path(__file__).resolve().parents[1] / "miniapp", tmp_path / "packages")
    expected = hashlib.sha256(original.read_bytes()).hexdigest()
    manifest = split(original, tmp_path / "parts", part_bytes=32000)
    reconstructed = tmp_path / "reconstructed.xdc"
    assert len(manifest["parts"]) > 1
    assert join(tmp_path / "parts", reconstructed, expected_sha256=expected)["verified"]
    assert reconstructed.read_bytes() == original.read_bytes()
    with core.IOTransport(accounts_dir=str(tmp_path / "accounts")) as transport:
        rpc = core.Rpc(transport)
        sender, receiver = rpc.add_account(), rpc.add_account()
        for account, address in ((sender, "sender@example.invalid"), (receiver, "receiver@example.invalid")):
            for key, value in (("addr", address), ("configured_addr", address), ("configured", "1")):
                rpc.set_config(account, key, value)
            rpc.get_chat_securejoin_qr_code(account, None)
        contacts = rpc.import_vcard_contents(sender, rpc.make_vcard(receiver, [1]))
        chat = rpc.create_chat_by_contact_id(sender, contacts[0])
        message = rpc.send_msg(sender, chat, core.MessageData(file=str(reconstructed), text="Synthetic reconstructed package"))
        assert rpc.get_message(sender, message).show_padlock
        assert rpc.get_webxdc_info(sender, message).name == "Michiflin AI"
        # No start_io call: nothing is sent to an external provider or a real user.
