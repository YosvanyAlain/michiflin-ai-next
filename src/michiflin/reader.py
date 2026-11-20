from __future__ import annotations

import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .errors import AppError
from .public_http import Document, PublicHttp, normalize_url

PAGE_CHARS = 6000
MAX_TEXT = 36000
_SKIP = {"script", "style", "noscript", "template", "svg", "math", "iframe", "object", "embed",
         "nav", "footer", "aside", "form", "button", "select", "textarea", "head"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "div", "section", "article", "main", "tr"}


def _clean(value: str) -> str:
    value = re.sub(r"[\x00-\x08\x0b-\x1f\x7f\u202a-\u202e\u2066-\u2069]", "", value)
    return re.sub(r"[^\S\n]+", " ", value).strip()


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, bool, int | None]] = []
        self.chunks: list[tuple[str, bool]] = []
        self.title: list[str] = []
        self.heading: list[str] = []
        self.links: list[dict] = []
        self.nodes = 0

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 20000 or len(self.stack) >= 256:
            raise AppError("web_response_limit")
        if tag in {"p", "li"} and self.stack and self.stack[-1][0] == tag:
            self.handle_endtag(tag)
        attrs = dict(attrs)
        parent_skip = self.stack[-1][1] if self.stack else False
        skip = (parent_skip or tag in _SKIP or "hidden" in attrs
                or (attrs.get("aria-hidden") or "").lower() == "true"
                or bool(re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", (attrs.get("style") or ""), re.I)))
        focus = (self.stack[-1][2] if self.stack else False) or tag in {"article", "main"}
        if tag in _BLOCK or tag in {"br", "hr"}:
            self.chunks.append(("\n", focus))
        link = None
        if tag == "a" and not skip and len(self.links) < 100:
            link = len(self.links)
            self.links.append({"href": attrs.get("href", ""), "parts": [], "focus": focus})
        if tag not in _VOID:
            self.stack.append((tag, skip, focus, link))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                if tag in _BLOCK:
                    self.chunks.append(("\n", self.stack[index][2]))
                del self.stack[index:]
                break

    def handle_data(self, data):
        if any(frame[0] == "title" for frame in self.stack):
            self.title.append(data)
        skip = self.stack[-1][1] if self.stack else False
        if skip:
            return
        focus = self.stack[-1][2] if self.stack else False
        if any(frame[0] == "h1" for frame in self.stack):
            self.heading.append(data)
        self.chunks.append((data, focus))
        for frame in reversed(self.stack):
            if frame[3] is not None:
                self.links[frame[3]]["parts"].append(data)
                break


def _decode(document: Document) -> str:
    charset = re.search(r"charset\s*=\s*[\"']?([a-z0-9_-]+)", document.content_type, re.I)
    encoding = charset.group(1).lower() if charset else "utf-8-sig"
    if encoding not in {"utf-8", "utf-8-sig", "iso-8859-1", "windows-1252", "us-ascii"}:
        raise AppError("web_format_unsupported")
    try:
        return document.body.decode(encoding, errors="replace")
    except (UnicodeError, LookupError):
        raise AppError("web_content_invalid") from None


def _pages(text: str) -> list[str]:
    pages = []
    while text:
        cut = min(PAGE_CHARS, len(text))
        if cut < len(text):
            boundary = text.rfind("\n", cut // 2, cut)
            if boundary < 0:
                boundary = text.rfind(" ", cut // 2, cut)
            if boundary > 0:
                cut = boundary
        pages.append(text[:cut].strip())
        text = text[cut:].lstrip()
    return pages


class Reader:
    def __init__(self, client: PublicHttp | None = None) -> None:
        self.client = client or PublicHttp()

    def read(self, args: dict) -> dict:
        document = self.client.get(args.get("url"))
        markup = _decode(document)
        title, links = urlsplit(document.url).hostname, []
        if document.content_type.startswith("text/plain"):
            text = _clean(markup)
        else:
            parser = _TextParser()
            parser.feed(markup)
            parser.close()
            focused = "".join(value for value, focus in parser.chunks if focus)
            use_focus = len(_clean(focused)) >= 80
            raw = focused if use_focus else "".join(value for value, _ in parser.chunks)
            text = "\n\n".join(line for part in raw.splitlines() if (line := _clean(part)))
            title = _clean(" ".join(parser.title or parser.heading))[:240] or title
            seen = {document.url}
            for link in parser.links:
                label = _clean(" ".join(link["parts"]))[:100]
                if not label or use_focus and not link["focus"]:
                    continue
                try:
                    url = normalize_url(urljoin(document.url, link["href"]))
                except AppError:
                    continue
                if url not in seen:
                    links.append({"title": label, "url": url})
                    seen.add(url)
                if len(links) >= 20:
                    break
        if len(text.strip()) < 40:
            raise AppError("web_no_readable_text")
        truncated = len(text) > MAX_TEXT
        pages = _pages(text[:MAX_TEXT])
        return {"title": title, "url": document.url, "pages": pages, "links": links,
                "characters": sum(len(page) for page in pages), "truncated": truncated,
                "fetched_at": datetime.now(UTC).isoformat(), "kind": "plain_text_reader"}
