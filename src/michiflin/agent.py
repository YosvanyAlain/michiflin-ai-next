from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import library, utilities, weather


@dataclass(frozen=True)
class Tool:
    description: str
    properties: dict
    required: tuple[str, ...]
    run: Callable[[dict], dict]

    def schema(self, name: str) -> dict:
        return {"type": "function", "function": {"name": name, "description": self.description,
                "parameters": {"type": "object", "properties": self.properties,
                               "required": list(self.required), "additionalProperties": False}}}


def tools(client, *, local_only: bool, locale: str, book_client=None) -> dict[str, Tool]:
    string, number = {"type": "string"}, {"type": "number"}
    result = {
        "calculate": Tool("Evaluate bounded arithmetic, never program code.", {"expression": string}, ("expression",),
                          lambda args: utilities.calculate(args.get("expression"))),
        "convert": Tool("Convert compatible physical units. Temperature uses C, F, K.",
                        {"value": number, "source": string, "target": string}, ("value", "source", "target"),
                        lambda args: utilities.convert(args.get("value"), args.get("source"), args.get("target"))),
        "time": Tool("Read the current time in an IANA time zone.", {"zone": string}, ("zone",),
                     lambda args: utilities.time_in(args.get("zone"))),
    }
    if not local_only:
        result.update({
            "find_books": Tool("Find books and verified access links, never download or send files.",
                               {"title": string, "author": string, "category": {"type": "string", "enum": ["", *library.CATEGORIES]},
                                "source": {"type": "string", "enum": ["gutenberg", "openlibrary"]}}, (),
                               lambda args: library.search(book_client if book_client is not None and args.get("source", "gutenberg") == "gutenberg" else client, library.BookQuery.parse(args))),
            "find_places": Tool("Find candidate places. Ask the user to disambiguate when needed.", {"query": string}, ("query",),
                                lambda args: weather.locations(client, args.get("query"), locale)),
            "weather": Tool("Read modelled weather for explicit coordinates, not live station observations.",
                            {"latitude": number, "longitude": number}, ("latitude", "longitude"),
                            lambda args: weather.forecast(client, args.get("latitude"), args.get("longitude"))),
        })
    return result
