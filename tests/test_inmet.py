"""INMET warnings layer: parsing, areas on the cell grid, cache and failures, the
legend and the info box. The HTTP call is replaced; no test uses the network."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from terrascope.core import config
from terrascope.core.view import View
from terrascope_br import store
from terrascope_br.geo import Ring
from terrascope_br.net import SourceError
from terrascope_br.sources import inmet

DATA = Path(__file__).parent / "data"
SAMPLE = (DATA / "inmet_avisos_sample.json").read_bytes()
AMAPA_VIEW = View(-51.5, 3.3, 5.0, 182, 50)  # covers the Amapa warning of the sample


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    return tmp_path


def _square(low, high):
    """A GeoJSON Polygon text, as the API sends it, with corners at low and high."""
    ring = [[low, low], [high, low], [high, high], [low, high], [low, low]]
    return json.dumps({"type": "Polygon", "coordinates": [ring]})


def _warning(ident, polygon, **extra):
    raw = {"id": ident, "descricao": "Tempestade", "severidade": "Perigo Potencial",
           "aviso_cor": "#FFFE00", "inicio": "2026-11-14 20:00", "fim": "2026-11-14 23:59",
           "estados": "Amapá", "geocodes": "1600204,1600501", "encerrado": False,
           "poligono": polygon}
    raw.update(extra)
    return raw


def _grid(width=10, height=10):
    """A view of lon 0..10, lat 0..10: one cell per degree."""
    return SimpleNamespace(width=width, height=height, origin_lon=0.0, origin_lat=10.0,
                           lon_span=10.0, lat_span=10.0)


def test_sample_keeps_each_warning_once():
    alerts = inmet.parse_alerts(SAMPLE)
    assert [alert["id"] for alert in alerts] == ["56405", "56406"]  # 56406 is in both lists
    assert all(alert["color"] == "#fffe00" for alert in alerts)
    assert alerts[0]["event"] == "Tempestade"
    assert alerts[0]["municipalities"] == 342


def test_marker_point_is_the_middle_of_the_area_box():
    alert = inmet.parse_alerts(SAMPLE)[1]  # Amapa, lon -52.66..-50.65, lat 2.12..4.54
    assert alert["lon"] == pytest.approx((-52.66 + -50.65) / 2, abs=0.01)
    assert alert["lat"] == pytest.approx((2.12 + 4.54) / 2, abs=0.01)


def test_warnings_without_a_usable_area_or_closed_are_left_out():
    payload = json.dumps({"hoje": [
        _warning("1", None),
        _warning("2", "not json"),
        _warning("3", json.dumps({"type": "Point", "coordinates": [-50, 2]})),
        _warning("4", _square(0, 1), encerrado=True),
        _warning("", _square(0, 1)),
        _warning("6", _square(0, 1)),
    ], "futuro": []}).encode("utf-8")
    assert [alert["id"] for alert in inmet.parse_alerts(payload)] == ["6"]


def test_a_colour_that_is_not_hex_gets_the_default():
    assert inmet._colour("#FF0000") == "#ff0000"
    assert inmet._colour("red") == inmet.DEFAULT_COLOR
    assert inmet._colour(None) == inmet.DEFAULT_COLOR


@pytest.mark.parametrize("payload", [b"<html>busy</html>", b"[]", b'{"hoje": []}', b"\xff\xfe"])
def test_unexpected_payload_is_rejected(payload):
    with pytest.raises(ValueError):
        inmet.parse_alerts(payload)


def test_an_empty_answer_is_a_valid_answer():
    assert inmet.parse_alerts(b'{"hoje": [], "futuro": []}') == []


def test_cells_inside_the_area_take_its_colour():
    alert = {"id": "a", "color": "#ff0000", "rings": [Ring([[2, 2], [5, 2], [5, 5], [2, 5], [2, 2]])]}
    cells = inmet.warning_cells([alert], _grid())
    # Cell centres 2.5, 3.5, 4.5 lie inside the square (lon 2..5 and lat 2..5), so 3 x 3 cells.
    assert set(cells) == {(x, y) for x in (2, 3, 4) for y in (5, 6, 7)}
    assert set(cells.values()) == {"#ff0000"}


def test_a_hole_in_the_area_stays_empty():
    outer = Ring([[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]])
    hole = Ring([[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]])
    alert = {"id": "h", "color": "#ffa500", "rings": [outer, hole]}
    cells = inmet.warning_cells([alert], _grid())
    assert (5, 4) not in cells and (4, 5) not in cells  # centres inside the hole
    assert (0, 0) in cells and (9, 9) in cells


def test_a_later_warning_is_drawn_over_an_earlier_one():
    first = {"id": "1", "color": "#ffff00", "rings": [Ring([[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]])]}
    second = {"id": "2", "color": "#ff0000", "rings": [Ring([[2, 2], [4, 2], [4, 4], [2, 4], [2, 2]])]}
    cells = inmet.warning_cells([first, second], _grid())
    assert cells[(0, 9)] == "#ffff00"
    assert cells[(2, 7)] == "#ff0000"


def test_build_marks_each_warning_with_its_colour_and_selects_one():
    layer = inmet.InmetAlertsLayer()
    layer.items = inmet.parse_alerts(SAMPLE)
    layer.data_time = time.time() - 60
    render = layer.build(AMAPA_VIEW)
    assert render.bg_cells, "the Amapa area should show on this view"
    assert len(render.markers) == 1 and render.markers[0].glyph == "!"
    assert render.markers[0].bg_color == "#fffe00"
    assert not render.markers[0].selected

    layer.select(dict(layer.items[1]))  # selection is by id, so a copy works
    render = layer.build(AMAPA_VIEW)
    assert render.markers[0].selected
    rows = dict(layer.info_rows())
    assert rows["EVENT"] == "Chuvas Intensas"
    assert rows["VALID"] == "14/11 18:00 - 15/11 23:59"
    assert rows["MUNICIPALITIES"] == "3"  # the Amapa warning lists three municipality codes


def test_old_warnings_are_hidden_and_announced():
    layer = inmet.InmetAlertsLayer()
    layer.items = inmet.parse_alerts(SAMPLE)
    layer.data_time = time.time() - 2 * 3600
    render = layer.build(AMAPA_VIEW)
    assert not render.bg_cells and not render.markers
    assert layer.legend_lines()[1].text.startswith("no update since")


def test_legend_has_one_entry_per_colour_and_says_so_when_there_are_none():
    layer = inmet.InmetAlertsLayer()
    layer.items = inmet.parse_alerts(SAMPLE)
    layer.data_time = time.time()
    lines = layer.legend_lines()
    assert len(lines) == 2 and lines[0].text == layer.source_name and lines[0].bold
    assert lines[1].legend_items == (("■", "#fffe00", "Perigo Potencial"),)

    layer.items = []
    lines = layer.legend_lines()
    assert [line.text for line in lines] == [layer.source_name, "no active warnings"]
    assert all(line.text != "INMET" for line in lines)  # the credit is in the tab attribution


def test_fetch_writes_the_cache_with_pacing(cache, monkeypatch):
    calls = []

    def answer(url, timeout, **kwargs):
        calls.append((url, kwargs))
        return 200, SAMPLE, {}

    monkeypatch.setattr(inmet, "get", answer)
    layer = inmet.InmetAlertsLayer()
    layer.enabled = True
    assert layer.refresh_if_due(force=True) is True
    assert calls == [(inmet.ALERTS_URL, {"min_gap": 1.0})]
    assert store.path("inmet", "avisos_ativos.json").read_bytes() == SAMPLE
    layer.reload()
    assert len(layer.items) == 2 and not layer.has_problem()


def test_an_html_answer_is_an_error_and_the_old_cache_stays(cache, monkeypatch):
    store.write_bytes(store.path("inmet", "avisos_ativos.json"), SAMPLE)
    monkeypatch.setattr(inmet, "get", lambda *args, **kwargs: (200, b"<html>busy</html>", {}))
    layer = inmet.InmetAlertsLayer()
    layer.enabled = True
    with pytest.raises(SourceError) as failure:
        layer.refresh_if_due(force=True)
    assert "unexpected payload" in str(failure.value)
    assert store.path("inmet", "avisos_ativos.json").read_bytes() == SAMPLE
    layer.reload()
    assert len(layer.items) == 2


def test_a_failed_fetch_keeps_the_last_good_warnings(cache, monkeypatch):
    store.write_bytes(store.path("inmet", "avisos_ativos.json"), SAMPLE)

    def down(*args, **kwargs):
        raise SourceError("HTTP 503")

    monkeypatch.setattr(inmet, "get", down)
    layer = inmet.InmetAlertsLayer()
    layer.enabled = True
    with pytest.raises(SourceError):
        layer.refresh_if_due(force=True)
    layer.reload()
    assert len(layer.items) == 2
    assert layer.status == "update failed: HTTP 503"


def test_no_cache_and_no_answer_says_no_data_yet(cache):
    layer = inmet.InmetAlertsLayer()
    layer.reload()
    assert layer.items == []
    assert layer.legend_lines()[1].text == "no data yet"


def test_an_empty_answer_from_the_service_is_no_warnings(cache, monkeypatch):
    monkeypatch.setattr(inmet, "get", lambda *args, **kwargs: (200, b'{"hoje": [], "futuro": []}', {}))
    layer = inmet.InmetAlertsLayer()
    layer.enabled = True
    layer.refresh_if_due(force=True)
    layer.reload()
    assert layer.items == []
    assert layer.legend_lines()[1].text == "no active warnings"


def test_offline_never_fetches(cache, monkeypatch):
    calls = []
    monkeypatch.setattr(inmet, "get", lambda *args, **kwargs: calls.append(args) or (200, SAMPLE, {}))
    monkeypatch.setattr(config, "FETCH_NEW_DATA_FROM_API", False)
    layer = inmet.InmetAlertsLayer()
    layer.enabled = True
    assert layer.refresh_if_due(force=True) is False
    assert calls == []


def test_layer_is_registered_on_the_brasil_tab():
    from terrascope_br.tabs import brasil_tabs

    tab = brasil_tabs()[0]
    assert "br_inmet_alerts" in tab["layers"]
    assert "INMET" in tab["attribution"]
    assert inmet.InmetAlertsLayer.toggle_key == "v"


def test_warning_texts_switch_to_portuguese(cache):
    from terrascope.core import i18n

    layer = inmet.InmetAlertsLayer()
    layer.items = []
    layer.data_time = time.time()
    try:
        i18n.activate("pt_BR")
        assert layer.legend_lines()[1].text == "sem avisos ativos"
    finally:
        i18n.activate("en")
    assert layer.legend_lines()[1].text == "no active warnings"
