"""Write the Scoop manifest of a Windows build of Terrascope with this plugin.

Usage:
    python packaging/windows/make_scoop_manifest.py --version 1.0.0-247062e-brd7c0f95 \
        --zip dist/terrascope-br_1.0.0-247062e-brd7c0f95_windows_x86_64.zip \
        --url https://github.com/<owner>/terrascope-br/releases/download/<tag>/<zip name> \
        --output dist/terrascope.json

The hash is the SHA-256 of the zip, so nothing has to be edited by hand.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

HOMEPAGE = "https://github.com/fernandoaugusto21/terrascope-br"
DESCRIPTION = ("Terrascope (fernandoaugusto21/Terrascope main) with the terrascope-br plugin: "
               "a Braille terminal map with a BRASIL tab of Brazilian data layers")


def manifest(version: str, url: str, sha256: str) -> dict:
    return {
        "version": version,
        "description": DESCRIPTION,
        "homepage": HOMEPAGE,
        "license": "MIT",
        "url": url,
        "hash": sha256,
        "bin": "terrascope.exe",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--zip", required=True, type=Path)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    digest = hashlib.sha256(args.zip.read_bytes()).hexdigest()
    text = json.dumps(manifest(args.version, args.url, digest), indent=2) + "\n"
    args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
