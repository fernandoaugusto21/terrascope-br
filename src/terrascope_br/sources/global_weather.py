"""Global rain and city temperature, cropped to Brazil, for the BRASIL tab.

Both layers reuse the core's code instead of copying it:

* br_global_rain: the RainViewer frame of the core's radar. It uses the core's
  fetch functions, its palette and its intensity scale, and is drawn as cell
  backgrounds only inside the outline of Brazil (the IBGE state map that the
  br_ufs layer keeps in its cache; nothing is drawn until that map is there).
  The layer keeps its own copy of the newest frame; it does not share the
  core's frame cache (see the README).
* br_global_temp: the core's city-temperature layer (WeatherLayer), limited to
  the cities of Brazil and without the core's radar and earthquakes.

Draw order: background cells are painted in layer order (a later layer wins),
and every marker is painted after every background. The rain paints backgrounds
and is listed before br_radar, so the IAG-USP radar covers it. The temperature
labels are markers, so they sit on top of both.

Each layer has its own switch (the legend line and its key), on top of the
tab's switch that turns the whole layer on.
"""

from __future__ import annotations

import math
import sys
import time

import numpy as np
from PIL import Image, ImageDraw

from terrascope.core import config
from terrascope.core.layer import Layer, LayerRender, SidebarLine
from terrascope.core.mapdata import MapDataError, load_places
from terrascope.core.view import View
from terrascope.layers.weather.config import (
    CATALOG_REFRESH_SECONDS,
    CATALOG_RETRY_SECONDS,
    CITY_CONFIG,
    INTENSITY_MAX,
    INTENSITY_MIN,
    PLACES_LOADING_TEXT,
    PLACES_RETRY_SECONDS,
    PLACES_UNAVAILABLE_TEXT,
    RADAR_MODE,
)
from terrascope.layers.weather.layer import WeatherLayer
from terrascope.layers.weather.radar import (
    _LEVEL_COUNT,
    RADAR_LEVEL_COLORS,
    RadarFetchError,
    fetch_catalog,
    fetch_frame_raster,
    sample_raster,
)

from terrascope_br._i18n import _
from terrascope_br.base import key as legend_key, utc_text
from terrascope_br.sources.ibge import state_rings

RAIN_ACTION = "br_global_rain.toggle"
TEMP_ACTION = "br_global_temp.toggle"
# min longitude, min latitude, max longitude, max latitude: the grid the outline is drawn on
BRAZIL_BOX = (-74.0, -33.8, -34.7, 5.3)
GRID_STEP = 0.05           # degrees per grid cell (about 5 km)
BRAZIL_COUNTRY = "Brazil"  # the adm0name of Brazil in the Natural Earth places file
STALE_SECONDS = 30 * 60    # the same rule as the IAG-USP radar: old rain is not shown as current
KEY_STEPS = 4              # swatches in the rain key

_grid_cache: dict = {}  # {"rings": the rings it was drawn from, "grid": the grid}


def brazil_grid(rings) -> np.ndarray:
    """True where the grid of BRAZIL_BOX lies inside one of the state rings."""
    min_lon, min_lat, max_lon, max_lat = BRAZIL_BOX
    width = math.ceil((max_lon - min_lon) / GRID_STEP)
    height = math.ceil((max_lat - min_lat) / GRID_STEP)
    image = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(image)
    for ring in rings:
        xs = (ring.points[:, 0] - min_lon) / GRID_STEP
        ys = (max_lat - ring.points[:, 1]) / GRID_STEP
        draw.polygon(list(zip(xs.tolist(), ys.tolist())), fill=255)
    return np.asarray(image) > 0


def current_brazil_grid() -> np.ndarray | None:
    """The Brazil grid from the IBGE state map in the cache, or None while that map is missing."""
    rings = state_rings()
    if not rings:
        return None
    if _grid_cache.get("rings") is not rings:  # state_rings() returns the same list until the file changes
        _grid_cache["rings"] = rings
        _grid_cache["grid"] = brazil_grid(rings)
    return _grid_cache["grid"]


