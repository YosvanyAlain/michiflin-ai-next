from __future__ import annotations

import gzip
import json
import socket
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from michiflin import public_http as web
from michiflin.errors import AppError

PUBLIC = "93.184.216.34"


@pytest.mark.parametrize("address", [
    "127.0.0.1", "0.0.0.0", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
    "100.64.0.1", "224.0.0.1", "255.255.255.255", "192.0.2.1", "198.18.0.1", "::1", "::",
    "fc00::1", "fe80::1", "ff02::1", "::ffff:127.0.0.1", "2002:7f00:1::", "64:ff9b::7f00:1",
    "64:ff9b:1::a00:1", "2001:db8::1", "not-an-address",
])
def test_private_reserved_transition_and_metadata_addresses_are_not_public(address):
    assert not web.public_address(address)


@pytest.mark.parametrize("address", [PUBLIC, "1.1.1.1", "2606:4700:4700::1111"])
def test_public_unicast_addresses(address):
    assert web.public_address(address)


@pytest.mark.parametrize("url", [
    "http://example.com", "file:///etc/passwd", "https://user:password@example.com/",
    "https://example.com:8080", "https://localhost/", "https://service.internal/", "https://example.com./",
    "https://example.com\\@127.0.0.1/", "https://example.com/\r\nInjected:yes", "https://[::1]/",
    "https://127.0.0.1", "https://169.254.169.254", "https://[::ffff:127.0.0.1]", "https://2130706433",
    "https://example.com:bad", "https://-bad.example.com/", "https://.example.com/", None, 42, "x" * 2049,
])
def test_urls_cannot_inject_credentials_headers_private_addresses_or_non_https_schemes(url):
    with pytest.raises(AppError):
        web.normalize_url(url)


def test_normalization_preserves_query_encodes_unicode_and_discards_fragment():
    assert web.normalize_url("https://EXAMPLE.com:443/niño?q=🐈#part") == "https://example.com/ni%C3%B1o?q=%F0%9F%90%88"
    assert web.normalize_url("https://[2606:4700:4700::1111]/") == "https://[2606:4700:4700::1111]/"


@pytest.mark.parametrize("addresses", [["127.0.0.1"], [PUBLIC, "10.0.0.1"], ["::ffff:10.1.1.1"], [], [42]])
def test_every_dns_answer_is_checked_before_connection(monkeypatch, addresses):
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: SimpleNamespace(stdout=json.dumps(addresses).encode()))
    with pytest.raises(AppError):
        web.resolve_public("example.com", 3)


def test_dns_resolution_is_time_bounded_and_receives_no_runtime_secrets(monkeypatch):
    monkeypatch.setenv("SYNTHETIC_PRIVATE_KEY", "must-not-enter-resolver")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=json.dumps([PUBLIC]).encode())

    monkeypatch.setattr(subprocess, "run", run)
    assert web.resolve_public("example.com", 2) == [PUBLIC]
    command, kwargs = calls[0]
    assert command[1:3] == ["-I", "-c"] and command[-1] == "example.com"
    assert kwargs["timeout"] == 2 and kwargs["env"] == {"LANG": "C.UTF-8"}
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired(command, 2)))
    with pytest.raises(AppError, match="web_source_unavailable"):
        web.resolve_public("example.com", 2)


@pytest.mark.parametrize("proxy", [None, "socks5://127.0.0.1:40000"])
def test_connection_pins_numeric_address_but_keeps_original_tls_hostname(monkeypatch, proxy):
    import socks

    raw, secured = Mock(), Mock()
    context = Mock()
    context.wrap_socket.return_value = secured
    monkeypatch.setattr(web.ssl, "create_default_context", lambda: context)
    factory = Mock(return_value=raw)
    monkeypatch.setattr(socket, "socket", factory)
    monkeypatch.setattr(socks, "create_connection", factory)
    connection = web._PinnedHTTPS("example.com", PUBLIC, proxy, 3)
    connection.connect()
    context.wrap_socket.assert_called_once_with(raw, server_hostname="example.com", do_handshake_on_connect=False)
    secured.do_handshake.assert_called_once_with()
    if proxy:
        assert factory.call_args.args == ((PUBLIC, 443),)
        assert factory.call_args.kwargs["proxy_rdns"] is False
        assert factory.call_args.kwargs["proxy_addr"] == "127.0.0.1"
    else:
        raw.connect.assert_called_once_with((PUBLIC, 443))
    connection.abort()
    secured.shutdown.assert_called_once_with(socket.SHUT_RDWR)
    secured.close.assert_called_once()


