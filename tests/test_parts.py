import copy
import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from michiflin import parts
from michiflin.errors import AppError


@pytest.fixture
def bundle(tmp_path):
    source = tmp_path / "original.xdc"
    source.write_bytes(b"Synthetic raw bytes; not executable webxdc.\x00" * 11)
    directory = tmp_path / "bundle"
    manifest = parts.split(source, directory, part_bytes=40)
    return source, directory, manifest


def write_manifest(directory, value):
    (directory / "manifest.json").write_text(json.dumps(value))


def test_exact_raw_reconstruction_hash_original_and_private_modes(bundle, tmp_path):
    source, directory, manifest = bundle
    result = parts.join(directory, tmp_path / "reconstructed.xdc")
    assert result == {"bytes": source.stat().st_size, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "verified": True}
    assert source.read_bytes() == (tmp_path / "reconstructed.xdc").read_bytes()
    assert manifest["name"] == "original.xdc"
    assert directory.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in directory.iterdir())
    assert (tmp_path / "reconstructed.xdc").stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".parts-*")) and not list(tmp_path.glob(".joined-*"))


@pytest.mark.parametrize("size", [0, 1, 39, 40, 41, 80])
def test_empty_and_exact_boundary_files(tmp_path, size):
    source = tmp_path / "source"
    source.write_bytes(b"x" * size)
    manifest = parts.split(source, tmp_path / "bundle", part_bytes=40)
    assert len(manifest["parts"]) == max(1, (size + 39) // 40)
    parts.join(tmp_path / "bundle", tmp_path / "output")
    assert (tmp_path / "output").read_bytes() == source.read_bytes()


def test_gap_detection_is_not_claimed_hash_verification(bundle, tmp_path):
    _source, directory, manifest = bundle
    (directory / manifest["parts"][1]["name"]).unlink()
    assert parts.inspect(directory)["missing"] == [2]
    assert parts.inspect(directory)["verified"] is False
    with pytest.raises(AppError, match="parts_missing"):
        parts.join(directory, tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("mutation", ["corrupt", "short", "long", "whole_hash", "symlink", "hardlink", "fifo", "extra"])
def test_invalid_parts_never_publish_partial_output(bundle, tmp_path, mutation):
    source, directory, manifest = bundle
    first = directory / manifest["parts"][0]["name"]
    if mutation == "corrupt":
        first.write_bytes(b"!" * first.stat().st_size)
    elif mutation == "short":
        first.write_bytes(b"!")
    elif mutation == "long":
        first.write_bytes(b"!" * (first.stat().st_size + 1))
    elif mutation == "whole_hash":
        manifest["sha256"] = "0" * 64
        write_manifest(directory, manifest)
    elif mutation in {"symlink", "hardlink", "fifo"}:
        first.unlink()
        if mutation == "symlink":
            first.symlink_to(source)
        elif mutation == "hardlink":
            os.link(source, first)
        else:
            os.mkfifo(first)
    else:
        (directory / "unexpected").write_text("not listed")
    with pytest.raises((AppError, OSError)):
        parts.join(directory, tmp_path / "output")
    assert not (tmp_path / "output").exists()
    assert not list(tmp_path.glob(".joined-*"))


@pytest.mark.parametrize("mutation", ["traversal", "duplicate", "order", "boolean", "count", "format", "name", "oversize", "unknown"])
def test_strict_manifest_schema(bundle, mutation):
    _source, _directory, original = bundle
    value = copy.deepcopy(original)
    if mutation == "traversal":
        value["parts"][0]["name"] = "../outside"
    elif mutation == "duplicate":
        value["parts"][1] = value["parts"][0]
    elif mutation == "order":
        value["parts"].reverse()
    elif mutation == "boolean":
        value["bytes"] = True
    elif mutation == "count":
        value["parts"].append(copy.deepcopy(value["parts"][-1]))
    elif mutation == "format":
        value["format"] = "unknown.v2"
    elif mutation == "name":
        value["name"] = "../original.xdc"
    elif mutation == "oversize":
        value["bytes"] = parts.MAX_BYTES + 1
    else:
        value["unreviewed"] = "field"
    with pytest.raises(AppError, match="invalid_parts_manifest"):
        parts.validate_manifest(value)


def test_duplicate_json_keys_are_rejected(bundle):
    _source, directory, _manifest = bundle
    text = (directory / "manifest.json").read_text()
    (directory / "manifest.json").write_text('{"format":"untrusted",' + text[1:])
    with pytest.raises(AppError, match="invalid_parts_manifest"):
        parts.inspect(directory)


@pytest.mark.parametrize("existing", ["file", "empty_directory", "directory", "symlink"])
def test_split_never_replaces_an_existing_destination(bundle, tmp_path, existing):
    source, _directory, _manifest = bundle
    destination = tmp_path / "existing"
    if existing == "file":
        destination.write_text("preserved")
    elif existing == "symlink":
        destination.symlink_to(source)
    else:
        destination.mkdir()
        if existing == "directory":
            (destination / "preserved").write_text("preserved")
    with pytest.raises((AppError, OSError)):
        parts.split(source, destination, part_bytes=40)
    if existing == "file":
        assert destination.read_text() == "preserved"
    elif existing == "symlink":
        assert destination.is_symlink()
    else:
        assert destination.is_dir()
    assert not list(tmp_path.glob(".parts-*"))


def test_join_never_overwrites_original_or_dereferences_output_symlink(bundle, tmp_path):
    source, directory, _manifest = bundle
    original = source.read_bytes()
    for destination in (source, tmp_path / "link"):
        if destination != source:
            destination.symlink_to(source)
        with pytest.raises(AppError, match="parts_destination_exists"):
            parts.join(directory, destination)
        assert source.read_bytes() == original
    assert not list(tmp_path.glob(".joined-*"))


def test_parent_symlink_source_symlink_and_dotdot_are_refused(bundle, tmp_path):
    source, directory, _manifest = bundle
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(directory, target_is_directory=True)
    with pytest.raises(OSError):
        parts.inspect(parent_link)
    source_link = tmp_path / "source-link"
    source_link.symlink_to(source)
    with pytest.raises(OSError):
        parts.split(source_link, tmp_path / "output", part_bytes=40)
    with pytest.raises(AppError, match="unsafe_parts_path"):
        parts.inspect(directory / ".." / "bundle")


def test_manifest_symlink_hardlink_and_oversize_are_refused(bundle, tmp_path):
    _source, directory, _manifest = bundle
    manifest = directory / "manifest.json"
    original = tmp_path / "original-manifest"
    manifest.rename(original)
    manifest.symlink_to(original)
    with pytest.raises(OSError):
        parts.inspect(directory)
    manifest.unlink()
    os.link(original, manifest)
    with pytest.raises(AppError, match="unsafe_parts_file"):
        parts.inspect(directory)
    manifest.unlink()
    manifest.write_bytes(b"x" * (parts.MANIFEST_LIMIT + 1))
    with pytest.raises(AppError, match="unsafe_parts_file"):
        parts.inspect(directory)


def test_oversized_source_and_excessive_parts_are_refused(tmp_path):
    source = tmp_path / "source"
    with source.open("wb") as output:
        output.truncate(parts.MAX_BYTES + 1)
    with pytest.raises(AppError, match="unsafe_parts_file"):
        parts.split(source, tmp_path / "bundle", part_bytes=parts.MAX_PART_BYTES)
    source.write_bytes(b"x" * (parts.MAX_PARTS + 1))
    with pytest.raises(AppError, match="parts_count_limit"):
        parts.split(source, tmp_path / "bundle", part_bytes=1)
    assert not (tmp_path / "bundle").exists()


def test_concurrent_split_publication_has_exactly_one_winner(bundle, tmp_path):
    source, _directory, _manifest = bundle
    barrier = threading.Barrier(2)

    def attempt(_):
        barrier.wait()
        try:
            parts.split(source, tmp_path / "contended", part_bytes=40)
            return True
        except AppError as error:
            assert error.code == "parts_destination_exists"
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(attempt, range(2))) == 1
    assert not list(tmp_path.glob(".parts-*"))
    parts.join(tmp_path / "contended", tmp_path / "verified")
    assert (tmp_path / "verified").read_bytes() == source.read_bytes()


def test_disk_failure_cleans_staging_and_preserves_source(bundle, tmp_path, monkeypatch):
    source, _directory, _manifest = bundle
    original = source.read_bytes()
    monkeypatch.setattr(parts, "_write", lambda *a: (_ for _ in ()).throw(OSError("synthetic disk full")))
    with pytest.raises(OSError):
        parts.split(source, tmp_path / "failed", part_bytes=40)
    assert source.read_bytes() == original
    assert not (tmp_path / "failed").exists() and not list(tmp_path.glob(".parts-*"))


def test_source_mutation_during_split_is_detected(bundle, tmp_path, monkeypatch):
    source, _directory, _manifest = bundle
    original = parts._write
    mutated = False

    def changed(*args):
        nonlocal mutated
        original(*args)
        if not mutated:
            with source.open("ab") as stream:
                stream.write(b"mutation")
            mutated = True

    monkeypatch.setattr(parts, "_write", changed)
    with pytest.raises(AppError, match="parts_source_changed"):
        parts.split(source, tmp_path / "failed", part_bytes=40)
    assert not (tmp_path / "failed").exists() and not list(tmp_path.glob(".parts-*"))


@pytest.mark.parametrize("message_bytes,overhead", [(True, 65536), (1024, 65536), (16 * 1024 * 1024 + 1, 65536),
                                                   (1024 * 1024, 0), (1024 * 1024, 1024 * 1024)])
def test_invalid_mail_budgets_are_refused(message_bytes, overhead):
    with pytest.raises(AppError, match="invalid_part_budget"):
        parts.mail_part_budget(message_bytes, overhead_bytes=overhead)


def test_mail_budget_accounts_for_base64_lines_and_overhead():
    cap, overhead = 1024 * 1024, 128 * 1024
    budget = parts.mail_part_budget(cap, overhead_bytes=overhead)
    wire = ((budget + 56) // 57) * 78 + overhead
    assert wire <= cap
    assert budget <= parts.MAX_PART_BYTES


def test_external_trusted_hash_cannot_be_replaced_with_a_modified_manifest(bundle, tmp_path):
    source, directory, manifest = bundle
    with pytest.raises(AppError, match="parts_hash_mismatch"):
        parts.join(directory, tmp_path / "wrong", expected_sha256="0" * 64)
    assert not (tmp_path / "wrong").exists()
    parts.join(directory, tmp_path / "correct", expected_sha256=manifest["sha256"])
    assert (tmp_path / "correct").read_bytes() == source.read_bytes()


def test_join_does_not_add_unlisted_files_to_original_bundle(bundle):
    _source, directory, _manifest = bundle
    before = set(directory.iterdir())
    with pytest.raises(AppError, match="unsafe_parts_path"):
        parts.join(directory, directory / "output")
    assert set(directory.iterdir()) == before


@pytest.mark.parametrize("name", ["bad\ud800name", "bad\x00name", "..", "../escape", "x" * 241])
def test_invalid_display_names_never_create_a_bundle(bundle, tmp_path, name):
    source, _directory, _manifest = bundle
    with pytest.raises(AppError, match="invalid_parts_manifest"):
        parts.split(source, tmp_path / "failed", part_bytes=40, display_name=name)
    assert not (tmp_path / "failed").exists()


def test_concurrent_join_has_exactly_one_winner(bundle, tmp_path):
    source, directory, _manifest = bundle
    barrier = threading.Barrier(2)

    def attempt(_):
        barrier.wait()
        try:
            parts.join(directory, tmp_path / "contended")
            return True
        except AppError as error:
            assert error.code == "parts_destination_exists"
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(attempt, range(2))) == 1
    assert (tmp_path / "contended").read_bytes() == source.read_bytes()
    assert not list(tmp_path.glob(".joined-*"))


@pytest.mark.parametrize("operation", ["split", "join"])
def test_directory_sync_failure_reports_uncertainty_without_deleting_complete_output(bundle, tmp_path, monkeypatch, operation):
    source, directory, _manifest = bundle
    original = os.fsync
    parent_ino = tmp_path.stat().st_ino

    def fail_parent(descriptor):
        if os.fstat(descriptor).st_ino == parent_ino:
            raise OSError("synthetic directory sync failure")
        return original(descriptor)

    monkeypatch.setattr(os, "fsync", fail_parent)
    destination = tmp_path / "published"
    with pytest.raises(AppError, match="parts_publication_uncertain"):
        if operation == "split":
            parts.split(source, destination, part_bytes=40)
        else:
            parts.join(directory, destination)
    assert destination.exists()
    if operation == "split":
        assert parts.inspect(destination)["missing"] == []
    else:
        assert destination.read_bytes() == source.read_bytes()
        assert destination.stat().st_nlink == 1
    assert not list(tmp_path.glob(".parts-*")) and not list(tmp_path.glob(".joined-*"))