def brazil_cells(view: View, grid: np.ndarray) -> np.ndarray:
    """True for every terminal cell of `view` whose centre lies on the Brazil grid."""
    min_lon, min_lat, max_lon, max_lat = BRAZIL_BOX
    height, width = grid.shape
    cols = np.arange(view.width) + 0.5
    rows = np.arange(view.height) + 0.5
    lons = view.origin_lon + cols * view.lon_span / view.width
    lons = (lons + 180.0) % 360.0 - 180.0  # the view may be wrapped across the antimeridian
    lats = view.origin_lat - rows * view.lat_span / view.height
    px = np.floor((lons - min_lon) / GRID_STEP).astype(int)
    py = np.floor((max_lat - lats) / GRID_STEP).astype(int)
    ok_x = (px >= 0) & (px < width)
    ok_y = (py >= 0) & (py < height)
    hit = grid[np.clip(py, 0, height - 1)[:, None], np.clip(px, 0, width - 1)[None, :]]
    return hit & ok_y[:, None] & ok_x[None, :]


def intensity_to_dbz(value: float) -> float:
    """The core stores RainViewer's dBZ as (dBZ + 10) * 255 / 75 (radar.rgba_to_intensity)."""
    return value * 75.0 / 255.0 - 10.0


def rain_cells(raster: np.ndarray, view: View, grid: np.ndarray) -> dict[tuple[int, int], str]:
    """Background colour of every cell of `view` inside Brazil that has rain.

    The cell takes the stronger of its two half-cell samples, quantised with the
    core's rule: the same levels, the same colours as the core's radar overlay."""
    values = sample_raster(raster, view)  # row 2*y is the top half of cell row y
    cell_values = np.maximum(values[0::2], values[1::2])
    shown = (cell_values >= INTENSITY_MIN) & brazil_cells(view, grid)
    if not shown.any():
        return {}
    span = float(INTENSITY_MAX - INTENSITY_MIN)
    levels = np.clip(((cell_values - INTENSITY_MIN) / span * _LEVEL_COUNT).astype(int), 0, _LEVEL_COUNT - 1)
    cells: dict[tuple[int, int], str] = {}
    rows, cols = np.nonzero(shown)
    for y, x in zip(rows.tolist(), cols.tolist()):
        cells[(x, y)] = RADAR_LEVEL_COLORS[int(levels[y, x])]
    return cells


def rain_key() -> tuple:
    """Legend swatches: the lower edge of four of the core's levels, in dBZ."""
    span = INTENSITY_MAX - INTENSITY_MIN
    pairs = []
    for step in range(KEY_STEPS):
        index = round(step * (_LEVEL_COUNT - 1) / (KEY_STEPS - 1))
        edge = INTENSITY_MIN + index * span / _LEVEL_COUNT
        pairs.append((RADAR_LEVEL_COLORS[index], f"{intensity_to_dbz(edge):.0f} dBZ"))
    return legend_key(*pairs)


class GlobalRainLayer(Layer):
    """RainViewer radar over Brazil, under the BR radar."""

    name = "br_global_rain"
    toggle_key = "j"
    enabled = False  # the BRASIL tab switches it on
    info_title = _("RAIN")

    def __init__(self) -> None:
        super().__init__()
        self.shown = True  # this layer's own switch: its legend line and its key
        self.frame: dict | None = None  # newest observed frame (RainViewer catalogue entry)
        self.raster: np.ndarray | None = None
        self.error = ""
        self.checked_at = 0.0

    # -- switches -------------------------------------------------------------------
    def _flip(self) -> None:
        self.shown = not self.shown
        self.dirty = True

    def handle_key(self, key: int) -> bool:
        if 0 <= key < 256 and chr(key).lower() == self.toggle_key:
            self._flip()
            return True
        return False

    def press_legend(self, action: str) -> None:
        if action == RAIN_ACTION:
            self._flip()

    # -- refresh (runs on the layer's worker thread, see Layer.tick) -------------------
    def is_due(self) -> bool:
        if not self.shown:
            return False
        wait = CATALOG_RETRY_SECONDS if self.error else CATALOG_REFRESH_SECONDS
        return time.time() - self.checked_at >= wait

    def refresh_if_due(self, force: bool = False) -> bool:
        if not self.enabled or not self.shown or not config.FETCH_NEW_DATA_FROM_API:
            return False
        if not force and not self.is_due():
            return False
        try:
            frames = fetch_catalog(RADAR_MODE)
            past = [frame for frame in frames if not frame.get("nowcast")] or frames
            if not past:
                raise RadarFetchError(_("no radar frame in the catalogue"))
            newest = max(past, key=lambda frame: frame["time"])
            with self.lock:
                unchanged = self.frame is not None and self.frame["time"] == newest["time"]
            raster = None if unchanged else fetch_frame_raster(newest, RADAR_MODE)
        except Exception as error:
            self.error = str(error)
            self.checked_at = time.time()
            raise  # Layer backs off and retries; the last good frame stays
        self.error = ""
        self.checked_at = time.time()
        if raster is None:
            return False
        with self.lock:
            self.frame, self.raster = newest, raster
        return True

    def reload(self) -> None:
        self.dirty = True

    # -- drawing and legend ---------------------------------------------------------------
    def build(self, view: View) -> LayerRender:
        with self.lock:
            frame, raster = self.frame, self.raster
        grid = current_brazil_grid()
        if not self.shown or frame is None or raster is None or grid is None:
            return LayerRender(None, "#000000")
        if time.time() - frame["time"] > STALE_SECONDS:
            return LayerRender(None, "#000000")
        return LayerRender(None, "#000000", bg_cells=rain_cells(raster, view, grid))

    def _problem(self) -> str:
        """The legend message when the key cannot be trusted, or '' when it can."""
        with self.lock:
            frame, error = self.frame, self.error
        if current_brazil_grid() is None:
            return _("waiting for the Brazil outline (IBGE)")
        if frame is None:
            return _("unavailable: %s") % error if error else _("no data yet")
        if error:
            return _("update failed: %s") % error
        if time.time() - frame["time"] > STALE_SECONDS:
            return _("no update since %s") % utc_text(frame["time"])
        return ""

    def legend_lines(self) -> list[SidebarLine]:
        lines = [
            SidebarLine(
                text=_("Global rain (RainViewer, dBZ)"),
                bold=True,
                choices=(_("on"), _("off")),
                choice_index=0 if self.shown else 1,
                action=RAIN_ACTION,
            )
        ]
        if self.shown:
            problem = self._problem()
            lines.append(SidebarLine(text=problem) if problem else SidebarLine(text="", legend_items=rain_key()))
        return lines


