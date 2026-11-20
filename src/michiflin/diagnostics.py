from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from . import library, weather
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


def main() -> None:
    results = check_sources()
    print(json.dumps(results, indent=2))
    if not all(item["ok"] for item in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
