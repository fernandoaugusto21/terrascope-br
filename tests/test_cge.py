"""CGE flood-risk states by region (br_inundation_cge): the image map, the
colours of the states, the GeoSampa outlines and the layer. The real page and
image of 21/11/2026 (every region in watch) and a coarse copy of the GeoSampa
answer are in tests/data; the other states are painted on a copy of the image."""

import io
import json
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from terrascope.core import config
from terrascope.core.view import View
from terrascope_br import store
from terrascope_br.sources import cge

DATA = Path(__file__).parent / "data"
PAGE = (DATA / "cge_alertas.html").read_text(encoding="utf-8")
IMAGE = (DATA / "cge_alertas.png").read_bytes()
REGIONS = json.loads((DATA / "geosampa_subprefeituras.json").read_text(encoding="utf-8"))
FLOODS = (DATA / "cge_alagamentos_18112026.html").read_text(encoding="utf-8")


def _painted(*circles) -> bytes:
    """The real image with (x, y, radius, colour) discs painted on it."""
    image = Image.open(io.BytesIO(IMAGE)).convert("RGB")
    draw = ImageDraw.Draw(image)
    for x, y, radius, colour in circles:
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=colour)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def test_the_image_map_names_the_32_regions_and_each_has_an_outline():
    areas = cge.parse_areas(PAGE)
    codes = {code for code, *_rest in areas}
    assert len(areas) == 32 and len(codes) == 32
    assert codes == set(cge.compact_regions(REGIONS))


def test_on_a_quiet_day_every_region_is_in_watch():
    states = cge.read_states(PAGE, IMAGE)
    assert len(states) == 32 and {region["state"] for region in states.values()} == {"observacao"}
    assert states["SE"]["name"] == "Sé"


@pytest.mark.parametrize("colour, state", [
    ((255, 255, 0), "atencao"), ((255, 151, 0), "iminencia"), ((255, 0, 0), "alerta"),
    ((240, 240, 112), "atencao"),  # yellow at half strength over the grey of the map
])
def test_a_painted_region_takes_the_state_of_its_colour(colour, state):
    # Sé is the circle at (157, 153); Lapa at (114, 140) stays grey.
    states = cge.read_states(PAGE, _painted((157, 153, 10, colour)))
    assert states["SE"]["state"] == state
    assert states["LA"]["state"] == "observacao"


def test_a_few_coloured_pixels_do_not_change_the_state():
    states = cge.read_states(PAGE, _painted((157, 153, 2, (255, 0, 0))))
    assert states["SE"]["state"] == "observacao"


def test_grey_white_black_and_the_river_blue_are_not_states():
    for colour in ((225, 225, 225), (255, 255, 255), (0, 0, 0), (69, 69, 69), (51, 153, 204)):
        assert cge.pixel_state(*colour) == "observacao"


def test_pages_without_the_map_are_errors():
    with pytest.raises(ValueError):
        cge.parse_areas("<html>manutenção</html>")
    with pytest.raises(ValueError):
        cge.read_states(PAGE, b"not a png")
    with pytest.raises(ValueError):
        cge.compact_regions({"features": []})


def test_thinning_keeps_the_ends_and_drops_close_points():
    ring = [[0.0, 0.0], [0.0001, 0.0], [0.0002, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.0]]
    thinned = cge.thin(ring, 0.001)
    assert thinned[0] == ring[0] and thinned[-1] == ring[-1] and [0.0001, 0.0] not in thinned


# -- the layer ---------------------------------------------------------------------

def _write(states: dict, age: float = 0.0) -> None:
    store.write_bytes(store.path("cge", "subprefeituras.json"), json.dumps(cge.compact_regions(REGIONS)).encode())
    store.write_bytes(store.path("cge", "alertas.json"), json.dumps(
        {"time": time.time() - age, "regions": {code: {"name": code, "state": state}
                                                 for code, state in states.items()}}).encode())