class GlobalTempLayer(WeatherLayer):
    """Core city temperatures, only for the cities of Brazil. No radar, no earthquakes."""

    name = "br_global_temp"
    toggle_key = "m"
    enabled = False  # the BRASIL tab switches it on

    def __init__(self) -> None:
        super().__init__()
        self.show_cities = True  # this layer's own switch (WeatherLayer's city switch)
        self.show_quakes = False
        self._ordered_startup = False  # no core startup stage drives this layer
        self._startup_weather_ready = True  # city weather is fetched for the visible cities

    def handle_key(self, key: int) -> bool:  # the core's C and E keys are not ours
        if 0 <= key < 256 and chr(key).lower() == self.toggle_key:
            self.show_cities = not self.show_cities
            self.dirty = True
            return True
        return False

    def press_legend(self, action: str) -> None:
        if action == TEMP_ACTION:
            self.show_cities = not self.show_cities
            self.dirty = True

    def prefetch(self) -> None:  # the core's prefetch would also fetch the radar
        return

    def slider(self):  # the radar time slider belongs to the rain layer
        return None

    def radar_markers(self, view: View, blocked: set[tuple]) -> list:
        return []

    def _load_places(self) -> None:
        try:
            places = load_places()
        except (MapDataError, OSError, ValueError) as error:
            print(f"brazil cities: {error}", file=sys.stderr)
            self._places_state = "failed"
            self._places_retry_at = time.monotonic() + PLACES_RETRY_SECONDS
            self.dirty = True
            return
        brazil = sorted(
            (place for place in places if place[5] == BRAZIL_COUNTRY),
            key=lambda place: place[4],
            reverse=True,
        )
        capitals = [place for place in brazil if place[3]]
        cities = [place for place in brazil if not place[3]]
        with self.lock:
            self._places_pending = (capitals, cities)

    def legend_lines(self) -> list[SidebarLine]:
        lines = [
            SidebarLine(
                text=_("City temperature, relative (Open-Meteo)"),
                bold=True,
                choices=(_("on"), _("off")),
                choice_index=0 if self.show_cities else 1,
                action=TEMP_ACTION,
            )
        ]
        if self.show_cities:
            if self._places_state == "loading":
                lines.append(SidebarLine(text=PLACES_LOADING_TEXT))
            elif self._places_state == "failed":
                lines.append(SidebarLine(text=PLACES_UNAVAILABLE_TEXT))
            else:
                lines.append(SidebarLine(text="", legend_items=legend_key(
                    (CITY_CONFIG["temperature_cold_color"], _("cold")),
                    (CITY_CONFIG["temperature_hot_color"], _("hot")),
                    (CITY_CONFIG["color"], _("no data")),
                )))
        return lines
