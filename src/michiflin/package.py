from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

_REQUIRED = {"index.html", "manifest.toml", "icon.png", "app.js", "ui.js", "transport.js", "offline.js",
             "extras.js", "files.js", "filetools.js", "reader.js", "locales/en.js", "locales/es.js", "styles/workspace.css", "assets/mark.svg", "assets/michiflin-night.jpg"}


def build(source: Path, destination: Path) -> Path:
    paths = []
    digest = hashlib.sha256()
    for name in sorted(_REQUIRED):
        path = source / name
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
            raise ValueError("A required miniapp asset is missing or unsafe")
        raw = path.read_bytes()
        if len(raw) > 2_000_000:
            raise ValueError("A miniapp asset exceeds the size limit")
        digest.update(name.encode() + b"\0" + raw)
        paths.append((name, raw))
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / f"michiflin-{digest.hexdigest()[:16]}.xdc"
    temporary = output.with_suffix(".partial")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, raw in paths:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw)
    temporary.replace(output)
    return output
