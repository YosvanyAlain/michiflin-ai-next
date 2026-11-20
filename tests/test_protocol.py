from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from michiflin.errors import AppError
from michiflin.package import build
from michiflin.protocol import decode_request, encode_response


def test_protocol_roundtrip_and_no_unrecognized_fields():
    request = {"type": "michiflin.request", "protocol": 1, "id": "synthetic-123456", "op": "hello", "injected": True}
    assert decode_request(request) == {"id": request["id"], "op": "hello"}
    body = {"id": request["id"], "ok": True, "data": {"text": "Café 🐈"}}
    assert encode_response(body)[0]["body"] == body


@pytest.mark.parametrize("payload", [None, [], {}, {"type": "michiflin.request", "protocol": 2},
                                      {"type": "michiflin.request", "protocol": 1, "args": float("nan")}])
def test_protocol_rejects_invalid_frames(payload):
    with pytest.raises(AppError):
        decode_request(payload)


def test_chunking_is_ascii_bounded_and_reconstructable():
    body = {"id": "synthetic-123456", "ok": True, "data": "🐈" * 30000}
    frames = encode_response(body)
    assert len(frames) > 1
    assert all(frame["total"] == len(frames) and len(frame["text"]) <= 45000 for frame in frames)
    assert json.loads("".join(frame["text"] for frame in frames)) == body
    with pytest.raises(AppError, match="result_too_large"):
        encode_response({"id": "synthetic-123456", "data": "x" * 1_000_000})


def test_request_byte_limit_is_not_a_character_limit():
    with pytest.raises(AppError, match="request_too_large"):
        decode_request({"type": "michiflin.request", "protocol": 1, "args": "猫" * 11000})


def test_package_is_reproducible_complete_and_small(tmp_path):
    source = Path(__file__).resolve().parents[1] / "miniapp"
    first = build(source, tmp_path / "first")
    second = build(source, tmp_path / "second")
    assert first.read_bytes() == second.read_bytes()
    assert first.stat().st_size < 150000
    with zipfile.ZipFile(first) as archive:
        names = set(archive.namelist())
        assert {"manifest.toml", "icon.png", "index.html", "app.js"} <= names
        assert all(not name.startswith("/") and ".." not in name for name in names)
        assert all(not name.endswith(".py") for name in names)


def test_package_refuses_missing_assets(tmp_path):
    with pytest.raises(ValueError, match="missing or unsafe"):
        build(tmp_path, tmp_path / "output")
