from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import requests

from .backup import LIMIT, create, restore
from .egress import source_proxy
from .gutenberg import GutenbergClient
from .storage import Ledger

TAG = "michiflin-next-checkpoints-v1"


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PrivateStore:
    def __init__(self, repository: str, token: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not token:
            raise ValueError("Private backup configuration is required")
        self.base = "https://api.github.com/repos/" + repository
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                                     "X-GitHub-Api-Version": "2022-11-28"})
        metadata = self.json("GET", "")
        if metadata.get("private") is not True or not metadata.get("permissions", {}).get("push"):
            raise ValueError("Backup storage must be private and writable")

    def json(self, method: str, path: str, **kwargs):
        response = self.session.request(method, self.base + path, timeout=(5, 60), allow_redirects=False, **kwargs)
        response.raise_for_status()
        return response.json()

    def release(self) -> dict:
        response = self.session.get(self.base + "/releases/tags/" + TAG, timeout=(5, 30), allow_redirects=False)
        if response.status_code == 404:
            return self.json("POST", "/releases", json={"tag_name": TAG, "name": "Authenticated next runtime checkpoints",
                                                       "body": "Private encrypted state. Previous recovery assets are retained.",
                                                       "draft": False, "prerelease": False})
        response.raise_for_status()
        return response.json()

    def assets(self, release: dict) -> list[dict]:
        assets = []
        for page in range(1, 101):
            batch = self.json("GET", f"/releases/{release['id']}/assets", params={"per_page": 100, "page": page})
            assets.extend(batch)
            if len(batch) < 100:
                return assets
        raise ValueError("Checkpoint inventory requires explicit archival before continuing")

    def download(self, asset: int, destination: Path, maximum: int = LIMIT + 1024) -> None:
        url = self.base + f"/releases/assets/{asset}"
        headers = {"Accept": "application/octet-stream"}
        with self.session.get(url, headers=headers, stream=True, timeout=(5, 60), allow_redirects=False) as response:
            if response.status_code == 302:
                from urllib.parse import urlsplit

                location = response.headers.get("Location", "")
                parsed = urlsplit(location)
                if (parsed.scheme != "https" or parsed.hostname != "release-assets.githubusercontent.com"
                        or parsed.username or parsed.password):
                    raise ValueError("Unexpected private asset redirect")
            else:
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError("Unexpected private asset status")
                self._copy(response, destination, maximum)
                return
        # Never forward the private API token to object storage.
        with requests.Session() as public:
            public.trust_env = False
            with public.get(location, stream=True, timeout=(5, 60), allow_redirects=False) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError("Unexpected private asset status")
                self._copy(response, destination, maximum)

    @staticmethod
    def _copy(response, destination: Path, maximum: int) -> None:
        size = 0
        with destination.open("xb") as output:
            for chunk in response.iter_content(1024 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise ValueError("Private asset exceeds its size limit")
                output.write(chunk)
        destination.chmod(0o600)

    def upload(self, release: dict, path: Path) -> dict:
        with path.open("rb") as stream:
            response = self.session.post(
                f"https://uploads.github.com{self.base.removeprefix('https://api.github.com')}/releases/{release['id']}/assets",
                params={"name": path.name}, data=stream, headers={"Content-Type": "application/octet-stream"},
                timeout=(5, 120), allow_redirects=False)
        response.raise_for_status()
        return response.json()


def latest_checkpoint(store: PrivateStore, release: dict, seed: dict, directory: Path) -> dict:
    assets = store.assets(release)
    running = [asset for asset in assets if re.fullmatch(r"session-[a-f0-9]{32}\.running\.json", asset["name"])]
    if not running:
        return validate_checkpoint(seed)
    latest = max(running, key=lambda asset: asset["id"])
    expected = latest["name"].replace(".running.json", ".verified.json")
    committed = [asset for asset in assets if asset["name"] == expected]
    if len(committed) != 1 or committed[0]["id"] <= latest["id"]:
        raise ValueError("The previous session has no verified shutdown checkpoint; automatic restart is blocked")
    path = directory / "latest-checkpoint.json"
    store.download(committed[0]["id"], path, maximum=4096)
    result = validate_checkpoint(json.loads(path.read_text()))
    if result.get("session") != latest["name"].removesuffix(".running.json"):
        raise ValueError("The checkpoint belongs to a different session")
    return result


def validate_checkpoint(value: dict) -> dict:
    if (not isinstance(value, dict) or value.get("format") != 1 or type(value.get("asset_id")) is not int
            or value["asset_id"] <= 0 or not isinstance(value.get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])):
        raise ValueError("Invalid authenticated checkpoint reference")
    return value


