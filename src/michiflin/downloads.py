from __future__ import annotations

import io
import os
import secrets
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from .errors import AppError
from .gutenberg import fetch, mirror_url
from .library import FORMATS, _allowed_download

MAX_BYTES = 24 * 1024 * 1024
EXTENSIONS = {"epub": ".epub", "txt": ".txt", "pdf": ".pdf", "kindle": ".mobi"}


def _validate(body: bytes, format: str) -> None:
    if not body:
        raise AppError("download_invalid")
    if format == "epub":
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                items = archive.infolist()
                if len(items) > 10000 or sum(item.file_size for item in items) > 150_000_000:
                    raise AppError("download_invalid")
                mime = archive.getinfo("mimetype")
                if mime.file_size > 100 or archive.read(mime) != b"application/epub+zip":
                    raise AppError("download_invalid")
        except (zipfile.BadZipFile, KeyError, RuntimeError):
            raise AppError("download_invalid") from None
    elif format == "pdf" and not body.startswith(b"%PDF-") or format == "kindle" and body[60:68] != b"BOOKMOBI":
        raise AppError("download_invalid")
    elif format == "txt":
        try:
            text = body.decode("utf-8-sig")
            if "\0" in text or text.lstrip().lower().startswith(("<!doctype html", "<html")):
                raise AppError("download_invalid")
        except UnicodeError:
            raise AppError("download_invalid") from None


def fetch_bytes(url: str) -> bytes:
    return fetch(mirror_url(url), limit=MAX_BYTES)


def prepare(client, ledger, directory: Path, principal, instance: int, args: dict) -> dict:
    identifier, format = args.get("book"), args.get("format")
    if type(identifier) is not int or not 1 <= identifier <= 10_000_000 or not isinstance(format, str) or format not in EXTENSIONS:
        raise AppError("invalid_book_query")
    ledger.check_delivery_capacity(principal.storage_key)
    metadata = client.get(f"https://gutendex.com/books/{identifier}/")
    if metadata.get("id") != identifier or metadata.get("copyright") is not False:
        raise AppError("book_rights_unverified")
    formats = metadata.get("formats")
    if not isinstance(formats, dict):
        raise AppError("download_unavailable")
    url = next((url for mime, url in formats.items() if isinstance(mime, str)
                and mime.startswith(FORMATS[format]) and _allowed_download(url)
                and urlsplit(url).scheme == "https"), None)
    if not url:
        raise AppError("download_unavailable")
    body = fetch_bytes(url)
    _validate(body, format)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    receipt = secrets.token_hex(16)
    file = directory / (receipt + EXTENSIONS[format])
    descriptor = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
        title = str(metadata.get("title", ""))[:200]
        ledger.queue_delivery(receipt, principal.storage_key, principal.chat_id, instance, file.name, title, len(body))
    except BaseException:
        file.unlink(missing_ok=True)
        raise
    return {"receipt": receipt, "status": "queued", "title": title, "bytes": len(body), "format": format,
            "rights": "public_domain_us", "source": f"https://www.gutenberg.org/ebooks/{identifier}"}
