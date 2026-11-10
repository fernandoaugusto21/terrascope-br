"""Translation hook: uses Terrascope's `_()` when the core provides it."""

try:
    from terrascope.core.i18n import _
except ImportError:  # the core without i18n support: keep English text
    def _(message: str) -> str:
        return message
