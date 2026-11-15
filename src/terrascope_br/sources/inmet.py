"""INMET warnings (avisos ativos): the warnings of the national weather service,
drawn as the areas they cover.

One request gives two lists, "hoje" (today) and "futuro" (the next days). A
warning can be in both, so each id is kept once. Its area comes as GeoJSON text
in lon/lat order, and each warning has its own colour (aviso_cor). The cells
inside each area are filled with that colour; a marker at the centre of the
area's box is the one the info box describes.

Warnings with no usable area are left out. Times are shown as the API gives
them: the API does not say which time zone they are in.

When the data is older than the stale limit the areas are hidden and the legend
says when the service last answered, so an expired warning is not shown as
current."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime

import numpy as np

from terrascope.core.layer import LayerRender, Marker, SidebarLine
from terrascope.core.view import View

from terrascope_br import store
from terrascope_br._i18n import _
from terrascope_br.base import SourceLayer, key
from terrascope_br.geo import Ring, rings_from_geojson, view_key
from terrascope_br.net import SourceError, get

ALERTS_URL = "https://apiprevmet3.inmet.gov.br/avisos/ativos"
DEFAULT_COLOR = "#ffd60a"           # used when a warning has no usable colour
MARKER_COLOR = "#000000"            # the mark drawn on a warning's own colour
COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{6}")

def _colour(raw) -> str:
    text = str(raw or "").strip()
    return text.lower() if COLOR_PATTERN.fullmatch(text) else DEFAULT_COLOR

def _area(raw) -> list[Ring]:
    """Rings of the warning's area. The API sends the GeoJSON as text."""
    value = raw
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, dict):
        return []
    try:
        return rings_from_geojson({"features": [{"geometry": value}]})
    except (ValueError, TypeError, IndexError):
        return []

def _count(codes) -> int:
    return len([code for code in str(codes or "").split(",") if code.strip()])

def _when(text: str) -> str:
    """'2026-11-14 20:00' -> '14/11 20:00'. Other text is shown as it is."""
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M").strftime("%d/%m %H:%M")
    except ValueError:
        return text

def _alert(raw: dict) -> dict | None:
    """One warning as the layer uses it, or None when it has no area or is closed."""
    if raw.get("encerrado") is True:
        return None
    rings = _area(raw.get("poligono"))
    if not rings:
        return None
    ident = str(raw.get("id") or raw.get("codigo") or "").strip()
    if not ident:
        return None
    lon = (min(ring.min_lon for ring in rings) + max(ring.max_lon for ring in rings)) / 2
    lat = (min(ring.min_lat for ring in rings) + max(ring.max_lat for ring in rings)) / 2
    return {
        "id": ident,
        "event": str(raw.get("descricao") or _("unnamed warning")).strip(),
        "severity": str(raw.get("severidade") or "").strip(),
        "color": _colour(raw.get("aviso_cor")),
        "start": str(raw.get("inicio") or ""),
        "end": str(raw.get("fim") or ""),
        "states": str(raw.get("estados") or "").strip(),
        "municipalities": _count(raw.get("geocodes")),
        "rings": rings,
        "lon": lon,
        "lat": lat,
    }

