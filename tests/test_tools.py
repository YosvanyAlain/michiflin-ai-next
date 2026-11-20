from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from michiflin import library, utilities, weather
from michiflin.errors import AppError
from michiflin.network import JsonClient


@pytest.mark.parametrize("expression,result", [
    ("17 * 23", 391), ("2 + 3 * 4", 14), ("(2+3)*4", 20), ("2 ** 5", 32),
    ("-12 / 3", -4), ("1.5 + .5", 2), ("10 % 3", 1),
])
def test_bounded_calculator(expression, result):
    assert utilities.calculate(expression)["value"] == result


@pytest.mark.parametrize("expression", [
    "__import__('os').system('id')", "2 ** (2 ** 32)", "9**9999999", "1/0",
    "[1,2]", "True", "1e200", "(-1)**0.5", "x", "1;2", "9" * 401,
])
def test_calculator_refuses_code_and_expensive_expressions(expression):
    with pytest.raises(AppError):
        utilities.calculate(expression)


def test_unit_dimensions_temperatures_and_binary_storage():
    assert utilities.convert(32, "F", "C")["value"] == 0
    assert utilities.convert(1, "MiB", "B")["value"] == 1048576
    assert utilities.convert(1, "mi", "m")["value"] == pytest.approx(1609.344)
    with pytest.raises(AppError, match="incompatible_units"):
        utilities.convert(1, "kg", "m")
    with pytest.raises(AppError, match="below_absolute_zero"):
        utilities.convert(-1, "K", "C")


@pytest.mark.parametrize("code,effect", [(0, "clear_day"), (3, "cloudy"), (45, "fog"),
                                         (65, "rain"), (75, "snow"), (95, "thunderstorm")])
def test_weather_animations_follow_wmo_codes(code, effect):
    assert weather.effect_for(code, True) == effect
    assert weather.effect_for(0, False) == "clear_night"


def test_weather_seasons_respect_hemisphere_and_tropics():
    assert weather.season_for(1, 51) == "winter"
    assert weather.season_for(1, -35) == "summer"
    assert weather.season_for(7, 51) == "summer"
    assert weather.season_for(1, 23.1) == "tropical"


@pytest.mark.parametrize("lat,lon", [(True, 1), (91, 0), (0, 181), (float("nan"), 0), ("23", 1)])
def test_weather_refuses_invalid_coordinates(lat, lon):
    with pytest.raises(AppError):
        weather.forecast(Mock(), lat, lon)


def test_weather_marks_old_model_data_as_stale():
    now = datetime(2026, 1, 1, 12, tzinfo=UTC).timestamp()
    client = Mock()
    client.get.return_value = {"current": {"weather_code": 65, "time": "2026-01-01T08:00", "is_day": 1}, "utc_offset_seconds": 0}
    result = weather.forecast(client, 23.1, -82.3, clock=lambda: now)
    assert result["stale"] and result["effect"] == "rain"
    assert result["data_kind"] == "weather_model"


@pytest.mark.parametrize("url", ["http://openlibrary.org/search.json", "https://openlibrary.org.evil.test/",
                                  "https://127.0.0.1/", "https://openlibrary.org:8443/",
                                  "https://user:pass@openlibrary.org/"])
def test_source_client_refuses_unapproved_destinations(url):
    with pytest.raises(AppError, match="source_not_allowed"):
        JsonClient().get(url)


def test_gutenberg_applies_title_author_and_format_filters_without_inventing_downloads():
    client = Mock()
    client.get.return_value = {"count": 300, "next": "https://gutendex.com/books/?page=2", "results": [
        {"id": 123, "title": "Great Expectations", "authors": [{"name": "Dickens, Charles"}], "copyright": False,
         "formats": {"application/epub+zip": "https://www.gutenberg.org/ebooks/123.epub3.images"}},
        {"id": 124, "title": "Another Title", "authors": [{"name": "Dickens, Charles"}], "copyright": False, "formats": {}},
        {"id": 125, "title": "Great Expectations", "authors": [{"name": "Different Author"}], "copyright": False, "formats": {}},
        {"id": 126, "title": "Great Expectations", "authors": [{"name": "Dickens, Charles"}], "copyright": True, "formats": {}},
    ]}
    result = library.search(client, library.BookQuery.parse({"title": "Great", "author": "Dickens", "format": "epub"}))
    assert len(result["books"]) == 1
    assert result["books"][0]["downloads"][0]["format"] == "epub"
    assert result["books"][0]["access"] == "public_domain_us"
    assert result["filtered_page"] is True


def test_catalogue_does_not_advertise_unverified_formats_or_free_downloads():
    client = Mock()
    client.get.return_value = {"numFound": 1, "docs": [{"key": "/works/OL123W", "title": "Example", "author_name": ["Writer"], "ebook_access": "borrowable"}]}
    result = library.search(client, library.BookQuery.parse({"source": "openlibrary", "author": "Writer"}))
    assert result["books"][0]["access"] == "loan"
    assert result["books"][0]["downloads"] == []
    with pytest.raises(AppError, match="catalogue_format_unavailable"):
        library.BookQuery.parse({"source": "openlibrary", "format": "pdf"})


def test_book_download_urls_cannot_point_to_arbitrary_servers():
    for url in ["javascript:alert(1)", "https://gutenberg.org.evil.test/book.epub", "file:///etc/passwd", "https://127.0.0.1/book.epub"]:
        assert library._allowed_download(url) == ""
