from __future__ import annotations

import contextlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import zlib
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from .egress import source_proxy
from .errors import AppError

MAX_BYTES = 1_000_000
BINARY_TYPES = frozenset({"application/gzip", "application/x-gzip", "application/octet-stream", "application/epub+zip", "application/rdf+xml",
                          "application/xml", "text/xml", "text/plain", "text/csv", "application/pdf",
                          "application/x-mobipocket-ebook"})
PAGE_TYPES = frozenset({"text/html", "application/xhtml+xml", "text/plain"})
_RESOLVER = """import json,socket,sys
print(json.dumps(sorted({x[4][0] for x in socket.getaddrinfo(sys.argv[1],443,type=socket.SOCK_STREAM)})))
"""


def public_address(value: str) -> bool:
    try:
        if "%" in value:
            return False
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address) and (
            address.ipv4_mapped or address.sixtofour or address.teredo
            or address not in ipaddress.IPv6Network("2000::/3")
        ):
            return False
        return address.is_global and not address.is_multicast
    except ValueError:
        return False


def normalize_url(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or re.search(r"[\s\x00-\x1f\x7f\\]", value):
        raise AppError("invalid_web_url")
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or not host or host.endswith(".")):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if (len(host) > 253 or "." not in host
                    or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split("."))
                    or host.endswith((".localhost", ".local", ".internal", ".invalid", ".test", ".example", ".onion"))):
                raise ValueError from None
        else:
            if not public_address(str(address)):
                raise AppError("web_destination_blocked")
            host = f"[{address}]" if address.version == 6 else str(address)
        path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parsed.query, safe="%/?@:!$&'()*+,;=-._~")
        return urlunsplit(("https", host, path, query, ""))
    except (ValueError, UnicodeError):
        raise AppError("invalid_web_url") from None


def resolve_public(host: str, timeout: float) -> list[str]:
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", _RESOLVER, host], env={"LANG": "C.UTF-8"},
            capture_output=True, timeout=min(5, timeout), check=True,
        )
        if len(result.stdout) > 8192:
            raise ValueError
        addresses = json.loads(result.stdout)
        if not isinstance(addresses, list) or not 1 <= len(addresses) <= 64:
            raise ValueError
    except (subprocess.SubprocessError, OSError, ValueError):
        raise AppError("web_source_unavailable", retryable=True) from None
    if not all(isinstance(address, str) and public_address(address) for address in addresses):
        raise AppError("web_destination_blocked")
    return addresses


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, proxy: str | None, timeout: float) -> None:
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self.address, self.proxy = address, proxy
        self.wire: socket.socket | None = None
        self.aborted = False

    def connect(self) -> None:
        connect_deadline = time.monotonic() + self.timeout
        if self.proxy:
            import socks

            sock = socks.create_connection(
                (self.address, 443), timeout=self.timeout, proxy_type=socks.SOCKS5,
                proxy_addr="127.0.0.1", proxy_port=urlsplit(self.proxy).port, proxy_rdns=False,
            )
        else:
            family = socket.AF_INET6 if ":" in self.address else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_STREAM)
            self.wire = sock
            sock.settimeout(self.timeout)
            try:
                sock.connect((self.address, 443))
            except BaseException:
                sock.close()
                raise
        self.wire = sock
        try:
            remaining = connect_deadline - time.monotonic()
            if self.aborted or remaining <= 0:
                raise TimeoutError
            sock.settimeout(remaining)
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host, do_handshake_on_connect=False)
            self.wire = self.sock
            if self.aborted:
                raise TimeoutError
            self.sock.do_handshake()
            self.sock.settimeout(self.timeout)
        except BaseException:
            self.abort()
            raise

    def abort(self) -> None:
        self.aborted = True
        if self.wire:
            with contextlib.suppress(OSError):
                self.wire.shutdown(socket.SHUT_RDWR)
            if self.sock is None:
                self.wire.close()
        self.close()


@dataclass(frozen=True)
class Document:
    url: str
    content_type: str
    body: bytes