@pytest.fixture
def layer():
    made = cge.CgeAlertsLayer()
    made.enabled = True
    yield made
    for name in ("subprefeituras.json", "alertas.json"):
        store.path("cge", name).unlink(missing_ok=True)


CITY = View(-46.6, -23.6, 64, 120, 40)


def test_regions_in_a_state_are_coloured_and_marked_the_rest_only_outlined(layer):
    _write({"SE": "alerta", "LA": "atencao"})
    layer.reload()
    assert len(layer.regions) == 32
    assert [item["code"] for item in layer.items] == ["LA", "SE"]   # most severe drawn last
    render = layer.build(CITY)
    assert any(line.strip("⠀ ") for line in render.lines)    # every outline
    assert {marker.item["code"] for marker in render.markers} == {"LA", "SE"}
    colours = set(render.bg_cells.values())
    assert cge.STATES["alerta"][0] in colours and cge.STATES["atencao"][0] in colours
    layer.select(next(marker.item for marker in render.markers if marker.item["code"] == "SE"))
    rows = dict(layer.info_rows())
    assert rows["STATE"] == "alert" and rows["SOURCE"] == "CGE"


def test_a_quiet_day_has_outlines_and_no_colour(layer):
    _write({})
    layer.reload()
    render = layer.build(CITY)
    assert render.lines and not render.markers and not render.bg_cells
    assert len(layer.legend_lines()) == 2 and layer.legend_lines()[1].legend_items


def test_old_states_are_not_shown_as_current(layer):
    _write({"SE": "alerta"}, age=2 * 3600)
    layer.reload()
    render = layer.build(CITY)
    assert render.lines and not render.markers and not render.bg_cells
    assert "no update since" in layer.legend_lines()[1].text


def test_nothing_is_drawn_far_out_or_away_from_the_city(layer):
    _write({"SE": "alerta"})
    layer.reload()
    assert layer.build(View(-46.6, -23.6, 8, 120, 40)).lines is None
    assert layer.build(View(-43.2, -22.9, 64, 120, 40)).lines is None   # Rio de Janeiro


def test_fetch_reads_the_page_the_image_and_the_outlines_once(layer, monkeypatch):
    monkeypatch.setattr(config, "FETCH_NEW_DATA_FROM_API", True)
    asked = []

    def fake_get(url, timeout, min_gap=1.0, if_modified_since=None):
        asked.append(url)
        if url == cge.REGIONS_URL:
            return 200, json.dumps(REGIONS).encode(), {}
        if url == cge.PAGE_URL:
            return 200, PAGE.encode(), {}
        if url == cge.FLOODS_URL:
            return 200, FLOODS.encode(), {}
        return 200, _painted((157, 153, 10, (255, 151, 0))), {"Last-Modified": "Sat, 21 Nov 2026 20:31:11 GMT"}

    monkeypatch.setattr(cge, "get", fake_get)
    layer.fetch()
    layer.fetch()
    assert asked.count(cge.REGIONS_URL) == 1   # the outlines are kept 30 days
    layer.reload()
    assert [item["code"] for item in layer.items] == ["SE"] and layer.items[0]["state"] == "iminencia"
    assert layer.data_time == pytest.approx(1795293071.0)   # the image's Last-Modified


def test_the_layer_is_switched_on_its_legend_line(layer):
    from terrascope.core import plugins

    assert layer.toggle_key == ""   # every letter is taken
    assert plugins.toggle_key_problem(layer.toggle_key, {}, plugins.reserved_toggle_keys()) is None
    _write({"SE": "alerta"})
    layer.reload()
    title = layer.legend_lines()[0]
    assert title.action == layer.switch_action and title.choice_index == 0
    layer.press_legend(layer.switch_action)
    assert not layer.shown and layer.render(CITY).lines is None


@pytest.mark.parametrize("colour, state", [
    ((240, 112, 112), "alerta"),     # red at half strength over the grey
    ((153, 0, 0), "alerta"),         # dark red
    ((240, 188, 112), "iminencia"),  # orange at half strength
])
def test_faded_and_dark_state_colours_keep_their_state(colour, state):
    assert cge.pixel_state(*colour) == state


