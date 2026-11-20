from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def main() -> None:
    node = shutil.which("node")
    if node is None:
        import playwright

        node = str(Path(playwright.__file__).parent / "driver" / "node")
    root = Path(__file__).resolve().parents[1] / "miniapp"
    for source in sorted([*root.glob("*.js"), *(root / "locales").glob("*.js")]):
        subprocess.run([node, "--check", str(source)], check=True)
    print("JavaScript syntax checks passed.")


if __name__ == "__main__":
    main()
