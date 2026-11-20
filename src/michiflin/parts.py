from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
from contextlib import contextmanager
from pathlib import Path

from .errors import AppError

FORMAT = "michiflin.raw-parts.v1"
MAX_BYTES = 64 * 1024 * 1024
MAX_PART_BYTES = 1024 * 1024
MAX_PARTS = 128
MANIFEST_LIMIT = 40000
CHUNK = 64 * 1024
NOFOLLOW = os.O_NOFOLLOW | os.O_NONBLOCK


def mail_part_budget(message_bytes: int, *, overhead_bytes: int = 128 * 1024) -> int:
    """Conservative base64+CRLF allowance, not a provider-specific delivery guarantee."""
    if (type(message_bytes) is not int or not 256 * 1024 <= message_bytes <= 16 * 1024 * 1024
            or type(overhead_bytes) is not int or not 64 * 1024 <= overhead_bytes < message_bytes):
        raise AppError("invalid_part_budget")
    return min(MAX_PART_BYTES, ((message_bytes - overhead_bytes) // 78) * 57)


def _name(value: object) -> str:
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)
            or len(value.encode("utf-8")) > 240
            or "/" in value or "\\" in value):
        raise AppError("invalid_parts_manifest")
    return value


def _hash(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def validate_manifest(value: object) -> dict:
    if (not isinstance(value, dict) or set(value) != {"format", "name", "bytes", "sha256", "part_bytes", "parts"}
            or value["format"] != FORMAT or type(value["bytes"]) is not int or not 0 <= value["bytes"] <= MAX_BYTES
            or not _hash(value["sha256"]) or type(value["part_bytes"]) is not int
            or not 1 <= value["part_bytes"] <= MAX_PART_BYTES or not isinstance(value["parts"], list)):
        raise AppError("invalid_parts_manifest")
    _name(value["name"])
    expected = max(1, (value["bytes"] + value["part_bytes"] - 1) // value["part_bytes"])
    if not 1 <= expected <= MAX_PARTS or len(value["parts"]) != expected:
        raise AppError("invalid_parts_manifest")
    for number, part in enumerate(value["parts"], 1):
        size = min(value["part_bytes"], max(0, value["bytes"] - (number - 1) * value["part_bytes"]))
        if (not isinstance(part, dict) or set(part) != {"number", "name", "bytes", "sha256"}
                or type(part["number"]) is not int or part["number"] != number
                or part["name"] != f"part-{number:05d}.bin" or type(part["bytes"]) is not int
                or part["bytes"] != size or not _hash(part["sha256"])):
            raise AppError("invalid_parts_manifest")
    return value


@contextmanager
def _directory(path: Path):
    # Walk every component with openat+O_NOFOLLOW so a replaced parent cannot redirect a later open.
    components = Path(path).absolute().parts
    if ".." in components:
        raise AppError("unsafe_parts_path")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in components[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _file(directory: int, name: str, *, maximum: int):
    descriptor = os.open(name, os.O_RDONLY | NOFOLLOW, dir_fd=directory)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
            raise AppError("unsafe_parts_file")
        stream = os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise
    with stream:
        yield stream, info


def _same(before, after):
    if any(getattr(before, key) != getattr(after, key) for key in ("st_size", "st_mtime_ns", "st_ctime_ns", "st_ino", "st_dev")):
        raise AppError("parts_source_changed")


def _write(directory: int, name: str, body: bytes) -> None:
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(descriptor, "wb") as target:
        target.write(body)
        target.flush()
        os.fsync(target.fileno())


def _publish(parent: int, staging: str, destination: str) -> None:
    # Plain rename can silently replace an empty directory; RENAME_NOREPLACE cannot.
    try:
        rename = ctypes.CDLL(None, use_errno=True).renameat2
    except AttributeError:
        raise AppError("atomic_publish_unavailable") from None
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(staging), parent, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        if error in (errno.EEXIST, errno.ENOTEMPTY):
            raise AppError("parts_destination_exists")
        if error in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
            raise AppError("atomic_publish_unavailable")
        raise OSError(error, "Atomic directory publication failed")


def split(source: Path, destination: Path, *, part_bytes: int, display_name: str | None = None) -> dict:
    """Copy a caller-authorized local file to a new private raw-part bundle. Never executes .xdc input."""
    if type(part_bytes) is not int or not 1 <= part_bytes <= MAX_PART_BYTES:
        raise AppError("invalid_part_budget")
    source, destination = Path(source), Path(destination)
    name = _name(source.name if display_name is None else display_name)
    _name(destination.name)
    with _directory(source.parent) as origin, _directory(destination.parent) as parent, \
            _file(origin, source.name, maximum=MAX_BYTES) as (stream, before):
        count = max(1, (before.st_size + part_bytes - 1) // part_bytes)
        if count > MAX_PARTS:
            raise AppError("parts_count_limit")
        staging = ".parts-" + secrets.token_hex(16)
        os.mkdir(staging, mode=0o700, dir_fd=parent)
        published = False
        try:
            stage = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                digest, records, size = hashlib.sha256(), [], 0
                for number in range(1, count + 1):
                    body = stream.read(part_bytes)
                    size += len(body)
                    digest.update(body)
                    filename = f"part-{number:05d}.bin"
                    _write(stage, filename, body)
                    records.append({"number": number, "name": filename, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()})
                if stream.read(1) or size != before.st_size:
                    raise AppError("parts_source_changed")
                _same(before, os.fstat(stream.fileno()))
                manifest = validate_manifest({"format": FORMAT, "name": name, "bytes": size,
                                              "sha256": digest.hexdigest(), "part_bytes": part_bytes, "parts": records})
                _write(stage, "manifest.json", json.dumps(manifest, ensure_ascii=True, separators=(",", ":")).encode())
                os.fsync(stage)
            finally:
                os.close(stage)
            _publish(parent, staging, destination.name)
            published = True
            try:
                os.fsync(parent)
            except OSError:
                raise AppError("parts_publication_uncertain") from None
            return manifest
        finally:
            if not published:
                shutil.rmtree(staging, dir_fd=parent)


def _manifest(directory: int) -> dict:
    with _file(directory, "manifest.json", maximum=MANIFEST_LIMIT) as (stream, before):
        raw = stream.read(MANIFEST_LIMIT + 1)
        _same(before, os.fstat(stream.fileno()))
    if len(raw) > MANIFEST_LIMIT:
        raise AppError("invalid_parts_manifest")
    try:
        value = json.loads(raw, object_pairs_hook=_unique)
    except (UnicodeError, ValueError, RecursionError):
        raise AppError("invalid_parts_manifest") from None
    return validate_manifest(value)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate manifest key")
        result[key] = value
    return result


def _inventory(directory: int, manifest: dict) -> list[int]:
    present = set()
    expected = {part["name"] for part in manifest["parts"]}
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name not in expected and entry.name != "manifest.json":
                raise AppError("parts_unexpected_files")
            present.add(entry.name)
    return [part["number"] for part in manifest["parts"] if part["name"] not in present]


def inspect(bundle: Path) -> dict:
    """Report gaps only, not integrity or authenticity. SHA-256 is not a signature."""
    with _directory(bundle) as directory:
        manifest = _manifest(directory)
        return {"name": manifest["name"], "bytes": manifest["bytes"], "parts": len(manifest["parts"]),
                "missing": _inventory(directory, manifest), "sha256": manifest["sha256"], "verified": False}


def join(bundle: Path, destination: Path, *, expected_sha256: str | None = None) -> dict:
    """Validate every part and the complete hash before atomic, no-clobber publication."""
    destination = Path(destination)
    _name(destination.name)
    with _directory(bundle) as directory, _directory(destination.parent) as parent:
        if (os.fstat(directory).st_dev, os.fstat(directory).st_ino) == (os.fstat(parent).st_dev, os.fstat(parent).st_ino):
            raise AppError("unsafe_parts_path")
        manifest = _manifest(directory)
        if expected_sha256 is not None and (not _hash(expected_sha256) or expected_sha256 != manifest["sha256"]):
            raise AppError("parts_hash_mismatch")
        if _inventory(directory, manifest):
            raise AppError("parts_missing")
        temporary = ".joined-" + secrets.token_hex(16)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        temporary_exists = True
        try:
            with os.fdopen(descriptor, "wb") as target:
                digest, size = hashlib.sha256(), 0
                for part in manifest["parts"]:
                    with _file(directory, part["name"], maximum=part["bytes"]) as (stream, before):
                        if before.st_size != part["bytes"]:
                            raise AppError("parts_hash_mismatch")
                        partial = hashlib.sha256()
                        remaining = part["bytes"]
                        while remaining:
                            body = stream.read(min(CHUNK, remaining))
                            if not body:
                                raise AppError("parts_hash_mismatch")
                            remaining -= len(body)
                            partial.update(body)
                            digest.update(body)
                            size += len(body)
                            target.write(body)
                        if stream.read(1) or partial.hexdigest() != part["sha256"]:
                            raise AppError("parts_hash_mismatch")
                        _same(before, os.fstat(stream.fileno()))
                if size != manifest["bytes"] or digest.hexdigest() != manifest["sha256"]:
                    raise AppError("parts_hash_mismatch")
                target.flush()
                os.fsync(target.fileno())
            # A hard-link publication is atomic and fails if any destination already exists.
            try:
                os.link(temporary, destination.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            except FileExistsError:
                raise AppError("parts_destination_exists") from None
            os.unlink(temporary, dir_fd=parent)
            temporary_exists = False
            try:
                os.fsync(parent)
            except OSError:
                raise AppError("parts_publication_uncertain") from None
            return {"bytes": size, "sha256": digest.hexdigest(), "verified": True}
        finally:
            if temporary_exists:
                os.unlink(temporary, dir_fd=parent)
