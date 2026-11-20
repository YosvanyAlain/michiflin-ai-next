from __future__ import annotations

import io
import zipfile
from dataclasses import replace
from unittest.mock import Mock

import pytest

from michiflin import downloads
from michiflin.config import Settings
from michiflin.errors import AppError
from michiflin.identity import Principal
from michiflin.service import Service


def epub() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
    return stream.getvalue()


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    client = Mock()
    client.get.return_value = {"id": 123, "copyright": False, "title": "Synthetic public-domain book",
                               "formats": {"application/epub+zip": "https://www.gutenberg.org/ebooks/123.epub"}}
    monkeypatch.setattr(downloads, "fetch_bytes", lambda _: epub())
    service = Service(Settings(state_dir=tmp_path), client)
    principal = Principal("synthetic@example.invalid", "A" * 40, 20, True, True)
    service.register_instance(principal, 42)
    return service, principal, client


def send(service, principal, identifier="download-request-0001"):
    return service.handle(principal, 42, {"id": identifier, "op": "library.deliver", "session": service.session,
                                         "args": {"book": 123, "format": "epub"}})


def test_verified_delivery_queues_once_and_never_exposes_filesystem_paths(prepared):
    service, principal, client = prepared
    result = send(service, principal)
    assert result["ok"] and result["data"]["status"] == "queued"
    assert send(service, principal) == result
    assert client.get.call_count == 1
    assert "filename" not in str(result) and str(service.settings.state_dir) not in str(result)
    receipt = result["data"]["receipt"]
    assert service.ledger.claim_delivery(receipt, "unrelated-owner") is None
    assert service.ledger.claim_delivery(receipt, principal.storage_key)
    assert service.ledger.claim_delivery(receipt, principal.storage_key) is None
    service.ledger.finish_delivery(receipt, 321)
    assert service.ledger.delivery_status(receipt, principal.storage_key)["status"] == "sent"
    with pytest.raises(AppError, match="invalid_delivery"):
        service.ledger.delivery_status(receipt, replace(principal, fingerprint="B" * 40).storage_key)


@pytest.mark.parametrize("metadata,error", [
    ({"copyright": True}, "book_rights_unverified"), ({"id": 456}, "book_rights_unverified"),
    ({"formats": {"application/epub+zip": "https://evil.invalid/book.epub"}}, "download_unavailable"),
    ({"formats": {"application/epub+zip": "http://www.gutenberg.org/book.epub"}}, "download_unavailable"),
])
def test_delivery_rechecks_rights_and_rejects_arbitrary_urls(prepared, metadata, error):
    service, principal, client = prepared
    client.get.return_value.update(metadata)
    assert send(service, principal)["error"] == error
    assert not list((service.settings.state_dir / "deliveries").glob("*"))


def test_restart_keeps_an_uncertain_delivery_from_automatic_resending(prepared):
    service, principal, _ = prepared
    receipt = send(service, principal)["data"]["receipt"]
    assert service.ledger.claim_delivery(receipt, principal.storage_key)
    service.ledger.recover_interrupted()
    assert service.ledger.delivery_status(receipt, principal.storage_key)["status"] == "uncertain"
    assert service.ledger.claim_delivery(receipt, principal.storage_key) is None


def test_delivery_quotas_are_independent_of_administrator_request_exemption(prepared):
    service, principal, _ = prepared
    for index in range(8):
        result = send(service, principal, f"download-request-{index:04}")
        assert result["ok"]
    assert send(service, principal, "download-request-9999")["error"] == "delivery_limit"


@pytest.mark.parametrize("data,format", [(b"<html>Not a book</html>", "txt"), (b"%PDF-x", "epub"),
                                         (b"bad", "pdf"), (b"bad", "kindle"), (b"\x00binary", "txt")])
def test_payload_format_is_verified_before_queueing(data, format):
    with pytest.raises(AppError, match="download_invalid"):
        downloads._validate(data, format)


def test_epub_payload_is_checked_without_extracting():
    downloads._validate(epub(), "epub")
    downloads._validate(b"A verified text ebook", "txt")
