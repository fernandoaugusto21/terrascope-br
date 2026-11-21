"""Levels of detail of the IBGE outlines: national files up to UF_DETAIL_ZOOM, then
one file per state in view ("intermediaria", then "maxima"), and never a national
file for a zoomed-in view."""

import json

import pytest

from terrascope.core import config
from terrascope.core.view import View
from terrascope_br import store, tabs
from terrascope_br.sources import ibge

SP = "35"
RJ = "33"


def _square(west, south, east, north):
    return [[[west, south], [east, south], [east, north], [west, north], [west, south]]]


def _collection(features):
    return json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"codarea": code},
         "geometry": {"type": "Polygon", "coordinates": _square(*box)}}
        for code, box in features
    ]}).encode("utf-8")


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "FETCH_NEW_DATA_FROM_API", True)
    ibge._parsed.clear()
    # Two "states": SP around the city of São Paulo, RJ well to the east.
    store.write_bytes(store.path("ibge", "ufs.geojson"),
                      _collection([(SP, (-53.0, -25.5, -44.0, -19.5)), (RJ, (-44.3, -23.5, -40.5, -20.5))]))
    return tmp_path


@pytest.fixture
def downloads(monkeypatch):
    urls = []

    def fake_get(url, timeout, **kwargs):
        urls.append(url)
        return 200, _collection([("x", (-46.8, -23.7, -46.4, -23.4))]), {}

    monkeypatch.setattr(ibge, "get", fake_get)
    return urls


def _view(zoom, lon=-46.6, lat=-23.55):
    return View(lon, lat, zoom, 108, 58)


def test_quality_by_zoom():
    assert ibge.detail_quality(8) is None
    assert ibge.detail_quality(ibge.UF_DETAIL_ZOOM) == "intermediaria"
    assert ibge.detail_quality(ibge.UF_MAX_DETAIL_ZOOM - 1) == "intermediaria"
    assert ibge.detail_quality(ibge.UF_MAX_DETAIL_ZOOM) == "maxima"


def test_states_in_view_come_from_the_cached_state_boxes(cache):
    assert ibge.states_in_view(_view(200)) == (SP,)
    assert set(ibge.states_in_view(_view(40, lon=-44.3, lat=-22.5))) == {SP, RJ}
    assert ibge.states_in_view(_view(200, lon=-60.0, lat=-5.0)) == ()


def test_municipalities_use_the_national_file_only_below_the_state_detail_zoom(cache):
    layer = ibge.MunicipalityOutlinesLayer()
    assert layer.pieces_for(_view(4)) == ()
    national = layer.pieces_for(_view(10))
    assert [file.name for file, _url in national] == ["municipios.geojson"]
    for zoom, quality in ((40, "intermediaria"), (200, "maxima")):
        pieces = layer.pieces_for(_view(zoom))
        assert [file.name for file, _url in pieces] == [f"mun-{SP}-{quality}.geojson"]
        assert all("/estados/35?" in url and "intrarregiao=municipio" in url and quality in url
                   for _file, url in pieces)


def test_states_switch_to_one_outline_file_per_state_when_zoomed_in(cache):
    layer = ibge.StateOutlinesLayer()
    assert [f.name for f, _u in layer.pieces_for(_view(3))] == ["ufs.geojson"]
    pieces = layer.pieces_for(_view(200))
    assert [f.name for f, _u in pieces] == [f"uf-{SP}-maxima.geojson"]
    assert "intrarregiao" not in pieces[0][1]


def test_a_zoomed_in_view_never_downloads_a_national_file(cache, downloads):
    layer = ibge.MunicipalityOutlinesLayer()
    layer.enabled = True
    layer.build(_view(200))
    assert layer.is_due()
    assert layer.refresh_if_due() is True
    assert downloads and all("/paises/BR" not in url for url in downloads)
    layer.reload()
    assert layer.items and layer.data_time is not None


def test_without_state_boxes_the_municipalities_wait_instead_of_using_the_national_file(cache, downloads):
    store.path("ibge", "ufs.geojson").unlink()
    layer = ibge.MunicipalityOutlinesLayer()
    layer.enabled = True
    layer.build(_view(200))
    assert not layer.is_due()
    assert downloads == []


def test_files_already_read_are_shown_at_once_when_the_zoom_changes(cache, downloads):
    layer = ibge.MunicipalityOutlinesLayer()
    layer.enabled = True
    layer.build(_view(200))
    layer.refresh_if_due()       # downloads and reads mun-35-maxima in the "background"
    layer.reload()
    detailed = layer.items
    layer.build(_view(40))       # intermediaria: not downloaded yet, the old outlines stay
    assert layer.items is detailed and layer.is_due()
    layer.build(_view(200))      # back: read already, swapped in without a fetch
    assert layer.items and not layer.is_due()


def test_offline_reads_the_cached_files_of_the_new_level_at_once(cache, monkeypatch):
    store.write_bytes(store.path("ibge", f"mun-{SP}-maxima.geojson"),
                      _collection([("x", (-46.8, -23.7, -46.4, -23.4))]))
    monkeypatch.setattr(config, "FETCH_NEW_DATA_FROM_API", False)
    layer = ibge.MunicipalityOutlinesLayer()
    layer.enabled = True
    layer.build(_view(200))
    assert len(layer.items) == 1


def test_municipality_legend_keeps_the_national_file_age_when_zoomed_out(cache):
    store.write_bytes(store.path("ibge", "municipios.geojson"), _collection([("x", (-50, -20, -49, -19))]))
    layer = ibge.MunicipalityOutlinesLayer()
    layer.reload()
    assert layer.items == [] and layer.data_time is not None


def test_the_brasil_tab_zooms_in_to_256():
    assert tabs.brasil_tabs()[0]["zoom_max"] == 256
