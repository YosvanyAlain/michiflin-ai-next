from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import subprocess
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ENGINE_URL = "https://github.com/ggml-org/llama.cpp/releases/download/b12556/llama-b12556-bin-ubuntu-x64.tar.gz"
ENGINE_SHA256 = "1a573b6089d29c9048eefe8497dd40cac4115400f223d33f2a71e506bf6d5b18"
MODEL_URL = "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/9217f5db79a29953eb74d5343926648285ec7e67/qwen2.5-0.5b-instruct-q4_k_m.gguf"
MODEL_SHA256 = "74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, path: Path, expected: str, limit: int) -> None:
    if path.is_file() and _sha(path) == expected:
        return
    partial = path.with_suffix(".partial")
    digest, size, deadline = hashlib.sha256(), 0, time.monotonic() + 900
    try:
        with requests.Session() as session:
            session.trust_env = False
            with session.get(url, stream=True, timeout=(10, 40)) as response:
                response.raise_for_status()
                with partial.open("wb") as target:
                    for chunk in response.iter_content(1024 * 1024):
                        size += len(chunk)
                        if size > limit or time.monotonic() > deadline:
                            raise RuntimeError("Local model download exceeded its limit")
                        digest.update(chunk)
                        target.write(chunk)
        if digest.hexdigest() != expected:
            raise RuntimeError("Local model download failed its pinned checksum")
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


def prepare(directory: Path) -> tuple[Path, Path]:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    archive, model = directory / "engine.tar.gz", directory / "qwen-0.5b-q4.gguf"
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(_download, ENGINE_URL, archive, ENGINE_SHA256, 32_000_000),
                pool.submit(_download, MODEL_URL, model, MODEL_SHA256, 510_000_000)]
        for job in jobs:
            job.result()
    engine = directory / "engine"
    engine.mkdir(mode=0o700, exist_ok=True)
    with tarfile.open(archive) as source:
        members = source.getmembers()
        if len(members) > 1000 or sum(item.size for item in members) > 200_000_000:
            raise RuntimeError("Local inference archive exceeds extraction limits")
        source.extractall(engine, filter="data")
    candidates = [path for path in engine.rglob("llama-server") if path.is_file() and not path.is_symlink()]
    if len(candidates) != 1:
        raise RuntimeError("The pinned inference archive has an unsupported layout")
    candidates[0].chmod(0o700)
    return candidates[0].resolve(), model.resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare and run an optional pinned CPU-only local model")
    parser.add_argument("command", choices=("prepare", "serve"))
    parser.add_argument("--directory", type=Path, default=Path(".cache/michiflin-local"))
    parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("The local model port must be between 1024 and 65535")
    executable, model = prepare(args.directory)
    print("Pinned local inference assets verified.", flush=True)
    if args.command == "serve":
        key_file = args.directory.resolve() / "local.key"
        if not key_file.exists():
            descriptor = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as target:
                target.write(secrets.token_urlsafe(32))
        environment = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR"}}
        environment["LD_LIBRARY_PATH"] = str(executable.parent)
        subprocess.run([str(executable), "--model", str(model), "--alias", "local", "--host", "127.0.0.1",
                        "--port", str(args.port), "--ctx-size", "8192", "--parallel", "1", "--threads", "2",
                        "--n-predict", "1400", "--no-webui", "--api-key-file", str(key_file)], env=environment, check=True)


if __name__ == "__main__":
    main()
