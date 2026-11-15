"""The global rain and city temperature layers of the BRASIL tab: the crop to
Brazil, the colour scale and legend, the draw order and the switches. The
network is never used: the fetch functions are replaced where a refresh runs."""

import time

import numpy as np
import pytest

from terrascope.core import config, plugins
from terrascope.core.registry import build_layers
from terrascope.core.view import View
from terrascope.layers.weather import layer as weather_layer
from terrascope.layers.weather.config import CITY_CONFIG, INTENSITY_MAX, INTENSITY_MIN
from terrascope.layers.weather.radar import RADAR_LEVEL_COLORS, RadarFetchError, _LEVEL_COUNT
from terrascope_br.geo import Ring
from terrascope_br.sources import chuva_online, global_weather as gw
from terrascope_br.tabs import brasil_tabs


@pytest.fixture
def offline(monkeypatch):
    """No live requests: the refresh and the city weather stay off."""
    monkeypatch.setattr(config, "FETCH_NEW_DATA_FROM_API", False)
    monkeypatch.setattr(weather_layer, "FETCH_NEW_DATA_FROM_API", False)


# A square standing in for the IBGE state map: centre of Brazil, Paraguay outside it.
SQUARE = [[-55.0, -20.0], [-45.0, -20.0], [-45.0, -10.0], [-55.0, -10.0], [-55.0, -20.0]]


@pytest.fixture
def brazil_outline(monkeypatch):
    rings = [Ring(SQUARE)]  # the same list object every call, as the cache gives it
    monkeypatch.setattr(gw, "state_rings", lambda: rings)
    yield rings


def _cell_centre(view, x, y):
    return (view.origin_lon + (x + 0.5) * view.lon_span / view.width,
            view.origin_lat - (y + 0.5) * view.lat_span / view.height)


def _brazil_view(zoom=3.0, width=120, height=40):
    return View(-52.0, -14.0, zoom, width, height)


def _world_view(width=160, height=60):
    return View(0.0, 0.0, 1.0, width, height)


def _raster(value, size=512):
    return np.full((size, size), value, dtype=np.float32)


def _frame(age_seconds=0):
    return {"time": int(time.time()) - age_seconds, "host": "h", "path": "p", "nowcast": False}


# -- the tab and the draw order ------------------------------------------------------

def test_brasil_tab_lists_the_global_layers_and_hides_the_basemap_city_names():
    tab = brasil_tabs()[0]
    assert {"br_global_rain", "br_global_temp"} <= set(tab["layers"])
    assert tab["place_names"] is False
    plugins.normalize_tab(tab, taken=set())  # the core accepts the tab with its new keys


def test_rain_and_temperature_are_drawn_under_the_iag_radar():
    names = [layer.name for layer in build_layers()]
    rain, temp, radar = (names.index(n) for n in ("br_global_rain", "br_global_temp", "br_radar"))
    assert rain < radar and temp < radar
    assert names.index("weather") < rain  # the core's own layers stay first


def test_global_layers_do_not_take_a_key_of_the_core_or_of_the_other_layers():
    keys = [layer.toggle_key.lower() for layer in build_layers() if layer.toggle_key]
    assert len(keys) == len(set(keys))
    assert gw.GlobalRainLayer.toggle_key == "j" and gw.GlobalTempLayer.toggle_key == "m"


# -- rain: crop, colours, legend ------------------------------------------------------

def test_grid_is_true_inside_the_outline_only():
    grid = gw.brazil_grid([Ring(SQUARE)])

    def at(lon, lat):
        row = int((gw.BRAZIL_BOX[3] - lat) / gw.GRID_STEP)
        col = int((lon - gw.BRAZIL_BOX[0]) / gw.GRID_STEP)
        return bool(grid[row, col])

    assert at(-50.0, -15.0)  # inside the square
    assert not at(-57.6, -25.3)  # Asuncion, Paraguay: outside it
    assert not at(-40.0, -30.0)  # the sea-side corner of the box


