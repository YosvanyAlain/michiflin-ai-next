from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__
from .package import build


def main() -> None:
    parser = argparse.ArgumentParser(prog="michiflin")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    package = commands.add_parser("build", help="Build the offline-first miniapp package")
    package.add_argument("--source", type=Path, default=Path("miniapp"))
    package.add_argument("--output", type=Path, default=Path("artifacts"))
    snapshot = commands.add_parser("snapshot", help="Create an authenticated private account and workspace snapshot")
    snapshot.add_argument("--accounts", type=Path, required=True)
    snapshot.add_argument("--state", type=Path, required=True)
    snapshot.add_argument("--output", type=Path, required=True)
    snapshot.add_argument("--passphrase-file", type=Path, required=True)
    recovery = commands.add_parser("restore", help="Restore and verify a snapshot without starting mail IO")
    recovery.add_argument("--input", type=Path, required=True)
    recovery.add_argument("--output", type=Path, required=True)
    recovery.add_argument("--passphrase-file", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "build":
        output = build(args.source, args.output)
        print(f"Built {output.name} ({output.stat().st_size} bytes). Production deployment is not enabled.")
        return
    from .backup import create, restore

    if (not args.passphrase_file.is_file() or args.passphrase_file.is_symlink()
            or args.passphrase_file.stat().st_size > 8192 or args.passphrase_file.stat().st_mode & 0o077):
        parser.error("The passphrase file must be private to its owner and must not be a symlink")
    passphrase = args.passphrase_file.read_text().strip()
    if args.command == "restore":
        restore(args.input, args.output, passphrase)
        print("Account identity, workspace database and pending files restored and verified. Mail IO remains stopped.")
    else:
        from deltachat2 import IOTransport, Rpc

        from .storage import Ledger

        if not (args.accounts / "accounts.toml").is_file() or not (args.state / "workspace.sqlite3").is_file():
            parser.error("Snapshot creation requires existing account and workspace state")
        import fcntl

        with (args.state / "runtime.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                parser.error("Stop the runtime before creating a consistent account/workspace snapshot")
            with IOTransport(accounts_dir=str(args.accounts)) as transport:
                rpc = Rpc(transport)
                accounts = rpc.get_all_account_ids()
                if len(accounts) != 1 or not rpc.is_configured(accounts[0]):
                    parser.error("Exactly one configured account is required")
                create(rpc, accounts[0], Ledger(args.state), args.output, passphrase)
        print("Authenticated private snapshot created. No invitation or identity details were printed.")


if __name__ == "__main__":
    main()
