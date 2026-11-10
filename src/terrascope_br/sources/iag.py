"""IAG-USP seismology catalogue (FDSN text): earthquakes in Brazil and its
neighbours, last 30 days. The catalogue also has small events that the USGS
feed (magnitude 2.5 and up) does not show."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from terrascope.core.layer import LayerRender, Marker, SidebarLine

from terrascope_br import store
from terrascope_br._i18n import _
from terrascope_br.base import SourceLayer
from terrascope_br.net import SourceError, get

QUERY_URL = "https://moho.iag.usp.br/fdsnws/event/1/query"
BOX = {"minlatitude": -34, "maxlatitude": 6, "minlongitude": -74, "maxlongitude": -34}
WINDOW_DAYS = 30
BRASILIA = timezone(timedelta(hours=-3))
# (minimum magnitude, colour, glyph), from the smallest band up.
BANDS = ((0.0, "#7fb8d6", "o"), (3.0, "#f2c14e", "o"), (4.5, "#ff9f43", "O"), (6.0, "#ff4d4d", "O"))

def query_url(now: datetime) -> str:
    params = {
        "starttime": (now - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%S"),
        "endtime": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "format": "text",
        **BOX,
    }
    return QUERY_URL + "?" + urlencode(params)

def band_of(magnitude: float | None) -> tuple[str, str]:
    if magnitude is None:
        return BANDS[0][1], "o"
    chosen = BANDS[0]
    for band in BANDS:
        if magnitude >= band[0]:
            chosen = band
    return chosen[1], chosen[2]

def parse_text(text: str) -> list[dict]:
    """Rows of the FDSN text format; the header line starts with '#'."""
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

    def stamp_file(self):
        return store.path("iag", "quakes.txt")

    def fetch(self) -> None:
        url = query_url(datetime.now(timezone.utc))
        _status, body, _headers = get(url, 60.0, min_gap=1.0)
        text = body.decode("utf-8", "replace")
        try:
            parse_text(text)
        except ValueError as error:
            raise SourceError(str(error)) from error
        store.write_bytes(self.stamp_file(), text.encode("utf-8"))

    def parse(self):
        file = self.stamp_file()
        data = store.read_bytes(file)
        if data is None:
            return [], None
        return parse_text(data.decode("utf-8", "replace")), time.time() - (store.age(file) or 0.0)

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

    def legend_rows(self) -> list[SidebarLine]:
        return [
            SidebarLine(text=_("< 3"), swatches=(BANDS[0][1],)),
            SidebarLine(text=_("3 - 4.4"), swatches=(BANDS[1][1],)),
            SidebarLine(text=_("4.5 - 5.9"), swatches=(BANDS[2][1],)),
            SidebarLine(text=_("6 +"), swatches=(BANDS[3][1],)),
        ]

    def info_rows(self) -> list[tuple[str, str]]:
        quake = self.selected
        if not quake:
            return []
        when_utc = datetime.fromtimestamp(quake["epoch"], timezone.utc)
        magnitude = "N/A" if quake["magnitude"] is None else f"M {quake['magnitude']:.1f} {quake['mag_type']}".strip()
        depth = "N/A" if quake["depth"] is None else f"{quake['depth']:.0f} km"
        return [
            (_("MAGNITUDE"), magnitude),
            (_("PLACE"), quake["place"]),
            (_("DEPTH"), depth),
            (_("UTC"), when_utc.strftime("%d/%m %H:%M")),
            (_("LOCAL"), when_utc.astimezone(BRASILIA).strftime("%d/%m %H:%M") + " (-03)"),
            (_("POSITION"), f"{quake['lat']:.3f}, {quake['lon']:.3f}"),
        ]