def test_an_image_without_the_state_legend_is_an_error_not_all_watch():
    grey = Image.new("RGB", (400, 537), (225, 225, 225))
    out = io.BytesIO()
    grey.save(out, format="PNG")
    with pytest.raises(ValueError, match="legend"):
        cge.read_states(PAGE, out.getvalue())


def test_an_image_map_bigger_than_the_image_is_an_error():
    page = PAGE.replace('coords="114,435,10"', 'coords="114,935,10"')   # Parelheiros, below the image
    with pytest.raises(ValueError, match="size"):
        cge.read_states(page, IMAGE)


def test_area_attributes_in_any_order_and_quoting():
    page = """<map><area title='Sé' coords="157,153,10" shape="circle"/><area shape=circle title="X"></map>"""
    assert cge.parse_areas(page) == [("SE", "Sé", 157, 153, 10)]


def test_odd_geosampa_answers_are_errors_not_crashes():
    for payload in ([], "erro", {"features": ["x", {"properties": "y"}, {"geometry": "z"}]}):
        with pytest.raises(ValueError):
            cge.compact_regions(payload)


def test_a_failed_outline_refresh_keeps_the_states_coming(layer, monkeypatch):
    from terrascope_br.net import SourceError

    _write({})
    old = time.time() - cge.REGIONS_SECONDS - 10
    import os
    os.utime(store.path("cge", "subprefeituras.json"), (old, old))
    asked = []

    def fake_get(url, timeout, min_gap=1.0, if_modified_since=None):
        asked.append(url)
        if url == cge.REGIONS_URL:
            raise SourceError("HTTP 500")
        if url == cge.PAGE_URL:
            return 200, PAGE.encode(), {}
        if url == cge.FLOODS_URL:
            return 200, FLOODS.encode(), {}
        return 200, _painted((157, 153, 10, (255, 0, 0))), {"Last-Modified": "Sat, 21 Nov 2026 20:31:11 GMT"}

    monkeypatch.setattr(cge, "get", fake_get)
    layer.fetch()
    layer.fetch()
    assert asked.count(cge.REGIONS_URL) == 1   # not again at every refresh
    layer.reload()
    assert [item["code"] for item in layer.items] == ["SE"]


def test_an_unchanged_image_is_not_downloaded_again(layer, monkeypatch):
    sent = []

    def fake_get(url, timeout, min_gap=1.0, if_modified_since=None):
        if url == cge.REGIONS_URL:
            return 200, json.dumps(REGIONS).encode(), {}
        if url == cge.PAGE_URL:
            return 200, PAGE.encode(), {}
        if url == cge.FLOODS_URL:
            return 200, FLOODS.encode(), {}
        sent.append(if_modified_since)
        if if_modified_since:
            return 304, b"", {}
        return 200, _painted((157, 153, 10, (255, 255, 0))), {"Last-Modified": "Sat, 21 Nov 2026 20:31:11 GMT"}

    monkeypatch.setattr(cge, "get", fake_get)
    layer.fetch()
    layer.fetch()
    assert sent == [None, "Sat, 21 Nov 2026 20:31:11 GMT"]
    layer.reload()
    assert [item["state"] for item in layer.items] == ["atencao"]


def test_geosampa_and_its_licence_are_named_while_the_outlines_show(layer):
    _write({})
    layer.reload()
    assert layer.credits() == []                       # nothing drawn yet
    layer.build(CITY)
    (line,) = layer.credits()
    assert line.startswith("GeoSampa (CC BY-SA 4.0)")   # the licence before what a narrow map cuts
    layer.build(View(-43.2, -22.9, 64, 120, 40))      # Rio: not drawn
    assert layer.credits() == []
    layer.build(CITY)
    layer.press_legend(layer.switch_action)            # switched off
    assert layer.credits() == []
    layer.press_legend(layer.switch_action)            # on again: named after the next drawing
    assert layer.credits() == []
    layer.build(CITY)
    assert len(layer.credits()) == 1
    layer.clear()
    assert layer.credits() == []