class Response:
    def __init__(self, body=b"public page", status=200, **headers):
        self.body, self.status = body, status
        self.headers = {"Content-Type": "text/html; charset=utf-8", **headers}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read1(self, size):
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.delenv("MICHIFLIN_SOURCE_PROXY", raising=False)
    responses, calls, resolutions = [], [], []

    class Connection:
        def __init__(self, host, address, proxy, timeout):
            self.call = {"host": host, "address": address, "proxy": proxy, "timeout": timeout}
            calls.append(self.call)

        def request(self, method, path, headers):
            self.call.update(method=method, path=path, headers=headers)

        def getresponse(self):
            return responses.pop(0)

        def abort(self):
            self.call["closed"] = True

    def resolve(host, _timeout):
        resolutions.append(host)
        return [PUBLIC]

    monkeypatch.setattr(web, "_PinnedHTTPS", Connection)
    monkeypatch.setattr(web, "resolve_public", resolve)
    return responses, calls, resolutions


def test_redirects_are_revalidated_and_no_cookies_or_authorization_are_forwarded(transport, monkeypatch):
    responses, calls, resolutions = transport
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.example:1234")
    responses.extend([
        Response(status=302, Location="https://second.example.org/story", **{"Set-Cookie": "private=value"}),
        Response(body=b"a public story"),
    ])
    result = web.PublicHttp().get("https://first.example.com/start")
    assert result.url == "https://second.example.org/story" and result.body == b"a public story"
    assert resolutions == ["first.example.com", "second.example.org"]
    assert all(c["address"] == PUBLIC and c["closed"] and c["proxy"] is None for c in calls)
    assert all("Cookie" not in c["headers"] and "Authorization" not in c["headers"] for c in calls)


def test_redirect_to_private_network_never_connects(transport):
    responses, calls, _ = transport
    responses.append(Response(status=302, Location="https://169.254.169.254/latest/meta-data/"))
    with pytest.raises(AppError, match="web_destination_blocked"):
        web.PublicHttp().get("https://example.com/")
    assert len(calls) == 1 and calls[0]["closed"]


def test_dns_rebinding_on_redirect_is_blocked_before_second_connection(transport, monkeypatch):
    responses, calls, _ = transport
    responses.append(Response(status=302, Location="/second"))
    answers = iter([[PUBLIC], ["127.0.0.1"]])
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: SimpleNamespace(stdout=json.dumps(next(answers)).encode()))
    monkeypatch.setattr(web, "resolve_public", ORIGINAL_RESOLVE)
    with pytest.raises(AppError, match="web_destination_blocked"):
        web.PublicHttp().get("https://example.com/")
    assert len(calls) == 1


ORIGINAL_RESOLVE = web.resolve_public


def test_warp_uses_only_the_explicit_validated_proxy_without_fallback(transport, monkeypatch):
    responses, calls, _ = transport
    monkeypatch.setenv("MICHIFLIN_SOURCE_PROXY", "socks5://127.0.0.1:40000")
    responses.append(Response())
    web.PublicHttp().get("https://example.com/")
    assert calls[0]["proxy"] == "socks5://127.0.0.1:40000"
    monkeypatch.setattr(web._PinnedHTTPS, "request", Mock(side_effect=OSError("synthetic proxy failure")))
    with pytest.raises(AppError, match="web_source_unavailable"):
        web.PublicHttp().get("https://example.com/")
    assert len(calls) == 2


