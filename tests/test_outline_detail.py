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


def test_without_state_boxes_only_the_small_state_file_is_fetched(cache, monkeypatch):
    # br_ufs switched off and never cached: the municipality layer fetches the boxes itself.
    store.path("ibge", "ufs.geojson").unlink()
    urls = []

    def fake_get(url, timeout, **kwargs):
        urls.append(url)
        if "/paises/BR" in url:
            return 200, _collection([(SP, (-53.0, -25.5, -44.0, -19.5))]), {}
        return 200, _collection([("x", (-46.8, -23.7, -46.4, -23.4))]), {}

    monkeypatch.setattr(ibge, "get", fake_get)
    layer = ibge.MunicipalityOutlinesLayer()
    layer.enabled = True
    assert layer.build(_view(200)).lines is None
    assert layer.is_due()
    layer.refresh_if_due()
    assert len(urls) == 1 and "intrarregiao=UF" in urls[0]   # the state file, never the municipalities
    layer.build(_view(200))
    assert [f.name for f, _u in layer._wanted] == [f"mun-{SP}-maxima.geojson"]
    layer.refresh_if_due()
    layer.reload()
    assert layer.items
    assert all("intrarregiao=municipio" not in url or "/estados/" in url for url in urls)


def test_a_view_over_the_sea_draws_no_outline_and_fetches_nothing(cache, downloads):
    layer = ibge.StateOutlinesLayer()
    layer.enabled = True
    layer.build(_view(200, lon=-38.0, lat=-28.0))   # Atlantic, boxes known
    assert layer._wanted == () and not layer.is_due() and downloads == []


def test_a_damaged_state_file_is_removed_and_reported(cache, monkeypatch):
    bad = store.path("ibge", f"mun-{SP}-maxima.geojson")
    store.write_bytes(bad, b'{"features": [{"geometry": {"type": "Polygon", "coordinates": [[["a", "b"], ["c", "d"]]]}}]}')
    layer = ibge.MunicipalityOutlinesLayer()
    layer.enabled = True
    layer.build(_view(200))
    with pytest.raises(ibge.SourceError):
        layer.refresh_if_due()
    assert not bad.exists() and layer.error


def test_the_old_outlines_stay_while_the_new_level_is_read(cache, downloads):
    layer = ibge.MunicipalityOutlinesLayer()
    layer.enabled = True
    layer.build(_view(200))
    layer.refresh_if_due()
    layer.reload()
    shown = layer.items
    # The intermediaria file is on disk (another session) but not read yet.
    store.write_bytes(store.path("ibge", f"mun-{SP}-intermediaria.geojson"),
                      _collection([("y", (-47.0, -24.0, -46.0, -23.0))]))
    layer.build(_view(40))
    layer.reload()                    # e.g. a reload from elsewhere before the refresh thread ran
    assert layer.items is shown       # not blanked, not parsed on the UI thread
    assert layer.is_due()


def test_parsed_files_are_bounded(cache):
    for index in range(ibge.PARSED_LIMIT + 5):
        file = store.path("ibge", f"mun-{index}-maxima.geojson")
        store.write_bytes(file, _collection([("x", (0, 0, 1, 1))]))
        ibge._cached(file, ibge._parse_rings)
    assert len(ibge._parsed) == ibge.PARSED_LIMIT


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
