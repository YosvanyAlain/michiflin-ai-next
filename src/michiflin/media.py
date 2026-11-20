from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlencode, urlsplit

from .errors import AppError
from .public_http import PublicHttp, normalize_url

_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"})


def youtube_url(value: object) -> str:
    try:
        url = normalize_url(value)
        parsed = urlsplit(url)
        host, path = parsed.hostname, parsed.path.rstrip("/")
        identifier = ""
        if host == "youtu.be" and path.count("/") == 1:
            identifier = path[1:]
        elif host in _HOSTS and path == "/watch":
            values = parse_qs(parsed.query, max_num_fields=20).get("v", [])
            if len(values) == 1:
                identifier = values[0]
        elif host in _HOSTS or host == "www.youtube-nocookie.com":
            parts = path.split("/")
            allowed = {"embed"} if host == "www.youtube-nocookie.com" else {"shorts", "live", "embed"}
            if len(parts) == 3 and parts[1] in allowed:
                identifier = parts[2]
        if not re.fullmatch(r"[a-zA-Z0-9_-]{11}", identifier):
            raise ValueError
        return "https://www.youtube.com/watch?v=" + identifier
    except (AppError, ValueError):
        raise AppError("invalid_youtube_url") from None


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        raise AppError("media_metadata_invalid")
    text = re.sub(r"[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]", " ", value)
    text = " ".join(text.split())[:limit]
    if not text:
        raise AppError("media_metadata_invalid")
    return text


class Media:
    def __init__(self, client: PublicHttp | None = None) -> None:
        self.client = client or PublicHttp()

    def inspect(self, args: dict) -> dict:
        url = youtube_url(args.get("url"))
        endpoint = "https://www.youtube.com/oembed?" + urlencode({"url": url, "format": "json"})
        try:
            document = self.client.get(endpoint, json_response=True)
        except AppError as error:
            if error.code in {"web_source_unavailable", "web_access_restricted"}:
                raise AppError("media_source_unavailable", retryable=error.retryable) from None
            if error.code in {"web_format_unsupported", "web_content_invalid"}:
                raise AppError("media_metadata_invalid") from None
            raise
        try:
            final = urlsplit(document.url)
            if final.hostname not in {"youtube.com", "www.youtube.com"} or final.path != "/oembed":
                raise AppError("media_metadata_invalid")
            data = json.loads(document.body)
        except (ValueError, UnicodeError, RecursionError):
            raise AppError("media_metadata_invalid") from None
        if not isinstance(data, dict) or data.get("type") != "video" or data.get("provider_name") != "YouTube":
            raise AppError("media_metadata_invalid")
        return {"title": _text(data.get("title"), 240), "author": _text(data.get("author_name"), 160),
                "url": url, "source": "YouTube oEmbed", "kind": "youtube_metadata",
                "download_available": False, "fetched_at": datetime.now(UTC).isoformat()}
