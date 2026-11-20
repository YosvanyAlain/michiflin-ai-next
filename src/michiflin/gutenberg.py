from __future__ import annotations

import csv
import gzip
import io
import os
import re
import sqlite3
import tempfile
import threading
import time
import unicodedata
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

from .errors import AppError
from .public_http import PublicHttp

MIRROR = "https://gutenberg.pglaf.org"
HOSTS = frozenset({"gutenberg.pglaf.org"})
FEED = MIRROR + "/cache/epub/feeds/pg_catalog.csv.gz"
PAGE_SIZE = 6
NS = {"pg": "http://www.gutenberg.org/2009/pgterms/", "dc": "http://purl.org/dc/terms/",
      "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#"}
ABOUT = "{" + NS["rdf"] + "}about"


def mirror_url(value: object) -> str:
    if not isinstance(value, str) or re.search(r"[\s\\%\x00-\x1f\x7f]", value):
        raise AppError("source_not_allowed")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname not in {"gutenberg.org", "www.gutenberg.org", *HOSTS}
                or parsed.port not in (None, 443) or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            raise ValueError
        path = parsed.path
        match = re.fullmatch(r"/ebooks/([1-9][0-9]{0,7})\.(epub3\.images|epub\.images|epub\.noimages|txt\.utf-8|kindle\.images|kindle\.noimages|kf8\.images|html\.images|rdf)", path)
        if match:
            identifier, format = match.groups()
            suffix = {"epub3.images": "-images-3.epub", "epub.images": "-images.epub", "epub.noimages": ".epub",
                      "txt.utf-8": ".txt", "kindle.images": "-images.mobi", "kindle.noimages": ".mobi",
                      "kf8.images": "-images-kf8.mobi", "html.images": "-images.html", "rdf": ".rdf"}[format]
            path = f"/cache/epub/{identifier}/pg{identifier}{suffix}"
        elif re.fullmatch(r"/cache/epub/([1-9][0-9]{0,7})/pg\1(?:[.-][a-zA-Z0-9._-]+)", path):
            pass
        elif parsed.hostname not in HOSTS and re.fullmatch(r"/files/[1-9][0-9]{0,7}/[a-zA-Z0-9_./-]+", path):
            _, _, identifier, *parts = path.split("/")
            if any(part in {"", ".", ".."} for part in parts):
                raise ValueError
            prefix = "/".join(identifier[:-1]) or "0"
            path = f"/{prefix}/{identifier}/" + "/".join(parts)
        elif parsed.hostname in HOSTS and (match := re.fullmatch(r"/((?:[0-9]/)+)([1-9][0-9]{0,7})/([a-zA-Z0-9_./-]+)", path)):
            prefix, identifier, tail = match.groups()
            if prefix.rstrip("/") != ("/".join(identifier[:-1]) or "0") or any(part in {"", ".", ".."} for part in tail.split("/")):
                raise ValueError
        else:
            raise ValueError
        return MIRROR + path
    except ValueError:
        raise AppError("source_not_allowed") from None


def fetch(url: str, *, limit: int, seconds: float = 20) -> bytes:
    parsed = urlsplit(url)
    if parsed.hostname not in HOSTS or parsed.scheme != "https":
        raise AppError("source_not_allowed")
    try:
        return PublicHttp(limit=limit, seconds=seconds, hosts=HOSTS).get(url, binary_response=True).body
    except AppError as error:
        if error.code.startswith("source_"):
            raise
        code = {"web_response_limit": "source_response_limit", "web_destination_blocked": "source_not_allowed"}.get(
            error.code, "source_rate_limited" if error.code == "source_rate_limited" else "source_unavailable")
        raise AppError(code, retryable=error.retryable) from None


def parse_metadata(body: bytes, identifier: int) -> dict:
    if len(body) > 512000:
        raise AppError("source_response_limit")
    try:
        root = ET.fromstring(body, forbid_dtd=True)
    except (ET.ParseError, DefusedXmlException, ValueError):
        raise AppError("source_response_invalid") from None
    books = root.findall("pg:ebook", NS)
    if len(books) != 1 or books[0].get(ABOUT) not in {f"ebooks/{identifier}", f"http://www.gutenberg.org/ebooks/{identifier}", f"https://www.gutenberg.org/ebooks/{identifier}"}:
        raise AppError("source_response_invalid")
    book = books[0]
    rights = [(node.text or "").strip() for node in book.findall("dc:rights", NS)]
    public = rights == ["Public domain in the USA."]
    formats = {}
    files = []
    for node in book.findall("dc:hasFormat/pg:file", NS)[:100]:
        try:
            url = mirror_url(node.get(ABOUT))
            prefix = "/".join(str(identifier)[:-1]) or "0"
            if not url.startswith((f"{MIRROR}/cache/epub/{identifier}/pg{identifier}", f"{MIRROR}/{prefix}/{identifier}/")):
                continue
            extent = int(node.findtext("dc:extent", "0", NS))
            if extent < 0:
                continue
        except (AppError, ValueError):
            continue
        for mime in node.findall("dc:format/rdf:Description/rdf:value", NS):
            if mime.text:
                files.append((extent or 1_000_000_000, mime.text, url))
    for _, mime, url in sorted(files):
        formats.setdefault(mime, url)
    return {"id": identifier, "title": book.findtext("dc:title", "", NS)[:300],
            "authors": [{"name": name.text[:160]} for name in book.findall("dc:creator/pg:agent/pg:name", NS)[:8] if name.text],
            "languages": [node.text for node in book.findall("dc:language/rdf:Description/rdf:value", NS)[:8] if node.text],
            "subjects": [node.text[:200] for node in book.findall("dc:subject/rdf:Description/rdf:value", NS)[:8] if node.text],
            "copyright": not public, "formats": formats,
            "metadata_source": f"{MIRROR}/cache/epub/{identifier}/pg{identifier}.rdf"}


