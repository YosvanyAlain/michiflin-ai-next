from __future__ import annotations

import os
import shutil
import subprocess

from michiflin import sandbox


def main() -> None:
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise SystemExit("Bubblewrap is not installed; isolated execution is disabled.")
    command = [bwrap, "--unshare-all", "--die-with-parent", "--cap-drop", "ALL", "--clearenv",
               "--ro-bind", "/usr", "/usr"]
    for path in ("/lib", "/lib64"):
        if os.path.isdir(path):
            command.extend(["--ro-bind", path, path])
    command.extend(["--", "/usr/bin/true"])
    result = subprocess.run(command, env={"PATH": "/usr/bin:/bin"}, capture_output=True, timeout=10)
    if result.returncode:
        # This fixed no-input namespace probe never receives a user prompt, account or private path.
        print(result.stderr[:2000].decode(errors="replace"))
        raise SystemExit("Host namespaces are unavailable; isolated execution is disabled.")
    if not sandbox.available():
        raise SystemExit("Namespaces work, but the complete Python/seccomp/resource probe failed.")
    print("Real namespace, seccomp and bounded Python execution probe passed.")


if __name__ == "__main__":
    main()
