"""Earthquakes in Brazil and its neighbours, last 30 days.

The main source is the IAG-USP seismology catalogue (FDSN text). It also has
small events that the USGS feed (magnitude 2.5 and up) does not show. When the
IAG-USP query fails (network error, HTTP error, empty or unreadable answer),
the layer uses the USGS feed for the same box and window instead. The source
in use is written next to the cache, shown in the legend title and in the
info box of each quake.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from terrascope.core.layer import LayerRender, Marker

from terrascope_br import store
from terrascope_br._i18n import _
from terrascope_br.base import SourceLayer, key
from terrascope_br.net import SourceError, get

QUERY_URL = "https://moho.iag.usp.br/fdsnws/event/1/query"
# All earthquakes of the last 30 days, the same window as the IAG-USP query.
USGS_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_month.geojson"
BOX = {"minlatitude": -34, "maxlatitude": 6, "minlongitude": -74, "maxlongitude": -34}
WINDOW_DAYS = 30
BRASILIA = timezone(timedelta(hours=-3))
# (minimum magnitude, colour, glyph), from the smallest band up.
BANDS = ((0.0, "#7fb8d6", "o"), (3.0, "#f2c14e", "o"), (4.5, "#ff9f43", "O"), (6.0, "#ff4d4d", "O"))

SOURCE_USP = "IAG-USP"
SOURCE_USGS = "USGS"  # the fallback; shown as "USGS (fallback)"

def query_url(now: datetime) -> str:
    params = {
        "starttime": (now - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%S"),
        "endtime": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "format": "text",
        **BOX,
    }
    return QUERY_URL + "?" + urlencode(params)

def source_label(source: str) -> str:
    return _("USGS (fallback)") if source == SOURCE_USGS else SOURCE_USP

def band_of(magnitude: float | None) -> tuple[str, str]:
    if magnitude is None:
        return BANDS[0][1], "o"
    chosen = BANDS[0]
    for band in BANDS:
        if magnitude >= band[0]:
            chosen = band
    return chosen[1], chosen[2]

def parse_text(text: str) -> list[dict]:
    """Rows of the FDSN text format; the header line starts with '#'.
    An empty or foreign answer raises ValueError."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith("#EventID"):
        raise ValueError("not the IAG-USP event table")
    columns = [column.strip() for column in lines[0].lstrip("#").split("|")]
    quakes = []
    for line in lines[1:]:
        if not line.strip():
            continue
        row = dict(zip(columns, (value.strip() for value in line.split("|"))))
        try:
            when = datetime.fromisoformat(row["Time"]).replace(tzinfo=timezone.utc)
            latitude = float(row["Latitude"])
            longitude = float(row["Longitude"])
        except (KeyError, ValueError):
            continue
        try:
            magnitude = float(row.get("Magnitude", ""))
        except ValueError:
            magnitude = None
        try:
            depth = float(row.get("Depth/km", ""))
        except ValueError:
            depth = None
        quakes.append({
            "id": row.get("EventID", ""),
            "epoch": when.timestamp(),
            "lat": latitude,
            "lon": longitude,
            "depth": depth,
            "magnitude": magnitude,
            "mag_type": row.get("MagType", ""),
            "place": row.get("EventLocationName", "") or _("unknown place"),
            "source": SOURCE_USP,
        })
    return quakes