def test_an_older_core_gets_geosampa_in_the_tab_line(monkeypatch):
    from terrascope_br import tabs

    assert "GeoSampa" not in tabs.brasil_tabs()[0]["attribution"]
    monkeypatch.setattr(tabs, "_core_names_layer_credits", lambda: False)
    assert tabs.brasil_tabs()[0]["attribution"].endswith("GeoSampa (CC BY-SA 4.0)")


# ---- region names, flood points, station and forecast ------------------------------

STATIONS_PAGE = (DATA / "cge_estacoes.html").read_text(encoding="utf-8")
STATION_503 = (DATA / "cge_estacao_503.html").read_text(encoding="utf-8")
FORECAST_PAGE = (DATA / "cge_previsao_estendida.html").read_text(encoding="utf-8")
NO_FLOODS = (DATA / "cge_alagamentos_vazio.html").read_text(encoding="utf-8")


def test_the_flood_list_gives_each_region_its_zone_and_points():
    floods = cge.parse_floods(FLOODS)
    assert set(floods) == {"MG", "PJ", "LA", "PI", "MO"}
    assert floods["MG"]["zone"] == "Zona Norte" and floods["LA"]["zone"] == "Zona Oeste"
    first = floods["MG"]["points"][0]
    assert first == {"active": True, "passable": True, "time": "17:18–19:40", "street": "AV MORVAN DIAS DE FIGUEIREDO",
                     "direction": "A SENNA/CASTELO B", "reference": "AV GUILHERME"}
    assert cge.flood_summary(floods["MG"]["points"]) == (1, 2, cge.PASSABLE_COLOR)
    assert cge.flood_summary(floods["PJ"]["points"]) == (1, 1, cge.IMPASSABLE_COLOR)
    assert cge.flood_summary(floods["PI"]["points"]) == (0, 4, None)
    assert cge.parse_floods(NO_FLOODS) == {}
    with pytest.raises(ValueError):
        cge.parse_floods(PAGE)


@pytest.mark.parametrize("name, code", [
    ("Vila Maria/ Vila Guilherme", "MG"), ("Pirituba/Jaraguá", "PJ"), ("Jaçanã/ Tremembé", "JT"),
    ("Aricanduva/ Vila Formosa", "AF"), ("Capela do Socorro", "CS"), ("M Boi Mirim", "MB"), ("Sé", "SE"),
])
def test_region_names_of_the_cge_pages(name, code):
    assert cge.region_code(name) == code


def test_the_station_list_maps_27_regions_to_their_station():
    stations = cge.parse_station_list(STATIONS_PAGE)
    assert len(stations) == 27
    assert stations["SE"] == "503" and stations["PR"] == "504" and stations["CS"] == "846"
    assert "CV" not in stations   # Casa Verde has no station of its own


def _write_floods(floods_time: float | None = None) -> None:
    _write({"SE": "atencao"})
    document = json.loads(store.read_bytes(store.path("cge", "alertas.json")).decode())
    document.update({"floods": cge.parse_floods(FLOODS), "floods_time": floods_time or time.time()})
    store.write_bytes(store.path("cge", "alertas.json"), json.dumps(document).encode())


ZOOMED = View(-46.65, -23.55, 256, 160, 60)


def test_names_from_the_label_zoom_and_flood_marks_with_their_number(layer):
    _write_floods()
    layer.reload()
    labels = {marker.label: marker for marker in layer.build(ZOOMED).markers if marker.label}
    names = {region["region"] for region in layer.regions}
    assert names & set(labels)                                   # region names drawn
    city = [marker for marker in layer.build(CITY).markers if marker.label]   # zoom 64, the whole city
    assert not names & {marker.label for marker in city}         # no names below the label zoom
    # One active point each: Pirituba/Jaraguá impassable, Vila Maria/Vila Guilherme passable.
    assert sorted((marker.item["code"], marker.label, marker.color) for marker in city) == [
        ("MG", "≈1", cge.PASSABLE_COLOR), ("PJ", "≈1", cge.IMPASSABLE_COLOR)]


