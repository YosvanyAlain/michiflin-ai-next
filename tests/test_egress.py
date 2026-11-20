from __future__ import annotations

import io

import pytest
import requests

from michiflin import diagnostics, downloads
from michiflin.egress import configure_source_session
from michiflin.errors import AppError
from michiflin.network import JsonClient


@pytest.mark.parametrize("value", [
    "http://127.0.0.1:40000", "socks5h://127.0.0.1:40000", "socks5://localhost:40000",
    "socks5://192.168.1.1:40000", "socks5://proxy.example:40000", "socks5://127.0.0.1:80",
    "socks5://user:password@127.0.0.1:40000", "socks5://127.0.0.1:40000/path",
    "socks5://127.0.0.1:40000?x=y", "socks5://127.0.0.1:40000#fragment",
    "socks5://127.0.0.1:99999", "socks5://127.0.0.1:040000", "socks5://[::1:40000",
])
def test_proxy_configuration_rejects_unreviewed_routes_without_exposing_value(monkeypatch, value):
    monkeypatch.setenv("MICHIFLIN_SOURCE_PROXY", value)
    with requests.Session() as session, pytest.raises(AppError) as error:
        configure_source_session(session)
    assert str(error.value) == "source_proxy_invalid"
    assert not error.value.retryable


def test_ambient_proxy_settings_are_ignored(monkeypatch):
    monkeypatch.delenv("MICHIFLIN_SOURCE_PROXY", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.example:8080")
    with requests.Session() as session:
        configure_source_session(session)
        assert session.trust_env is False
        assert session.proxies == {}


@pytest.mark.parametrize("proxied", [False, True])
@pytest.mark.parametrize("kind", ["json", "download"])
@pytest.mark.parametrize("failure", [False, True])
def test_source_clients_use_only_selected_route_without_silent_fallback(monkeypatch, kind, proxied, failure):
    monkeypatch.setenv("MICHIFLIN_SOURCE_PROXY", "socks5://127.0.0.1:40000" if proxied else "")
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.example:8080")
    calls = []

    def send(_adapter, request, **kwargs):
        calls.append((request, kwargs))
        if failure:
            raise requests.ConnectionError("synthetic route failure")
        response = requests.Response()
        response.status_code = 200
        response.raw = io.BytesIO(b'{"id":1342}' if kind == "json" else b"public-domain content")
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    run = (lambda: JsonClient().get("https://gutendex.com/books/1342/")) if kind == "json" else (
        lambda: downloads.fetch_bytes("https://www.gutenberg.org/ebooks/1342.epub3.images")
    )
    if failure:
        with pytest.raises(AppError, match="^source_unavailable$"):
            run()
    else:
        assert run() == ({"id": 1342} if kind == "json" else b"public-domain content")
    assert len(calls) == 1
    assert dict(calls[0][1]["proxies"]) == ({"https": "socks5://127.0.0.1:40000"} if proxied else {})
    assert calls[0][1]["verify"] is True


@pytest.mark.parametrize("url", ["https://127.0.0.1/private", "https://untrusted.example/file"])
def test_proxy_does_not_expand_source_allowlists(monkeypatch, url):
    monkeypatch.setenv("MICHIFLIN_SOURCE_PROXY", "socks5://127.0.0.1:40000")
    for run in [lambda: JsonClient().get(url), lambda: downloads.fetch_bytes(url)]:
        with pytest.raises(AppError, match="^source_not_allowed$"):
            run()


def test_download_probe_requires_real_validated_bytes(monkeypatch):
    monkeypatch.setattr(JsonClient, "get", lambda *_: {
        "id": 1342, "copyright": False,
        "formats": {"application/epub+zip": "https://www.gutenberg.org/ebooks/1342.epub3.images"},
    })
    monkeypatch.setattr(downloads, "fetch_bytes", lambda _: b"not an epub")
    assert diagnostics.check_download() == {"ok": False, "error": "download_invalid"}


def test_download_probe_never_fetches_unverified_rights(monkeypatch):
    monkeypatch.setattr(JsonClient, "get", lambda *_: {"id": 1342, "copyright": True})
    monkeypatch.setattr(downloads, "fetch_bytes", lambda _: pytest.fail("must not fetch"))
    assert diagnostics.check_download() == {"ok": False, "error": "book_rights_unverified"}
