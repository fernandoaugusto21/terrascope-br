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
