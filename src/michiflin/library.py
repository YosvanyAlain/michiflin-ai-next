from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import AppError
from .gutenberg import GutenbergClient, fold, mirror_url
from .network import JsonClient

CATEGORIES = (
    "fiction", "science", "history", "philosophy", "poetry", "children",
    "biography", "technology", "adventure", "art", "education", "travel",
)
FORMATS = {"epub": "application/epub+zip", "txt": "text/plain", "html": "text/html",
           "pdf": "application/pdf", "kindle": "application/x-mobipocket-ebook"}


@dataclass(frozen=True)
class BookQuery:
    title: str = ""
    author: str = ""
    category: str = ""
    source: str = "gutenberg"
    format: str = ""
    language: str = ""
    page: int = 1

    @classmethod
    def parse(cls, data: dict) -> BookQuery:
        for key in ("title", "author", "category", "source", "format", "language"):
            if key in data and not isinstance(data[key], str):
                raise AppError("invalid_book_query")
        page = data.get("page", 1)
        if type(page) is not int or not 1 <= page <= 100:
            raise AppError("invalid_page")
        value = cls(**{key: str(data.get(key, default)).strip() for key, default in (
            ("title", ""), ("author", ""), ("category", ""), ("source", "gutenberg"),
            ("format", ""), ("language", ""))}, page=page)
        if (len(value.title) > 160 or len(value.author) > 120
                or value.source not in {"gutenberg", "openlibrary"}
                or value.format not in {"", *FORMATS}
                or value.category not in {"", *CATEGORIES}
                or value.language not in {"", "en", "es", "fr", "de", "it", "pt"}):
            raise AppError("invalid_book_query")
        if value.source == "openlibrary" and value.format:
            raise AppError("catalogue_format_unavailable")
        return value


def _allowed_download(url: object) -> str:
    if not isinstance(url, str):
        return ""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return ""
    if (parsed.scheme not in {"http", "https"} or parsed.username or parsed.password
            or parsed.hostname not in {"gutenberg.org", "www.gutenberg.org", "gutenberg.pglaf.org"}
            or port not in (None, 80, 443)):
        return ""
    if parsed.hostname == "gutenberg.pglaf.org":
        try:
            mirror_url(url)
        except AppError:
            return ""
    return url


def search(client: JsonClient, query: BookQuery) -> dict:
    return _gutenberg(client, query) if query.source == "gutenberg" else _openlibrary(client, query)


def _gutenberg(client: JsonClient, query: BookQuery) -> dict:
    params: dict = {"page": query.page, "copyright": "false"}
    if query.title or query.author:
        params["search"] = " ".join(filter(None, (query.title, query.author)))
    if query.category:
        params["topic"] = query.category
    if query.language:
        params["languages"] = query.language
    if query.format:
        params["mime_type"] = FORMATS[query.format]
    if isinstance(client, GutenbergClient):
        params.update(title=query.title, author=query.author)
    raw = client.get("https://gutendex.com/books/", params)
    rows = raw.get("results")
    if not isinstance(rows, list):
        raise AppError("source_response_invalid")
    books = []
    for row in rows[:32]:
        if not isinstance(row, dict) or row.get("copyright") is not False:
            continue
        identifier = row.get("id")
        if type(identifier) is not int or identifier < 1:
            continue
        authors = [str(item.get("name", ""))[:160] for item in row.get("authors", []) if isinstance(item, dict)]
        title = str(row.get("title", ""))[:300]
        normalize = fold if isinstance(client, GutenbergClient) else str.casefold
        if normalize(query.title) not in normalize(title):
            continue
        if normalize(query.author) not in normalize(" ".join(authors)):
            continue
        available = row.get("formats", {})
        downloads = []
        if isinstance(available, dict):
            for key, mime in FORMATS.items():
                if query.format and key != query.format:
                    continue
                matched = next((url for actual, url in available.items()
                                if isinstance(actual, str) and actual.startswith(mime) and _allowed_download(url)), "")
                if matched:
                    downloads.append({"format": key, "url": matched})
        if query.format and not downloads:
            continue
        books.append({"id": f"gutenberg:{identifier}", "title": title, "authors": authors,
                      "languages": row.get("languages", []), "subjects": row.get("subjects", [])[:8],
                      "url": f"https://www.gutenberg.org/ebooks/{identifier}", "downloads": downloads,
                      "source": "Project Gutenberg", "access": "public_domain_us"})
    return {"books": books, "page": query.page, "has_more": bool(raw.get("next")),
            "source_total": raw.get("count"), "source": "Project Gutenberg / official mirror" if isinstance(client, GutenbergClient) else "Project Gutenberg / Gutendex",
            "catalog_stale": raw.get("catalog_stale", False), "partial_results": raw.get("partial_results", False),
            "notice": "public_domain_jurisdiction", "filtered_page": True}


def _openlibrary(client: JsonClient, query: BookQuery) -> dict:
    params: dict = {"page": query.page, "limit": 20,
                    "fields": "key,title,author_name,first_publish_year,ebook_access", "q": "ebook_access:*"}
    for key, value in (("title", query.title), ("author", query.author), ("subject", query.category)):
        if value:
            params[key] = value
    if query.language:
        params["lang"] = query.language
        language = {"en": "eng", "es": "spa", "fr": "fre", "de": "ger", "it": "ita", "pt": "por"}[query.language]
        params["q"] += " AND language:" + language
    raw = client.get("https://openlibrary.org/search.json", params)
    rows = raw.get("docs")
    if not isinstance(rows, list):
        raise AppError("source_response_invalid")
    books = []
    for row in rows[:20]:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key", ""))
        if not re.fullmatch(r"(?:/works/)?OL[0-9]+W", key):
            continue
        key = key.rsplit("/", 1)[-1]
        access = row.get("ebook_access", "no_ebook")
        books.append({"id": "openlibrary:" + key, "title": str(row.get("title", ""))[:300],
                      "authors": row.get("author_name", [])[:8], "year": row.get("first_publish_year"),
                      "url": f"https://openlibrary.org/works/{key}", "downloads": [],
                      "source": "Open Library", "access": {"public": "read_online", "borrowable": "loan",
                      "printdisabled": "restricted"}.get(access, "catalogue")})
    total = raw.get("numFound", raw.get("num_found", 0))
    return {"books": books, "page": query.page,
            "has_more": isinstance(total, int) and query.page * 20 < total,
            "source_total": total, "source": "Open Library", "notice": "catalogue_not_download"}
