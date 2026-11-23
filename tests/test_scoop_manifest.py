"""The Scoop manifest of the Windows build: the zip holds the --onedir folder's contents, so the
exe is at the root of the zip and `bin` names it."""

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "packaging" / "windows" / "make_scoop_manifest.py"
spec = importlib.util.spec_from_file_location("make_scoop_manifest", SCRIPT)
scoop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scoop)

URL = "https://github.com/fernandoaugusto21/terrascope-br/releases/download/windows-x/terrascope-br.zip"


def make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def onedir_zip(tmp_path: Path) -> Path:
    """What the workflow makes: the folder's contents at the root, _internal/ next to the exe."""
    return make_zip(tmp_path / "terrascope-br_1.0.0_windows_x86_64.zip", {
        "terrascope.exe": b"exe",
        "_internal/base_library.zip": b"lib",
        "_internal/python313.dll": b"dll",
    })


def test_the_manifest_points_at_the_exe_at_the_root():
    data = scoop.manifest("1.0.0", URL, "ab" * 32)
    assert data == {
        "version": "1.0.0",
        "description": scoop.DESCRIPTION,
        "homepage": "https://github.com/fernandoaugusto21/terrascope-br",
        "license": "MIT",
        "url": URL,
        "hash": "ab" * 32,
        "bin": "terrascope.exe",
    }
    assert "extract_dir" not in data   # the zip has no folder to strip


def test_the_hash_is_the_sha256_of_the_zip_and_bin_is_the_exe(tmp_path):
    zip_path = onedir_zip(tmp_path)
    output = tmp_path / "terrascope-br.json"
    assert scoop.main(["--version", "1.0.0-abc", "--zip", str(zip_path), "--url", URL,
                       "--output", str(output)]) == 0
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["hash"] == hashlib.sha256(zip_path.read_bytes()).hexdigest()
    assert written["url"] == URL
    assert written["version"] == "1.0.0-abc"
    assert written["bin"] == "terrascope.exe"


def test_a_zip_with_the_exe_inside_a_folder_is_refused(tmp_path):
    # A zip of dist/ (terrascope/terrascope.exe) would need extract_dir: refuse it instead.
    zip_path = make_zip(tmp_path / "wrong.zip", {"terrascope/terrascope.exe": b"exe",
                                                 "terrascope/_internal/x.dll": b"dll"})
    output = tmp_path / "terrascope-br.json"
    with pytest.raises(SystemExit, match="no terrascope.exe at the root of the zip"):
        scoop.main(["--version", "1.0.0", "--zip", str(zip_path), "--url", URL, "--output", str(output)])
    assert not output.exists()


def test_a_zip_without_the_exe_is_refused(tmp_path):
    zip_path = make_zip(tmp_path / "empty.zip", {"_internal/x.dll": b"dll"})
    with pytest.raises(ValueError, match="top level: _internal"):
        scoop.check_layout(zip_path)


def test_the_check_follows_the_bin_name(tmp_path):
    zip_path = make_zip(tmp_path / "other.zip", {"tool.exe": b"exe"})
    scoop.check_layout(zip_path, "tool.exe")
    with pytest.raises(ValueError):
        scoop.check_layout(zip_path)   # default bin is terrascope.exe
