from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

from . import downloads, library, weather
from .errors import AppError
from .media import Media
from .network import JsonClient
from .reader import Reader


def check_sources() -> dict:
    def probe(name, run):
        try:
            data = run(JsonClient())
            return name, {"ok": True, "items": len(data.get("books", data.get("places", []))),
                          "forecast": data.get("data_kind") == "weather_model"}
        except AppError as error:
            return name, {"ok": False, "error": error.code}

    operations = [
        ("gutendex", lambda client: library.search(client, library.BookQuery(title="Pride", format="epub"))),
        ("openlibrary", lambda client: library.search(client, library.BookQuery(title="Don Quijote", source="openlibrary", language="es"))),
        ("geocoding", lambda client: weather.locations(client, "Singapore", "en")),
        ("forecast", lambda client: weather.forecast(client, 1.29, 103.85)),
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        return dict(pool.map(lambda item: probe(*item), operations))


def check_download() -> dict:
    try:
        metadata = JsonClient().get("https://gutendex.com/books/1342/")
        if metadata.get("id") != 1342 or metadata.get("copyright") is not False:
            raise AppError("book_rights_unverified")
        formats = metadata.get("formats")
        url = formats.get("application/epub+zip") if isinstance(formats, dict) else None
        if not isinstance(url, str) or not library._allowed_download(url):
            raise AppError("download_invalid")
        body = downloads.fetch_bytes(url)
        downloads._validate(body, "epub")
        return {"ok": True, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    except AppError as error:
        return {"ok": False, "error": error.code}


def check_reader() -> dict:
    try:
        data = Reader().read({"url": "https://example.com/"})
        if data["title"] != "Example Domain" or "domain" not in " ".join(data["pages"]).lower():
            raise AppError("web_content_invalid")
        return {"ok": True, "characters": data["characters"], "pages": len(data["pages"])}
    except AppError as error:
        return {"ok": False, "error": error.code}


def check_media() -> dict:
    try:
        data = Media().inspect({"url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ"})
        return {"ok": True, "source": data["source"], "metadata_only": not data["download_available"]}
    except AppError as error:
        return {"ok": False, "error": error.code}


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe actual public sources without private bot state.")
    parser.add_argument("--download", action="store_true", help="Fetch and validate a lawful public-domain EPUB.")
    parser.add_argument("--reader", action="store_true", help="Fetch and parse an actual public web page.")
    parser.add_argument("--media", action="store_true", help="Fetch actual YouTube metadata, not video bytes.")
    args = parser.parse_args()
    results = check_sources()
    if args.download:
        results["gutenberg_epub"] = check_download()
    if args.reader:
        results["web_reader"] = check_reader()
    if args.media:
        results["youtube_metadata"] = check_media()
    print(json.dumps(results, indent=2))
    if not all(item["ok"] for item in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
