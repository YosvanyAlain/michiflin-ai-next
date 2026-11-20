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
    args = parser.parse_args()
    if args.command == "build":
        output = build(args.source, args.output)
        print(f"Built {output.name} ({output.stat().st_size} bytes). Production deployment is not enabled.")


if __name__ == "__main__":
    main()
