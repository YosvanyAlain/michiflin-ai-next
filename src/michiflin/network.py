from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from urllib.parse import urlsplit

import requests

from .errors import AppError

SERVICE_HOSTS = frozenset({
    "gutendex.com", "openlibrary.org", "geocoding-api.open-meteo.com", "api.open-meteo.com",
})


class JsonClient:
    def __init__(self, *, contact: str = "", limit: int = 2_000_000) -> None:
        self.limit = limit
        self.contact = contact
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}

    def get(self, url: str, params: Mapping | None = None) -> dict:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except (ValueError, TypeError):
            raise AppError("source_not_allowed") from None
        if (parsed.scheme != "https" or parsed.hostname not in SERVICE_HOSTS
                or parsed.username or parsed.password or port not in (None, 443)):
            raise AppError("source_not_allowed")
        with self._lock:
            remaining = 1.05 - (time.monotonic() - self._last.get(parsed.hostname, 0))
            if remaining > 0:
                time.sleep(remaining)
            self._last[parsed.hostname] = time.monotonic()
        try:
            with requests.Session() as session:
                session.trust_env = False
                session.headers["User-Agent"] = "Michiflin/0.1" + (f" ({self.contact})" if self.contact else "")
                with session.get(url, params=params, timeout=(5, 18), stream=True, allow_redirects=False) as response:
                    if response.status_code == 429:
                        raise AppError("source_rate_limited", retryable=True)
                    if response.status_code != 200:
                        raise AppError("source_unavailable", retryable=True)
                    size, chunks, deadline = 0, [], time.monotonic() + 25
                    for chunk in response.iter_content(16384):
                        size += len(chunk)
                        if size > self.limit or time.monotonic() > deadline:
                            raise AppError("source_response_limit")
                        chunks.append(chunk)
                    data = json.loads(b"".join(chunks))
                    if not isinstance(data, dict):
                        raise AppError("source_response_invalid")
                    return data
        except (requests.RequestException, ValueError, UnicodeError):
            raise AppError("source_unavailable", retryable=True) from None