def parse_usgs(body: bytes) -> list[dict]:
    """Quakes of the USGS GeoJSON feed that fall inside BOX. Raises ValueError
    when the answer is not a feed."""
    document = json.loads(body.decode("utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("features"), list):
        raise ValueError("not a USGS earthquake feed")
    quakes = []
    for feature in document["features"]:
        try:
            longitude, latitude = (float(value) for value in feature["geometry"]["coordinates"][:2])
            properties = feature["properties"]
            epoch = float(properties["time"]) / 1000.0
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if not (BOX["minlatitude"] <= latitude <= BOX["maxlatitude"]
                and BOX["minlongitude"] <= longitude <= BOX["maxlongitude"]):
            continue
        depth = feature["geometry"]["coordinates"][2] if len(feature["geometry"]["coordinates"]) > 2 else None
        magnitude = properties.get("mag")
        quakes.append({
            "id": str(feature.get("id", "")),
            "epoch": epoch,
            "lat": latitude,
            "lon": longitude,
            "depth": float(depth) if depth is not None else None,
            "magnitude": float(magnitude) if magnitude is not None else None,
            "mag_type": str(properties.get("magType") or ""),
            "place": str(properties.get("place") or "") or _("unknown place"),
            "source": SOURCE_USGS,
        })
    return quakes

class QuakeLayer(SourceLayer):
    name = "br_quakes"
    toggle_key = "b"
    source_name = _("IAG-USP quakes, 30 days")
    info_title = _("QUAKE")
    credit = "IAG-USP"
    refresh_seconds = 1800          # the catalogue changes slowly; the query takes ~12 s
    stale_after_seconds = 24 * 3600
    fetch_timeout = 60.0

    def __init__(self) -> None:
        super().__init__()
        self.source_used = SOURCE_USP  # set by parse(): the feed the cache holds

    def stamp_file(self):
        return store.path("iag", "source.txt")

    def _usp_file(self):
        return store.path("iag", "quakes.txt")

    def _usgs_file(self):
        return store.path("iag", "usgs_month.geojson")

    def fetch(self) -> None:
        """IAG-USP first; USGS when IAG-USP cannot give a usable table."""
        try:
            _status, body, _headers = get(query_url(datetime.now(timezone.utc)), 60.0, min_gap=1.0)
            parse_text(body.decode("utf-8", "replace"))  # ValueError when not the table
        except (SourceError, ValueError) as error:
            usp_error = str(error)
        else:
            store.write_bytes(self._usp_file(), body)
            store.write_bytes(self.stamp_file(), SOURCE_USP.encode("utf-8"))
            return
        try:
            _status, body, _headers = get(USGS_URL, 30.0, min_gap=1.0)
            parse_usgs(body)  # ValueError when not a feed
        except (SourceError, ValueError) as error:
            raise SourceError(f"IAG-USP: {usp_error}; USGS: {error}") from error
        store.write_bytes(self._usgs_file(), body)
        store.write_bytes(self.stamp_file(), SOURCE_USGS.encode("utf-8"))

    def parse(self):
        stamp = self.stamp_file()
        if store.age(stamp) is None:
            return [], None
        source = (store.read_bytes(stamp) or b"").decode("utf-8", "replace").strip()
        fetched = time.time() - (store.age(stamp) or 0.0)
        if source == SOURCE_USGS:
            data = store.read_bytes(self._usgs_file())
            items = parse_usgs(data) if data is not None else []
        else:
            source = SOURCE_USP
            data = store.read_bytes(self._usp_file())
            items = parse_text(data.decode("utf-8", "replace")) if data is not None else []
        self.source_used = source
        return items, fetched

    def legend_title(self) -> str:
        if self.source_used == SOURCE_USGS:
            return _("USGS quakes (fallback), 30 days")
        return self.source_name

    def build(self, view) -> LayerRender:
        markers = []
        for quake in self.items:
            cell = view.to_cell(quake["lon"], quake["lat"])
            if cell is None:
                continue
            color, glyph = band_of(quake["magnitude"])
            markers.append(Marker(cell[0], cell[1], color, glyph=glyph, item=quake,
                                  selected=self.selected is not None and quake["id"] == self.selected.get("id")))
        return LayerRender(None, "#000000", markers=markers)

    def legend_key(self):
        return key(
            (BANDS[0][1], _("< 3")),
            (BANDS[1][1], _("3-4.4")),
            (BANDS[2][1], _("4.5-5.9")),
            (BANDS[3][1], _("6+")),
        )

    def info_rows(self) -> list[tuple[str, str]]:
        quake = self.selected
        if not quake:
            return []
        when_utc = datetime.fromtimestamp(quake["epoch"], timezone.utc)
        magnitude = _("N/A") if quake["magnitude"] is None else f"M {quake['magnitude']:.1f} {quake['mag_type']}".strip()
        depth = _("N/A") if quake["depth"] is None else f"{quake['depth']:.0f} km"
        return [
            (_("SOURCE"), source_label(quake.get("source", SOURCE_USP))),
            (_("MAGNITUDE"), magnitude),
            (_("PLACE"), quake["place"]),
            (_("DEPTH"), depth),
            (_("UTC"), when_utc.strftime("%d/%m %H:%M")),
            (_("LOCAL"), when_utc.astimezone(BRASILIA).strftime("%d/%m %H:%M") + " (-03)"),
            (_("POSITION"), f"{quake['lat']:.3f}, {quake['lon']:.3f}"),
        ]