def parse_alerts(payload: bytes) -> list[dict]:
    """The warnings of "hoje" and "futuro", each id once. Raises ValueError when the
    payload does not have the two lists. An empty list is a valid answer."""
    document = json.loads(payload.decode("utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("hoje"), list) \
            or not isinstance(document.get("futuro"), list):
        raise ValueError("no hoje and futuro lists")
    alerts: list[dict] = []
    seen: set[str] = set()
    for raw in document["hoje"] + document["futuro"]:
        if not isinstance(raw, dict):
            continue
        ident = str(raw.get("id") or raw.get("codigo") or "")
        if ident in seen:
            continue
        seen.add(ident)
        alert = _alert(raw)
        if alert is not None:
            alerts.append(alert)
    return alerts

def _inside_ring(lon: np.ndarray, lat: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Crossing-number test of every grid point against one ring, one edge at a time."""
    inside = np.zeros(lon.shape, dtype=bool)
    x0, y0 = points[:, 0], points[:, 1]
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    for xa, ya, xb, yb in zip(x0.tolist(), y0.tolist(), x1.tolist(), y1.tolist()):
        if ya == yb:
            continue
        crosses = (ya > lat) != (yb > lat)
        x_at = (xb - xa) * (lat - ya) / (yb - ya) + xa
        inside ^= crosses & (lon < x_at)
    return inside

def warning_cells(alerts: list[dict], view: View) -> dict[tuple[int, int], str]:
    """Colour of each terminal cell of `view` whose centre lies inside a warning's
    area. Rings are combined by the even-odd rule, so holes stay empty. Later
    warnings in the list are drawn over earlier ones."""
    cols = np.arange(view.width) + 0.5
    rows = np.arange(view.height) + 0.5
    lons = view.origin_lon + cols * view.lon_span / view.width
    # Wrapped to [-180, 180) as in chuva_online.py, so a view across the antimeridian works.
    lons = (lons + 180.0) % 360.0 - 180.0
    lats = view.origin_lat - rows * view.lat_span / view.height
    grid_lon, grid_lat = np.meshgrid(lons, lats)
    cells: dict[tuple[int, int], str] = {}
    for alert in alerts:
        inside = np.zeros(grid_lon.shape, dtype=bool)
        for ring in alert["rings"]:
            if (ring.max_lon < lons.min() or ring.min_lon > lons.max()
                    or ring.max_lat < lats.min() or ring.min_lat > lats.max()):
                continue
            inside ^= _inside_ring(grid_lon, grid_lat, ring.points)
        ys, xs = np.nonzero(inside)
        for y, x in zip(ys.tolist(), xs.tolist()):
            cells[(x, y)] = alert["color"]
    return cells

class InmetAlertsLayer(SourceLayer):
    name = "br_inmet_alerts"
    toggle_key = "v"
    source_name = _("INMET warnings")
    info_title = _("WARNING")
    credit = "INMET"
    refresh_seconds = 1800
    stale_after_seconds = 3600

    def __init__(self) -> None:
        super().__init__()
        self._cells_key = None
        self._cells: dict[tuple[int, int], str] = {}

    def stamp_file(self):
        return store.path("inmet", "avisos_ativos.json")

    def fetch(self) -> None:
        _status, body, _headers = get(ALERTS_URL, 30.0, min_gap=1.0)
        try:
            parse_alerts(body)
        except (ValueError, UnicodeDecodeError) as error:
            raise SourceError(f"INMET returned an unexpected payload: {error}") from error
        store.write_bytes(self.stamp_file(), body)

    def parse(self):
        file = self.stamp_file()
        data = store.read_bytes(file)
        if data is None:
            return [], None
        return parse_alerts(data), time.time() - (store.age(file) or 0.0)

    def clear(self) -> None:
        super().clear()
        self._cells_key = None
        self._cells = {}

    def build(self, view: View) -> LayerRender:
        if not self.items or self.data_time is None or self.is_stale():
            return LayerRender(None, DEFAULT_COLOR)  # old warnings are not shown as current
        cells_key = (view_key(view), self.data_time)
        if cells_key != self._cells_key:
            self._cells = warning_cells(self.items, view)
            self._cells_key = cells_key
        markers = []
        for alert in self.items:
            cell = view.to_cell(alert["lon"], alert["lat"])
            if cell is None:
                continue
            markers.append(Marker(cell[0], cell[1], MARKER_COLOR, glyph="!", item=alert,
                                  bg_color=alert["color"], selected=self._is_selected(alert)))
        return LayerRender(None, DEFAULT_COLOR, markers=markers, bg_cells=self._cells)

    def _is_selected(self, alert: dict) -> bool:
        selected = self.selected
        return selected is not None and selected.get("id") == alert["id"]

    def legend_key(self):
        # One entry per colour in the data, labelled with the severity it has.
        seen: dict[str, str] = {}
        for alert in self.items:
            seen.setdefault(alert["color"], alert["severity"] or alert["event"])
        return key(*seen.items())

    def legend_lines(self) -> list[SidebarLine]:
        # The base class skips the key line when it is empty, so a day with no
        # warnings gets its own message instead of a blank line.
        lines = [SidebarLine(text=self.legend_title(), bold=True)]
        if self.has_problem():
            lines.append(SidebarLine(text=self.freshness_text()))
        elif not self.items:
            lines.append(SidebarLine(text=_("no active warnings")))
        else:
            lines.append(SidebarLine(text="", legend_items=self.legend_key()))
        return lines

    def info_rows(self) -> list[tuple[str, str]]:
        alert = self.selected
        if not alert:
            return []
        return [
            (_("EVENT"), alert["event"]),
            (_("SEVERITY"), alert["severity"] or _("N/A")),
            (_("VALID"), f"{_when(alert['start'])} - {_when(alert['end'])}"),
            (_("STATES"), alert["states"] or _("N/A")),
            (_("MUNICIPALITIES"), str(alert["municipalities"])),
        ]