class PublicHttp:
    """Cookie-free HTTPS reads pinned to vetted public addresses, including through SOCKS."""

    def __init__(self, *, limit: int = MAX_BYTES, seconds: float = 25,
                 hosts: frozenset[str] | None = None) -> None:
        if type(limit) is not int or not 1 <= limit <= 32 * 1024 * 1024 or not 1 <= seconds <= 25:
            raise ValueError("Invalid server-side public transport budget")
        self.limit, self.seconds, self.hosts = limit, seconds, hosts

    def get(self, url: object, *, json_response: bool = False, binary_response: bool = False) -> Document:
        url = normalize_url(url)
        if json_response and binary_response:
            raise ValueError("A single response mode is required")
        accepted_types = {"application/json"} if json_response else BINARY_TYPES if binary_response else PAGE_TYPES
        proxy = source_proxy()
        deadline = time.monotonic() + self.seconds
        visited = set()
        try:
            for _ in range(5):
                if url in visited or time.monotonic() >= deadline:
                    raise AppError("web_response_limit")
                visited.add(url)
                parsed = urlsplit(url)
                if self.hosts is not None and parsed.hostname not in self.hosts:
                    raise AppError("web_destination_blocked")
                addresses = resolve_public(parsed.hostname, max(.1, deadline - time.monotonic()))
                if time.monotonic() >= deadline:
                    raise AppError("web_response_limit")
                connection = _PinnedHTTPS(parsed.hostname, addresses[0], proxy, min(5, deadline - time.monotonic()))
                timer = threading.Timer(max(.01, deadline - time.monotonic()), connection.abort)
                timer.daemon = True
                timer.start()
                try:
                    connection.request("GET", urlunsplit(("", "", parsed.path, parsed.query, "")), headers={
                        "User-Agent": "Michiflin/0.1 (user-requested public sources)",
                        "Accept": "application/json" if json_response else "*/*" if binary_response else "text/html,application/xhtml+xml,text/plain;q=0.9",
                        "Accept-Encoding": "gzip", "Connection": "close",
                    })
                    with connection.getresponse() as response:
                        if response.status in {301, 302, 303, 307, 308}:
                            location = response.getheader("Location", "")
                            if not location:
                                raise AppError("web_source_unavailable")
                            url = normalize_url(urljoin(url, location))
                            continue
                        if response.status == 429:
                            raise AppError("source_rate_limited", retryable=True)
                        if response.status in {401, 403, 451}:
                            raise AppError("web_access_restricted")
                        if response.status != 200:
                            raise AppError("web_source_unavailable", retryable=True)
                        content_type = response.getheader("Content-Type", "").strip().lower()
                        if content_type.split(";", 1)[0].strip() not in accepted_types:
                            raise AppError("web_format_unsupported")
                        length = response.getheader("Content-Length")
                        if length is not None and (not length.isdecimal() or int(length) > self.limit):
                            raise AppError("web_response_limit")
                        encoding = response.getheader("Content-Encoding", "identity").lower().strip()
                        if encoding not in {"identity", "gzip"}:
                            raise AppError("web_format_unsupported")
                        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
                        body, wire_size = bytearray(), 0
                        while chunk := response.read1(16384):
                            wire_size += len(chunk)
                            if wire_size > self.limit or time.monotonic() >= deadline:
                                raise AppError("web_response_limit")
                            body.extend(decoder.decompress(chunk, self.limit + 1 - len(body)) if decoder else chunk)
                            if len(body) > self.limit or decoder and decoder.unconsumed_tail:
                                raise AppError("web_response_limit")
                        if time.monotonic() >= deadline:
                            raise AppError("web_response_limit")
                        if length is not None and wire_size != int(length):
                            raise AppError("web_content_invalid")
                        if decoder and (not decoder.eof or decoder.unused_data):
                            raise AppError("web_content_invalid")
                        if not body:
                            raise AppError("web_content_invalid")
                        return Document(url, content_type, bytes(body))
                finally:
                    timer.cancel()
                    connection.abort()
            raise AppError("web_response_limit")
        except (OSError, http.client.HTTPException, zlib.error, ValueError):
            raise AppError("web_source_unavailable", retryable=True) from None
