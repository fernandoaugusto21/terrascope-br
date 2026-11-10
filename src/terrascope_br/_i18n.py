"""Translation of this package's texts.

The texts are written in English in the source, marked with _(). Their
Portuguese (and any other) translations live in this package's own catalogue,
domain "terrascope_br", under locale/<lang>/LC_MESSAGES/terrascope_br.mo. The
core supplies the active language and the catalogue loader (translation()).
If the core has no i18n module (an older Terrascope), the English text is used.
"""

from __future__ import annotations

import functools
from pathlib import Path

try:
    from terrascope.core import i18n as _core_i18n
except ImportError:  # an older Terrascope without translation support
    _core_i18n = None

DOMAIN = "terrascope_br"
LOCALE_DIR = Path(__file__).resolve().parent / "locale"


@functools.lru_cache(maxsize=None)
def _catalog(language: str):
    """The catalogue for the active language, loaded once per language."""
    return _core_i18n.translation(DOMAIN, LOCALE_DIR)


def _(message: str) -> str:
    if _core_i18n is None:
        return message
    return _catalog(_core_i18n.language()).gettext(message)
