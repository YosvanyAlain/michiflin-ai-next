from __future__ import annotations

import json

from .errors import AppError


def decode_request(payload: object) -> dict:
    if not isinstance(payload, dict) or payload.get("type") != "michiflin.request" or type(payload.get("protocol")) is not int or payload.get("protocol") != 1:
        raise AppError("unsupported_protocol")
    try:
        size = len(json.dumps(payload, allow_nan=False).encode())
    except (TypeError, ValueError):
        raise AppError("invalid_request") from None
    if size > 32000:
        raise AppError("request_too_large")
    return {key: payload[key] for key in ("id", "op", "args", "session", "locale") if key in payload}


def encode_response(response: dict) -> list[dict]:
    encoded = json.dumps(response, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    if len(encoded) > 1_000_000:
        raise AppError("result_too_large")
    if len(encoded) <= 45000:
        return [{"type": "michiflin.response", "protocol": 1, "body": response}]
    chunks = [encoded[index:index + 45000] for index in range(0, len(encoded), 45000)]
    return [{"type": "michiflin.chunk", "protocol": 1, "id": response["id"],
             "index": index, "total": len(chunks), "text": text} for index, text in enumerate(chunks)]
