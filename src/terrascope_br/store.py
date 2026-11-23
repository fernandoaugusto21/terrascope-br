"""Files in the app's cache directory for the BR layers. The age of an entry
is its file modification time, so the same check works for JSON, CSV, text
and PNG. Writes go through a temporary file so a crash never leaves half a file.
The choices the user makes (an on/off switch kept between runs) are not cache:
they go to settings_path(), in the Terrascope config folder."""

from __future__ import annotations

import os
import time
from pathlib import Path

from terrascope.core import config

def base_dir() -> Path:
    """<cache-dir>/br. Read at call time, after the command line was applied."""
    return Path(config.CACHE_DIR) / "br"

def path(*parts: str) -> Path:
    return base_dir().joinpath(*parts)

def settings_path(*parts: str) -> Path:
    """<Terrascope config folder>/br/<parts>, where the choices the user makes are kept (the INMET token
    is there too), so that emptying the cache does not reset them. A core without a config folder keeps
    them in the cache."""
    folder = getattr(config, "USER_CONFIG_DIR", None)
    return (Path(folder) / "br" if folder else base_dir()).joinpath(*parts)

def age(file: Path) -> float | None:
    """Seconds since the file was written, or None when it does not exist."""
    try:
        return max(0.0, time.time() - file.stat().st_mtime)
    except OSError:
        return None

def read_bytes(file: Path) -> bytes | None:
    try:
        return file.read_bytes()
    except OSError:
        return None

def write_bytes(file: Path, data: bytes) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_name(file.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, file)
