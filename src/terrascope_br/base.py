"""Shared shape of the BR layers.

Data lives in the cache directory. `fetch()` writes it (background thread,
via Layer.tick / refresh_if_due); `load()` reads it back on the UI thread
(reload). A failed fetch keeps the old cache, shows the reason in the legend,
and never raises into the screen."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from terrascope.core.config import FETCH_NEW_DATA_FROM_API
from terrascope.core.layer import Layer, SidebarLine

from terrascope_br import store
from terrascope_br._i18n import _
from terrascope_br.net import SourceError

def utc_text(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%d/%m %H:%M UTC")

class SourceLayer(Layer):
    enabled = False            # only on while the BRASIL tab is active
    source_name = ""           # legend title, e.g. "INPE fires"
    credit = ""                # shown in the tab attribution and the legend
    refresh_seconds = 600      # minimum time between two fetches
    stale_after_seconds = 1800 # older data is announced as "no update since"
    fetch_timeout = 30.0

    def __init__(self) -> None:
        super().__init__()
        self.items: list = []
        self.data_time: float | None = None   # epoch of the data shown
        self.error = ""                       # reason of the last failed fetch
        self.status = ""                      # legend line about freshness

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
            # Let the base class back off and retry; the message is in the legend.
            raise
        self.error = ""
        return True

    def reload(self) -> None:
        try:
            self.items, self.data_time = self.parse()
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.items, self.data_time = [], None
            self.error = self.error or f"cache unreadable: {error}"
        self.status = self.freshness_text()
        self.dirty = True

    def clear(self) -> None:
        self.items = []
        self.status = ""
        self.dirty = True

    def freshness_text(self) -> str:
        if self.data_time is None:
            if self.error:
                return _("unavailable: %s") % self.error
            return _("no data yet")
        if time.time() - self.data_time > self.stale_after_seconds:
            return _("no update since %s") % utc_text(self.data_time)
        return _("updated %s") % utc_text(self.data_time)

    # -- legend and info box ---------------------------------------------------------
    def legend_lines(self) -> list[SidebarLine]:
        lines = [SidebarLine(text=self.source_name, bold=True)]
        lines.extend(self.legend_rows())
        lines.append(SidebarLine(text=self.status))
        if self.credit:
            lines.append(SidebarLine(text=self.credit))
        return lines

    def legend_rows(self) -> list[SidebarLine]:
        return []
