from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

from . import downloads, library, weather
from .errors import AppError
from .network import JsonClient


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe actual public sources without private bot state.")
    parser.add_argument("--download", action="store_true", help="Fetch and validate a lawful public-domain EPUB.")
    args = parser.parse_args()
    results = check_sources()
    if args.download:
        results["gutenberg_epub"] = check_download()
    print(json.dumps(results, indent=2))
    if not all(item["ok"] for item in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
