from __future__ import annotations

import os
from urllib.parse import urlsplit

import requests

from .errors import AppError


def source_proxy() -> str | None:
    """Accept only the reviewed, explicitly selected local SOCKS endpoint."""
    value = os.environ.get("MICHIFLIN_SOURCE_PROXY", "")
    if not value:
        return
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "socks5"
            and parsed.hostname == "127.0.0.1"
            and parsed.port is not None
            and 1024 <= parsed.port <= 65535
            and parsed.username is None
            and parsed.password is None
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
            and value == f"socks5://127.0.0.1:{parsed.port}"
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise AppError("source_proxy_invalid")
    return value


def configure_source_session(session: requests.Session) -> None:
    """Route only explicitly opted-in, allowlisted source clients through local SOCKS."""
    session.trust_env = False
    if value := source_proxy():
        session.proxies.update({"https": value})