@pytest.mark.parametrize("response,error", [
    (Response(status=403), "web_access_restricted"), (Response(status=429), "source_rate_limited"),
    (Response(status=500), "web_source_unavailable"),
    (Response(**{"Content-Type": "application/pdf"}), "web_format_unsupported"),
    (Response(**{"Content-Length": "1000001"}), "web_response_limit"),
    (Response(**{"Content-Length": "12"}), "web_content_invalid"),
    (Response(**{"Content-Encoding": "br"}), "web_format_unsupported"),
    (Response(body=b""), "web_content_invalid"),
    (Response(body=b"x" * 1_000_001), "web_response_limit"),
    (Response(body=gzip.compress(b"x" * 1_000_001), **{"Content-Encoding": "gzip"}), "web_response_limit"),
    (Response(body=gzip.compress(b"test")[:-2], **{"Content-Encoding": "gzip"}), "web_content_invalid"),
])
def test_limits_access_controls_and_unsupported_formats_fail_closed(transport, response, error):
    responses, calls, _ = transport
    responses.append(response)
    with pytest.raises(AppError, match=error):
        web.PublicHttp().get("https://example.com/")
    assert calls[0]["closed"]


def test_gzip_is_bounded_decoded_and_validated(transport):
    responses, _, _ = transport
    raw = gzip.compress(b"readable public page" * 100)
    responses.append(Response(body=raw, **{"Content-Encoding": "gzip", "Content-Length": str(len(raw))}))
    assert web.PublicHttp().get("https://example.com/").body == b"readable public page" * 100


def test_redirect_loop_stops_before_repeated_fetch(transport):
    responses, calls, _ = transport
    responses.append(Response(status=302, Location="/"))
    with pytest.raises(AppError, match="web_response_limit"):
        web.PublicHttp().get("https://example.com/")
    assert len(calls) == 1


def test_total_deadline_is_checked_even_if_a_partial_stream_ends_cleanly(transport, monkeypatch):
    responses, calls, _ = transport
    clock = [0.0]
    monkeypatch.setattr(web.time, 'monotonic', lambda: clock[0])

    class Slow(Response):
        def read1(self, size):
            if not self.body:
                clock[0] = 26.0
            return super().read1(size)

    responses.append(Slow())
    with pytest.raises(AppError, match='web_response_limit'):
        web.PublicHttp().get('https://example.com/')
    assert calls[0]['closed']


def test_timeout_during_proxy_connect_closes_socket_before_tls(monkeypatch):
    import socks

    raw, context = Mock(), Mock()
    monkeypatch.setattr(web.ssl, 'create_default_context', lambda: context)
    clock = [0.0]
    monkeypatch.setattr(web.time, 'monotonic', lambda: clock[0])

    def connect(*_a, **_k):
        clock[0] = 6.0
        return raw

    monkeypatch.setattr(socks, 'create_connection', connect)
    connection = web._PinnedHTTPS('example.com', PUBLIC, 'socks5://127.0.0.1:40000', 5)
    with pytest.raises(TimeoutError):
        connection.connect()
    context.wrap_socket.assert_not_called()
    raw.close.assert_called_once()


def test_deadline_abort_during_tls_handshake_shuts_down_the_live_tls_socket(monkeypatch):
    raw, secured, context = Mock(), Mock(), Mock()
    context.wrap_socket.return_value = secured
    monkeypatch.setattr(web.ssl, 'create_default_context', lambda: context)
    monkeypatch.setattr(socket, 'socket', Mock(return_value=raw))
    connection = web._PinnedHTTPS('example.com', PUBLIC, None, 5)

    def handshake():
        connection.abort()
        raise TimeoutError

    secured.do_handshake.side_effect = handshake
    with pytest.raises(TimeoutError):
        connection.connect()
    assert connection.aborted
    secured.shutdown.assert_called_with(socket.SHUT_RDWR)