def test_names_do_not_cover_each_other(layer):
    _write_floods()
    layer.reload()
    cells = []
    for marker in layer.build(ZOOMED).markers:
        if marker.label:
            cells += [(marker.x + dx, marker.y) for dx in range(len(marker.label))]
    assert len(cells) == len(set(cells))


def test_old_flood_points_are_not_shown(layer):
    _write_floods(floods_time=time.time() - 2 * 3600)
    layer.reload()
    assert not [m for m in layer.build(CITY).markers if m.label and m.label.startswith("≈")]
    assert dict(layer._flood_rows(layer.regions[0]))[cge._("FLOODS")] == cge._("N/A")


def test_a_click_on_a_name_selects_the_region(layer):
    _write_floods()
    layer.reload()
    layer.last_markers = layer.build(ZOOMED).markers      # what the core keeps of the frame
    named = next(marker for marker in layer.last_markers if marker.label and not marker.label.startswith("≈"))
    hits = layer.hit_test_all(named.x + len(named.label) - 1, named.y)
    assert hits and hits[0][1]["region"] == named.label


def test_the_info_box_lists_the_floods_of_the_region(layer, monkeypatch):
    _write_floods()
    layer.reload()
    monkeypatch.setattr(layer, "_region_details", lambda code: {"station": "S", "forecast": "F"})
    region = next(region for region in layer.regions if region["code"] == "MG")
    layer.selected = region
    rows = layer.info_rows()
    values = [value for _name, value in rows]
    assert ("ZONE" in [name for name, _value in rows] or cge._("ZONE") in [name for name, _value in rows])
    assert any("AV MORVAN DIAS DE FIGUEIREDO" in value for value in values)
    assert "S" in values and "F" in values


def test_region_details_read_the_station_and_the_city_forecast(monkeypatch):
    from datetime import datetime

    from terrascope_br.sources import weather_providers as wp

    pages = {cge.STATIONS_URL: STATIONS_PAGE, cge.STATION_URL.format(post="503"): STATION_503,
             wp.CGE_FORECAST_URL: FORECAST_PAGE}
    monkeypatch.setattr(wp, "_page", lambda url, keep: pages[url])
    forecast_day = wp.parse_cge_forecast(FORECAST_PAGE)[0]["date"]
    now = datetime.fromisoformat(forecast_day + "T10:00").replace(tzinfo=wp.SAO_PAULO_TIME)
    station, forecast = cge.region_details("SE", now)
    assert "°C" in station
    assert forecast.endswith(cge._("(whole city: the CGE gives no forecast by region)"))
    assert cge.region_details("CV", now)[0] == cge._("no CGE station in this region")


def test_a_failed_flood_list_keeps_the_states_updating(layer, monkeypatch):
    def fake_get(url, timeout, min_gap=1.0, if_modified_since=None):
        if url == cge.REGIONS_URL:
            return 200, json.dumps(REGIONS).encode(), {}
        if url == cge.PAGE_URL:
            return 200, PAGE.encode(), {}
        if url == cge.FLOODS_URL:
            raise cge.SourceError("HTTP 503")
        return 200, _painted((157, 153, 10, (255, 0, 0))), {}

    monkeypatch.setattr(cge, "get", fake_get)
    layer.fetch()
    layer.reload()
    assert [item["state"] for item in layer.items] == ["alerta"] and layer.floods_time is None


def test_every_region_has_a_zone_even_on_a_day_without_floods(layer):
    _write({})
    layer.reload()
    assert len(cge.REGION_ZONES) == 32 and set(cge.REGION_ZONES) == set(cge.REGION_CODES.values())
    assert all(region["zone"] for region in layer.regions)
