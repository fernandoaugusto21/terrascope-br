"""SP Águas rain gauges (SIBH): one reading per station for the last hour.
Stations come from CEMADEN, IAC, ANA, SABESP, SP Águas and SAISP. The city
name comes from the IBGE code in the reading (see ibge.py)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from terrascope.core.layer import LayerRender, Marker, SidebarLine

from terrascope_br import store
from terrascope_br._i18n import _
from terrascope_br.base import SourceLayer
from terrascope_br.sources.ibge import ensure_municipality_names, municipality_names
from terrascope_br.net import SourceError, get

MEASUREMENTS_URL = "https://apps.spaguas.sp.gov.br/sibh/api/v2/measurements/now?station_type_id=2&hours=1"
# (lower limit in mm, colour), from the lightest rain up. Zero rain is dimmed.
RAIN_BANDS = ((0.0, "#8fd3ff"), (2.0, "#2e86de"), (10.0, "#f7b731"), (25.0, "#eb3b5a"))
DRY_COLOR = "#4f6272"

def rain_color(mm: float) -> tuple[str, bool]:
    """Colour of a reading, and whether to draw it dimmed (no rain)."""
    if mm <= 0:
        return DRY_COLOR, True
    chosen = RAIN_BANDS[0][1]
    for limit, color in RAIN_BANDS:
        if mm > limit:
            chosen = color
    return chosen, False

def parse_measurements(payload: bytes, names: dict[str, tuple[str, str]]) -> list[dict]:
    """Readings with coordinates and a value; anything else is skipped."""
    document = json.loads(payload.decode("utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("measurements"), list):
        raise ValueError("no measurements list")
    readings = []
    for raw in document["measurements"]:
        try:
            latitude = float(raw["latitude"])
            longitude = float(raw["longitude"])
            mm = float(raw["value"])
        except (KeyError, TypeError, ValueError):
            continue
        code = str(raw.get("cod_ibge") or "")
        city, uf = names.get(code, ("", ""))
        updated = None
        try:
            updated = datetime.fromisoformat(str(raw["max_date"]).replace("Z", "+00:00"))
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            updated = updated.timestamp()
        except (KeyError, TypeError, ValueError):
            updated = None
        readings.append({
            "lat": latitude,
            "lon": longitude,
            "mm": mm,
            "station": str(raw.get("station_name") or _("unnamed station")).strip(),
            "owner": str(raw.get("station_owner") or _("unknown")),
            "city": f"{city}/{uf}" if city else (code or _("unknown")),
            "updated": updated,
        })
    return readings

class RainGaugeLayer(SourceLayer):
    name = "br_gauges"
    toggle_key = "g"
    source_name = _("SP Águas rain gauges, 1 h")
    info_title = _("RAIN GAUGE")
    credit = "SP Águas"
    refresh_seconds = 600
    stale_after_seconds = 1800

    def stamp_file(self):
        return store.path("spaguas", "measurements.json")

    def fetch(self) -> None:
        _status, body, _headers = get(MEASUREMENTS_URL, 30.0, min_gap=1.0)
        try:
            parse_measurements(body, {})
        except (ValueError, UnicodeDecodeError) as error:
            raise SourceError(f"SP Águas returned an unexpected payload: {error}") from error
        store.write_bytes(self.stamp_file(), body)
        try:
            ensure_municipality_names()
        except SourceError:
            # City names are optional: the gauges still show with the IBGE code.
            pass

    def parse(self):
        file = self.stamp_file()
        data = store.read_bytes(file)
        if data is None:
            return [], None
        readings = parse_measurements(data, municipality_names())
        return readings, time.time() - (store.age(file) or 0.0)

    def build(self, view) -> LayerRender:
        markers = []
        for reading in self.items:
            cell = view.to_cell(reading["lon"], reading["lat"])
            if cell is None:
                continue
            color, dim = rain_color(reading["mm"])
            markers.append(Marker(cell[0], cell[1], color, glyph="o", item=reading,
                                  dim=dim, selected=self._is_selected(reading)))
        return LayerRender(None, "#000000", markers=markers)

    def _is_selected(self, reading: dict) -> bool:
        selected = self.selected
        return selected is not None and selected.get("lat") == reading["lat"] \
            and selected.get("lon") == reading["lon"] and selected.get("station") == reading["station"]

    def legend_rows(self) -> list[SidebarLine]:
        return [
            SidebarLine(text=_("0 mm"), swatches=(DRY_COLOR,)),
            SidebarLine(text=_("0-2 mm"), swatches=(RAIN_BANDS[0][1],)),
            SidebarLine(text=_("2-10 mm"), swatches=(RAIN_BANDS[1][1],)),
            SidebarLine(text=_("10-25 mm"), swatches=(RAIN_BANDS[2][1],)),
            SidebarLine(text=_("over 25 mm"), swatches=(RAIN_BANDS[3][1],)),
        ]

    def info_rows(self) -> list[tuple[str, str]]:
        reading = self.selected
        if not reading:
            return []
        updated = "N/A"
        if reading.get("updated") is not None:
            updated = datetime.fromtimestamp(reading["updated"], timezone.utc).strftime("%d/%m %H:%M UTC")
        return [
            (_("STATION"), reading["station"]),
            (_("OWNER"), reading["owner"]),
            (_("CITY"), reading["city"]),
            (_("LAST HOUR"), f"{reading['mm']:.1f} mm"),
            (_("UPDATED"), updated),
        ]