def test_no_outline_means_no_rain_and_a_legend_message(monkeypatch):
    monkeypatch.setattr(gw, "state_rings", lambda: [])
    layer = gw.GlobalRainLayer()
    layer.frame, layer.raster = _frame(), _raster(120)
    assert gw.current_brazil_grid() is None
    assert not layer.build(_brazil_view()).bg_cells
    assert layer.legend_lines()[1].text == "waiting for the Brazil outline (IBGE)"


def test_rain_is_cropped_to_the_outline_even_when_the_whole_world_has_rain(brazil_outline):
    layer = gw.GlobalRainLayer()
    layer.frame, layer.raster = _frame(), _raster(120)
    view = _world_view()
    grid = gw.current_brazil_grid()
    cells = gw.rain_cells(layer.raster, view, grid)
    assert cells, "the part of the frame over the outline is drawn"
    assert len(cells) == int(gw.brazil_cells(view, grid).sum()), "every cell over the outline with rain, and only those"
    for x, y in cells:
        lon, lat = _cell_centre(view, x, y)
        assert -55.1 <= lon <= -44.9 and -20.1 <= lat <= -9.9, "nothing outside the outline (Paraguay is left out)"


def test_rain_colours_are_the_core_levels_of_the_intensity_scale(brazil_outline):
    layer = gw.GlobalRainLayer()
    layer.frame, layer.raster = _frame(), _raster(120)
    render = layer.build(_brazil_view())
    level = int((120 - INTENSITY_MIN) / (INTENSITY_MAX - INTENSITY_MIN) * _LEVEL_COUNT)
    assert set(render.bg_cells.values()) == {RADAR_LEVEL_COLORS[level]}


def test_no_rain_means_no_cells(brazil_outline):
    layer = gw.GlobalRainLayer()
    layer.frame, layer.raster = _frame(), _raster(0)
    assert not layer.build(_brazil_view()).bg_cells


def test_stale_frame_is_not_drawn_as_current_and_the_legend_says_so(brazil_outline):
    layer = gw.GlobalRainLayer()
    layer.frame, layer.raster = _frame(age_seconds=3600), _raster(120)
    assert not layer.build(_brazil_view()).bg_cells
    assert layer.legend_lines()[1].text.startswith("no update since")


def test_missing_frame_says_it_has_no_data(brazil_outline):
    layer = gw.GlobalRainLayer()
    assert layer.legend_lines()[1].text == "no data yet"


def test_rain_key_is_in_dbz_and_the_legend_says_so(brazil_outline):
    layer = gw.GlobalRainLayer()
    layer.frame, layer.raster = _frame(), _raster(120)
    key_line = layer.legend_lines()[1]
    assert len(key_line.legend_items) == gw.KEY_STEPS
    assert all(label.endswith(" dBZ") for _glyph, _colour, label in key_line.legend_items)
    assert layer.legend_lines()[0].text.endswith("(RainViewer, dBZ)")
    # the BR radar says weak / moderate / strong, not dBZ: the two legends differ
    assert "dBZ" not in " ".join(line.text for line in chuva_online.RadarLayer().legend_lines())


def test_rain_switch_by_key_and_by_legend_line(offline):
    layer = gw.GlobalRainLayer()
    assert layer.handle_key(ord("x")) is False
    assert layer.handle_key(ord("j")) is True
    assert layer.shown is False
    assert layer.legend_lines()[0].choice_index == 1
    assert len(layer.legend_lines()) == 1  # no key while off
    layer.press_legend(gw.RAIN_ACTION)
    assert layer.shown is True


def test_rain_refresh_fetches_the_newest_observed_frame_once(monkeypatch):
    now = int(time.time())
    catalogue = [
        {"time": now - 600, "path": "a", "nowcast": False},
        {"time": now, "path": "b", "nowcast": True},  # forecast frames are not observed rain
    ]
    fetched = []
    monkeypatch.setattr(gw, "fetch_catalog", lambda mode: catalogue)
    monkeypatch.setattr(gw, "fetch_frame_raster", lambda frame, mode: fetched.append(frame["time"]) or _raster(120))
    layer = gw.GlobalRainLayer()
    layer.enabled = True
    assert layer.refresh_if_due(force=True) is True
    assert fetched == [now - 600]
    assert layer.refresh_if_due(force=True) is False, "the same frame is not downloaded twice"
    assert fetched == [now - 600]