def run_bounded(runtime: Path, config: dict, duration: int, source: Path) -> int:
    environment = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH") if key in os.environ}
    if proxy := source_proxy():
        environment["MICHIFLIN_SOURCE_PROXY"] = proxy
    environment.update({"PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                        "MICHIFLIN_STATE_DIR": str(runtime / "state"), "MICHIFLIN_PUBLIC_ACCESS": "1",
                        "MICHIFLIN_ADMIN_KEYS": json.dumps(config["admin_keys"]),
                        "MICHIFLIN_PROVIDERS": json.dumps(config["providers"])})
    process = subprocess.Popen([sys.executable, "-m", "michiflin.runtime", "--config-dir", str(runtime),
                                "--miniapp-source", str(source), "serve"], env=environment,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        try:
            return process.wait(timeout=duration)
        except subprocess.TimeoutExpired:
            process.send_signal(signal.SIGINT)
            return process.wait(timeout=180)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def checkpoint(store: PrivateStore, release: dict, runtime: Path, directory: Path,
               passphrase: str, stem: str, revision: str) -> dict:
    from deltachat2 import IOTransport, Rpc

    recovery = runtime.parent.parent / "recovery"
    recovery.mkdir(mode=0o700, exist_ok=True)
    snapshot = recovery / (stem + ".snapshot")
    with (runtime / "state" / "runtime.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with IOTransport(accounts_dir=str(runtime / "accounts")) as transport:
            rpc = Rpc(transport)
            accounts = rpc.get_all_account_ids()
            if len(accounts) != 1 or not rpc.is_configured(accounts[0]):
                raise ValueError("Exactly one configured identity is required")
            metadata = create(rpc, accounts[0], Ledger(runtime / "state"), snapshot, passphrase)
    restore(snapshot, directory / "local-verification", passphrase)
    shutil.rmtree(directory / "local-verification")
    asset = store.upload(release, snapshot)
    downloaded = directory / "remote-verification.snapshot"
    store.download(asset["id"], downloaded)
    if checksum(downloaded) != metadata["sha256"]:
        raise ValueError("The remote checkpoint digest does not match")
    restore(downloaded, directory / "remote-verification", passphrase)
    manifest = {"format": 1, "asset_id": asset["id"], "sha256": metadata["sha256"],
                "revision": revision, "session": stem, "verified_at": int(time.time()),
                "catalog_bytes": metadata.get("catalog_bytes", 0)}
    marker = directory / (stem + ".verified.json")
    marker.write_text(json.dumps(manifest))
    marker.chmod(0o600)
    store.upload(release, marker)
    snapshot.unlink()
    return manifest


def execute(*, validate_only: bool, duration: int, directory: Path, source: Path,
            prepare_catalog: bool = False) -> dict:
    revision = os.environ.get("MICHIFLIN_DEPLOY_REVISION", "")
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("An exact deployment revision is required")
    if not validate_only and os.environ.get("MICHIFLIN_LAUNCH_REVISION") != revision:
        raise ValueError("Production is not armed for this exact revision")
    config = json.loads(os.environ["MICHIFLIN_DEPLOY_CONFIG"])
    passphrase = os.environ["MICHIFLIN_BACKUP_PASSPHRASE"]
    if (len(passphrase) < 24 or config.get("backup_passphrase") != passphrase
            or not isinstance(config.get("admin_keys"), dict) or not config["admin_keys"]
            or not isinstance(config.get("providers"), list) or not config["providers"]):
        raise ValueError("The private runtime configuration is incomplete")
    if not 1 <= duration <= 19800:
        raise ValueError("The private session duration is outside its safe range")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / "deployment.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = PrivateStore(os.environ["MICHIFLIN_BACKUP_REPOSITORY"], os.environ["MICHIFLIN_BACKUP_TOKEN"])
        release = store.release()
        work = Path(tempfile.mkdtemp(prefix="next-session-", dir=directory))
        completed = False
        try:
            seed = {"format": 1, "asset_id": int(os.environ["MICHIFLIN_SEED_ASSET_ID"]),
                    "sha256": os.environ["MICHIFLIN_SEED_SHA256"]}
            previous = latest_checkpoint(store, release, seed, work)
            downloaded = work / "restore.snapshot"
            store.download(previous["asset_id"], downloaded)
            if checksum(downloaded) != previous["sha256"]:
                raise ValueError("The selected snapshot digest does not match")
            runtime = work / "runtime"
            restore(downloaded, runtime, passphrase)
            if prepare_catalog:
                GutenbergClient(runtime / "state" / "catalog").sync()
            stem = ("validation-" if validate_only else "session-") + uuid.uuid4().hex
            if not validate_only:
                marker = work / (stem + ".running.json")
                marker.write_text(json.dumps({"format": 1, "revision": revision, "previous": previous,
                                               "started_at": int(time.time())}))
                marker.chmod(0o600)
                store.upload(release, marker)
                try:
                    result = run_bounded(runtime, config, duration, source)
                    print(f"Runtime stopped with exit code {result}; verifying private recovery.", flush=True)
                finally:
                    manifest = checkpoint(store, release, runtime, work, passphrase, stem, revision)
            else:
                manifest = checkpoint(store, release, runtime, work, passphrase, stem, revision)
            completed = True
            if not validate_only and result != 0:
                raise RuntimeError("The runtime failed; its latest state was nevertheless verified and preserved")
            print("Private snapshot authenticated, uploaded, downloaded and independently restored. No identity details were logged.", flush=True)
            return {"validated": True, "io_started": not validate_only, "checkpoint": manifest}

        finally:
            if completed or validate_only:
                shutil.rmtree(work)


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Run a bounded private session with fail-closed checkpoint recovery")
    parser.add_argument("--validate-only", action="store_true", help="Verify real backup/restore without starting mail IO")
    parser.add_argument("--prepare-catalog", action="store_true", help="Explicitly prepare the bounded public feed before checkpointing or starting IO")
    parser.add_argument("--duration", type=int, default=19200)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path("miniapp"))
    args = parser.parse_args()
    try:
        execute(validate_only=args.validate_only, duration=args.duration, directory=args.directory, source=args.source,
                prepare_catalog=args.prepare_catalog)
    except Exception:
        print("Private session stopped safely. No automatic rollback or unverified restart was attempted.", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
