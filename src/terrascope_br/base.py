"""Shared shape of the BR layers.

Data lives in the cache directory. `fetch()` writes it (background thread,
via Layer.tick / refresh_if_due); `parse()` reads it back on the UI thread
(reload). A failed fetch keeps the old cache and says why in the legend; a
failure never raises into the screen.

The legend of a layer is at most two lines: its title, then either its colour
key or, when something is wrong with the data, that message."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from terrascope.core.config import FETCH_NEW_DATA_FROM_API
from terrascope.core.layer import Layer, SidebarLine

from terrascope_br import store
from terrascope_br._i18n import _
from terrascope_br.net import SourceError

SWATCH = "■"  # the glyph drawn in front of each colour of a legend key

def utc_text(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%d/%m %H:%M UTC")

def key(*pairs: tuple[str, str]) -> tuple[tuple[str, str, str], ...]:
    """A legend key from (colour, label) pairs."""
    return tuple((SWATCH, colour, label) for colour, label in pairs)

class SourceLayer(Layer):
    enabled = False            # only on while the BRASIL tab is active
    source_name = ""           # legend title, e.g. "INPE fires"
    credit = ""                # shown in the tab attribution, not in the legend
    refresh_seconds = 600      # minimum time between two fetches
    stale_after_seconds = 1800 # older data is announced as "no update since"
    fetch_timeout = 30.0

    def __init__(self) -> None:
        super().__init__()
        self.items: list = []
        self.data_time: float | None = None   # epoch of the data shown
        self.error = ""                       # reason of the last failed fetch
        self.read_error = ""                  # reason the cache could not be read

    # -- what each source implements ------------------------------------------------
    def stamp_file(self):
        """The cache file whose age tells when the data was last fetched."""
        raise NotImplementedError

    def fetch(self) -> None:
        """Download and write the cache. Raises SourceError on failure."""
        raise NotImplementedError

    def parse(self) -> tuple[list, float | None]:
        """Read the cache; return (items, epoch of the data or None)."""
        raise NotImplementedError

    # -- refresh cycle -----------------------------------------------------------------
    def cache_age(self) -> float | None:
        return store.age(self.stamp_file())

    def is_due(self) -> bool:
        age = self.cache_age()
        return age is None or age >= self.refresh_seconds

    def refresh_if_due(self, force: bool = False) -> bool:
        if not self.enabled or not FETCH_NEW_DATA_FROM_API:
            return False
        if not force and not self.is_due():
            return False
        try:
            self.fetch()
        except SourceError as error:
            self.error = str(error)
            raise  # the base class backs off and retries
        self.error = ""
        return True

    def reload(self) -> None:
        # Runs on the UI thread after a fetch, and at startup. The legend text is
        # worked out from the current state in legend_lines(), not stored here, so
        # a failure or an ageing cache shows up without another reload.
        try:
            self.items, self.data_time = self.parse()
            self.read_error = ""
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.items, self.data_time = [], None
            self.read_error = str(error)
        self.dirty = True

    def clear(self) -> None:
        self.items = []
        self.dirty = True

    def is_stale(self) -> bool:
        return self.data_time is not None and time.time() - self.data_time > self.stale_after_seconds

    def has_problem(self) -> bool:
        """True when the legend must show a message instead of the colour key."""
        return bool(self.error or self.read_error) or self.data_time is None or self.is_stale()

    def freshness_text(self) -> str:
        """What the legend says about the data, from the state right now."""
        if self.data_time is None:
            if self.error:
                return _("unavailable: %s") % self.error
            if self.read_error:
                return _("cache unreadable: %s") % self.read_error
            return _("no data yet")
        if self.error:
            return _("update failed: %s") % self.error
        if self.is_stale():
            return _("no update since %s") % utc_text(self.data_time)
        return _("updated %s") % utc_text(self.data_time)

    @property
    def status(self) -> str:
        """The freshness text, as it is now (kept for callers that read it)."""
        return self.freshness_text()

    # -- legend and info box ---------------------------------------------------------
    def legend_key(self) -> tuple[tuple[str, str, str], ...]:
        """(glyph, colour, label) items of the colour key; empty = no key."""
        return ()

    def legend_title(self) -> str:
        return self.source_name

    def legend_lines(self) -> list[SidebarLine]:
        lines = [SidebarLine(text=self.legend_title(), bold=True)]
        if self.has_problem():
            lines.append(SidebarLine(text=self.freshness_text()))
        elif self.legend_key():
            lines.append(SidebarLine(text="", legend_items=self.legend_key()))
        return lines
