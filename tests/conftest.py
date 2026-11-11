"""Test setup: isolated config and cache folders, and the pt_BR catalogue compiled."""

import os
import pathlib
import tempfile

_ROOT = tempfile.mkdtemp(prefix="terrascope-br-tests-")
os.environ.setdefault("XDG_CONFIG_HOME", os.path.join(_ROOT, "config"))
os.environ.setdefault("XDG_CACHE_HOME", os.path.join(_ROOT, "cache"))
os.environ.pop("terrascope_CONFIG", None)
os.environ.pop("terrascope_LOCALE_DIR", None)

LOCALE = pathlib.Path(__file__).resolve().parents[1] / "src" / "terrascope_br" / "locale"


def _compile_catalogues() -> None:
    """The .mo files are build output (not in git). Compile them when missing."""
    try:
        from babel.messages.mofile import write_mo
        from babel.messages.pofile import read_po
    except ImportError:
        return
    for po in LOCALE.glob("*/LC_MESSAGES/terrascope_br.po"):
        mo = po.with_suffix(".mo")
        if mo.exists() and mo.stat().st_mtime >= po.stat().st_mtime:
            continue
        with open(po, "rb") as source:
            catalog = read_po(source)
        with open(mo, "wb") as target:
            write_mo(target, catalog)


_compile_catalogues()

from terrascope.core import i18n  # noqa: E402  (after the environment above)

i18n.activate("en")  # tests read English unless they switch; the machine may be pt-BR
