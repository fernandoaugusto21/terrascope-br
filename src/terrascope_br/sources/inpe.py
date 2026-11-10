"""INPE Queimadas: fire hotspots of the last 3 hours, from the 10-minute CSV
files (GOES). Only the files that are not in the cache yet are downloaded."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

from terrascope.core.layer import LayerRender, Marker

from terrascope_br import store
from terrascope_br._i18n import _
from terrascope_br.base import SourceLayer, key
from terrascope_br.net import SourceError, get

INDEX_URL = "https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/10min/"
FILE_PATTERN = re.compile(r"focos_10min_(\d{8})_(\d{4})\.csv")
WINDOW = timedelta(hours=3)
CACHE_KEEP = timedelta(hours=4)
BRASILIA = timezone(timedelta(hours=-3))
# (age limit in seconds, colour): younger than 1 h, 2 h, otherwise 3 h.
AGE_BANDS = ((3600, "#ff3b30"), (7200, "#ff9500"), (10800, "#ffd60a"))

def file_time(name: str) -> datetime | None:
    match = FILE_PATTERN.fullmatch(name)
    if not match:
        return None
    stamp = match.group(1) + match.group(2)
    return datetime.strptime(stamp, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)

def wanted_files(index_html: str, now: datetime) -> list[str]:
    """Names of the 10-minute files that can hold the last 3 hours, oldest first."""
    cutoff = now - WINDOW - timedelta(minutes=10)
    names = {match.group(0) for match in FILE_PATTERN.finditer(index_html)}
    chosen = [name for name in names if file_time(name) is not None and file_time(name) >= cutoff]
    return sorted(chosen)

def parse_csv(text: str, now_epoch: float) -> list[dict]:
    """Focus rows of one CSV (lat,lon,satelite,data). The data column is
    read as UTC: the file does not say which time zone it uses."""
    rows = []
    for line in text.splitlines()[1:]:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            latitude, longitude = float(parts[0]), float(parts[1])
            when = datetime.strptime(parts[3], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        epoch = when.timestamp()
        if now_epoch - epoch > WINDOW.total_seconds() or epoch > now_epoch + 600:
            continue
        rows.append({"lat": latitude, "lon": longitude, "satellite": parts[2], "epoch": epoch})
    return rows

def fire_color(age_seconds: float) -> str:
    for limit, color in AGE_BANDS:
        if age_seconds < limit:
            return color
    return AGE_BANDS[-1][1]

def _same_fire(item: dict, selected: dict | None) -> bool:
    """Items are rebuilt on every reload, so compare fields, not identity."""
    return (selected is not None and selected.get("epoch") == item["epoch"]
            and selected.get("lat") == item["lat"] and selected.get("lon") == item["lon"])

class FireLayer(SourceLayer):
    name = "br_fires"
    toggle_key = "f"
    source_name = _("INPE fires, 3 h")
    info_title = _("FIRE")
    credit = "INPE"
    refresh_seconds = 600
    stale_after_seconds = 1800

    def stamp_file(self):
        return store.path("inpe", "index.txt")

    def fetch(self) -> None:
        _status, body, _headers = get(INDEX_URL, 30.0, min_gap=1.0)
        now = datetime.now(timezone.utc)
        names = wanted_files(body.decode("utf-8", "replace"), now)
        if not names:
            raise SourceError("INPE index lists no recent files")
        folder = store.path("inpe", "csv")
        failures = 0
        for name in names:
            if (folder / name).exists():
                continue
            try:
                _status, csv_body, _headers = get(INDEX_URL + name, 30.0, min_gap=1.0)
            except SourceError:
                failures += 1
                continue
            store.write_bytes(folder / name, csv_body)
        if folder.exists():
            for old in folder.iterdir():
                when = file_time(old.name)
                if when is not None and now - when > CACHE_KEEP:
                    old.unlink(missing_ok=True)
        if failures == len(names):
            raise SourceError("no INPE focus file could be downloaded")
        store.write_bytes(self.stamp_file(), "\n".join(names).encode("utf-8"))

    def parse(self):
        stamp = self.stamp_file()
        if store.age(stamp) is None:
            return [], None  # never fetched: there is no time to show
        names = (store.read_bytes(stamp) or b"").decode("utf-8").split()
        fetched = time.time() - store.age(stamp)
        now = time.time()
        folder = store.path("inpe", "csv")
        seen = set()
        items = []
        for name in names:
            data = store.read_bytes(folder / name)
            if data is None:
                continue
            for row in parse_csv(data.decode("utf-8", "replace"), now):
                key = (row["lat"], row["lon"], row["satellite"], row["epoch"])
                if key not in seen:
                    seen.add(key)
                    items.append(row)
        return items, fetched

    def build(self, view) -> LayerRender:
        now = time.time()
        markers = []
        for item in self.items:
            cell = view.to_cell(item["lon"], item["lat"])
            if cell is None:
                continue
            markers.append(Marker(cell[0], cell[1], fire_color(now - item["epoch"]),
                                  glyph="*", item=item, selected=_same_fire(item, self.selected)))
        return LayerRender(None, "#000000", markers=markers)

    def legend_key(self):
        return key(
            (AGE_BANDS[0][1], _("< 1 h")),
            (AGE_BANDS[1][1], _("1-2 h")),
            (AGE_BANDS[2][1], _("2-3 h")),
        )

    def info_rows(self) -> list[tuple[str, str]]:
        item = self.selected
        if not item:
            return []
        when_utc = datetime.fromtimestamp(item["epoch"], timezone.utc)
        when_local = when_utc.astimezone(BRASILIA)
        age_minutes = max(0.0, (time.time() - item["epoch"]) / 60)
        return [
            (_("SATELLITE"), item["satellite"]),
            (_("UTC"), when_utc.strftime("%d/%m %H:%M")),
            (_("LOCAL"), when_local.strftime("%d/%m %H:%M") + " (-03)"),
            (_("AGE"), f"{age_minutes:.0f} min"),
            (_("POSITION"), f"{item['lat']:.3f}, {item['lon']:.3f}"),
        ]