def test_rain_failed_refresh_keeps_the_last_frame_and_backs_off(brazil_outline, monkeypatch):
    def down(mode):
        raise RadarFetchError("HTTP 503")

    monkeypatch.setattr(gw, "fetch_catalog", down)
    layer = gw.GlobalRainLayer()
    layer.enabled = True
    layer.frame, layer.raster = _frame(), _raster(120)
    with pytest.raises(RadarFetchError):
        layer.refresh_if_due(force=True)
    assert layer.raster is not None, "the last good frame stays"
    assert layer.legend_lines()[1].text == "update failed: HTTP 503"
    assert layer.is_due() is False, "a failed check waits before the next try"


def test_rain_does_not_fetch_while_switched_off(offline, monkeypatch):
    calls = []
    monkeypatch.setattr(gw, "fetch_catalog", lambda mode: calls.append(mode) or [])
    layer = gw.GlobalRainLayer()
    layer.enabled = True
    layer.shown = False
    assert layer.refresh_if_due(force=True) is False
    assert calls == []


# -- temperature: Brazil only, no radar, no earthquakes -------------------------------

PLACES = [
    (-47.9, -15.8, "Brasilia", True, 4_800_000, "Brazil"),
    (-46.6, -23.5, "Sao Paulo", False, 22_000_000, "Brazil"),
    (-57.6, -25.3, "Asuncion", True, 3_000_000, "Paraguay"),
    (-74.1, 4.6, "Bogota", True, 10_000_000, "Colombia"),
]


def _temp_layer_with(monkeypatch, places=PLACES):
    monkeypatch.setattr(gw, "load_places", lambda: list(places))
    layer = gw.GlobalTempLayer()
    layer._load_places()
    layer._capitals, layer._cities = layer._places_pending
    layer._places_state = "ready"
    return layer


def test_temperature_takes_only_the_places_of_brazil(offline, monkeypatch):
    layer = _temp_layer_with(monkeypatch)
    names = {place[2] for place in layer._capitals + layer._cities}
    assert names == {"Brasilia", "Sao Paulo"}


def test_temperature_labels_in_view_are_brazilian_only(offline, monkeypatch):
    layer = _temp_layer_with(monkeypatch)
    placed = layer._place_cities(_brazil_view(zoom=3.0))
    names = {city["name"] for city, _cell, _label, _icon in placed}
    assert names and names <= {"Brasilia", "Sao Paulo"}
    assert "Asuncion" not in names, "Asuncion is in view and a capital, but not Brazilian"


def test_temperature_has_no_radar_and_no_earthquakes(offline, monkeypatch):
    layer = _temp_layer_with(monkeypatch)
    view = _brazil_view()
    assert layer.radar_markers(view, set()) == []
    assert layer.slider() is None
    assert layer.show_quakes is False


def test_temperature_switch_by_key_ignores_the_core_keys(offline):
    layer = gw.GlobalTempLayer()
    assert layer.handle_key(ord("c")) is False, "C is the core's city switch, not this layer's"
    assert layer.handle_key(ord("m")) is True
    assert layer.show_cities is False
    layer.press_legend(gw.TEMP_ACTION)
    assert layer.show_cities is True


def test_temperature_legend_is_a_toggle_and_a_relative_key(offline):
    layer = gw.GlobalTempLayer()
    toggle, key = layer.legend_lines()[0], layer.legend_lines()[1]
    assert toggle.action == gw.TEMP_ACTION and toggle.choice_index == 0
    assert toggle.text == "City temperature, relative (Open-Meteo)"
    colours = [colour for _glyph, colour, _label in key.legend_items]
    assert colours == [CITY_CONFIG["temperature_cold_color"], CITY_CONFIG["temperature_hot_color"], CITY_CONFIG["color"]]