def fold(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(char))


class GutenbergClient:
    """Official mirror catalogue discovery and fresh per-book rights metadata."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.database = directory / "catalog.sqlite3"
        self._lock = threading.Lock()

    def metadata(self, identifier: int) -> dict:
        if type(identifier) is not int or not 1 <= identifier <= 10_000_000:
            raise AppError("invalid_book_query")
        url = f"{MIRROR}/cache/epub/{identifier}/pg{identifier}.rdf"
        return parse_metadata(fetch(url, limit=512000, seconds=8), identifier)

    def _age(self) -> float:
        if self.database.is_symlink():
            raise AppError("source_response_invalid")
        try:
            return time.time() - self.database.stat().st_mtime
        except FileNotFoundError:
            return float("inf")

    def sync(self) -> None:
        if self._age() < 7 * 86400:
            return
        if not self._lock.acquire(timeout=1):
            raise AppError("source_rate_limited", retryable=True)
        temporary = None
        try:
            if self._age() < 7 * 86400:
                return
            compressed = fetch(FEED, limit=8 * 1024 * 1024)
            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as archive:
                raw = archive.read(32 * 1024 * 1024 + 1)
            if len(raw) > 32 * 1024 * 1024:
                raise AppError("source_response_limit")
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix="catalog-", suffix=".sqlite3", dir=self.directory)
            os.close(fd)
            temporary = Path(name)
            with sqlite3.connect(temporary) as db:
                db.execute("CREATE TABLE books(id INTEGER PRIMARY KEY,title TEXT,author TEXT,topic TEXT,language TEXT)")
                rows = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
                if not {"Text#", "Type", "Title", "Authors", "Language", "Subjects", "Bookshelves"} <= set(rows.fieldnames or []):
                    raise AppError("source_response_invalid")
                count = 0
                for index, row in enumerate(rows):
                    if index >= 150000 or any(value is None or len(value) > 60000 for value in row.values() if not isinstance(value, list)):
                        raise AppError("source_response_limit")
                    if None in row:
                        raise AppError("source_response_invalid")
                    if row["Type"] != "Text":
                        continue
                    identifier = int(row["Text#"])
                    if not 1 <= identifier <= 10_000_000:
                        raise AppError("source_response_invalid")
                    languages = ";" + ";".join(value.strip() for value in row["Language"].split(";")) + ";"
                    db.execute("INSERT INTO books VALUES(?,?,?,?,?)", (identifier, fold(row["Title"]), fold(row["Authors"]),
                               fold(row["Subjects"] + " " + row["Bookshelves"]), languages))
                    count += 1
                if not count:
                    raise AppError("source_response_invalid")
                db.commit()
            temporary.replace(self.database)
        except (OSError, EOFError, ValueError, csv.Error, sqlite3.Error, UnicodeError, zlib.error):
            raise AppError("source_response_invalid") from None
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
            self._lock.release()

    def get(self, url: str, params: dict | None = None) -> dict:
        match = re.fullmatch(r"https://gutendex.com/books/([1-9][0-9]{0,7})/", url)
        if match:
            return self.metadata(int(match[1]))
        if url != "https://gutendex.com/books/" or not isinstance(params, dict):
            raise AppError("source_not_allowed")
        stale = False
        try:
            self.sync()
        except AppError:
            if not self.database.is_file() or self.database.is_symlink():
                raise
            stale = True
        where, values = ["1=1"], []
        for field in ("title", "author", "topic"):
            value = str(params.get(field, "")).strip()
            if value:
                where.append(f"instr({field},?)>0")
                values.append(fold(value))
        if language := params.get("languages"):
            where.append("instr(language,?)>0")
            values.append(";" + language + ";")
        page = params.get("page", 1)
        if type(page) is not int or not 1 <= page <= 100:
            raise AppError("invalid_page")
        clause = " AND ".join(where)
        try:
            with sqlite3.connect(f"file:{self.database}?mode=ro", uri=True, timeout=2) as db:
                count = db.execute("SELECT COUNT(*) FROM books WHERE " + clause, values).fetchone()[0]
                identifiers = [row[0] for row in db.execute("SELECT id FROM books WHERE " + clause + " ORDER BY id LIMIT ? OFFSET ?", [*values, PAGE_SIZE, (page - 1) * PAGE_SIZE])]
        except sqlite3.Error:
            raise AppError("source_response_invalid") from None

        def resolve(identifier):
            try:
                return self.metadata(identifier)
            except AppError:
                return None

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(resolve, identifiers))
        if identifiers and not any(results):
            raise AppError("source_unavailable", retryable=True)
        return {"results": [result for result in results if result], "count": count,
                "next": page * PAGE_SIZE < count, "catalog_stale": stale,
                "catalog_updated_at": self.database.stat().st_mtime,
                "partial_results": any(result is None for result in results)}
