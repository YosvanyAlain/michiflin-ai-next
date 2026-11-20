from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone

from .errors import AppError
from .network import JsonClient


def effect_for(code: int, day: bool) -> str:
    if code not in {0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99}:
        return "unknown"
    if code in {95, 96, 99}:
        return "thunderstorm"
    if code in {71, 73, 75, 77, 85, 86}:
        return "snow"
    if code in {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}:
        return "rain"
    if code in {45, 48}:
        return "fog"
    if code in {2, 3}:
        return "cloudy"
    if code == 1:
        return "partly_cloudy"
    return "clear_day" if day else "clear_night"


def season_for(month: int, latitude: float) -> str:
    if abs(latitude) < 23.44:
        return "tropical"
    index = (month % 12) // 3
    if latitude < 0:
        index = (index + 2) % 4
    return ("winter", "spring", "summer", "autumn")[index]


def locations(client: JsonClient, query: str, language: str) -> dict:
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 100:
        raise AppError("invalid_location")
    data = client.get("https://geocoding-api.open-meteo.com/v1/search", {
        "name": query.strip(), "count": 8, "language": language, "format": "json",
    })
    return {"places": [{"id": item["id"], "name": item.get("name", ""),
                        "region": item.get("admin1", ""), "country": item.get("country", ""),
                        "latitude": item["latitude"], "longitude": item["longitude"],
                        "timezone": item.get("timezone", "")}
                       for item in data.get("results", []) if isinstance(item, dict)
                       and all(key in item for key in ("id", "latitude", "longitude"))]}


def forecast(client: JsonClient, latitude: object, longitude: object, clock=time.time) -> dict:
    if (type(latitude) not in (int, float) or type(longitude) not in (int, float)
            or not math.isfinite(latitude) or not math.isfinite(longitude)
            or not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
        raise AppError("invalid_coordinates")
    data = client.get("https://api.open-meteo.com/v1/forecast", {
        "latitude": latitude, "longitude": longitude, "timezone": "auto", "forecast_days": 7,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,cloud_cover,wind_speed_10m",
        "hourly": "temperature_2m,precipitation_probability,weather_code,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,uv_index_max,precipitation_probability_max",
    })
    current = data.get("current")
    if not isinstance(current, dict) or "weather_code" not in current or "time" not in current:
        raise AppError("source_response_invalid")
    try:
        offset = int(data.get("utc_offset_seconds", 0))
        local = datetime.fromisoformat(current["time"]).replace(tzinfo=timezone(timedelta(seconds=offset)))
        code = int(current["weather_code"])
        updated = local.timestamp()
    except (TypeError, ValueError, OverflowError):
        raise AppError("source_response_invalid") from None
    return {"current": current, "current_units": data.get("current_units", {}),
            "hourly": data.get("hourly", {}), "daily": data.get("daily", {}),
            "latitude": data.get("latitude", latitude), "longitude": data.get("longitude", longitude),
            "timezone": data.get("timezone", ""), "utc_offset_seconds": offset,
            "effect": effect_for(code, bool(current.get("is_day"))), "season": season_for(local.month, latitude),
            "as_of": updated, "fetched_at": clock(), "stale": clock() - updated > 5400,
            "source": "Open-Meteo", "data_kind": "weather_model"}
