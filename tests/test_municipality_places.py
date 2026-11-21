"""One label per municipality from zoom 32 on: the point inside each outline,
the order of the labels, and the switch between Natural Earth cities and
municipalities. No network: the files are written to the test cache."""

import json

import numpy as np
import pytest

from terrascope.core import config
from terrascope.core import layer as core_layer
from terrascope.core.view import View
from terrascope.layers.weather import layer as weather_layer
from terrascope_br import store
from terrascope_br.sources import ibge, municipalities as m
from terrascope_br.sources.global_weather import GlobalTempLayer
from terrascope_br.sources.weather_providers import _inside

# A "C" open to the east: its centre (the middle of the box) is outside it.
C_SHAPE = [[0, 0], [3, 0], [3, 1], [1, 1], [1, 2], [3, 2], [3, 3], [0, 3], [0, 0]]


def _square(west, south, size):
    return [[west, south], [west + size, south], [west + size, south + size], [west, south + size], [west, south]]


def test_the_point_of_a_concave_outline_lies_inside_it():
    rings = [np.asarray(C_SHAPE, dtype=float)]
    lon, lat = m.interior_point(rings)
    assert _inside(rings, lon, lat)


def test_the_point_of_a_convex_outline_is_its_centre():
    rings = [np.asarray(_square(0, 0, 2), dtype=float)]
    assert m.interior_point(rings) == pytest.approx((1.0, 1.0))


def test_municipalities_with_a_big_city_come_first_then_the_bigger_ones():
    features = [
        ("1", (0, 0, 1, 1), [np.asarray(_square(0, 0, 1), dtype=float)]),
        ("2", (2, 0, 5, 3), [np.asarray(_square(2, 0, 3), dtype=float)]),
        ("3", (6, 0, 8, 2), [np.asarray(_square(6, 0, 2), dtype=float)]),
    ]
    cities = [(0.5, 0.5, "Capital", True, 1_000_000, "Brazil"), (40.0, 40.0, "Away", False, 9, "Brazil")]
    places = m.build_places(features, cities)
    assert [place[2] for place in places] == ["1", "2", "3"]
    assert places[0][3] == 1_000_000 and places[0][4] is True
    assert places[1][3] is None


def test_a_city_of_unknown_population_leaves_it_unknown():
    features = [("1", (0, 0, 1, 1), [np.asarray(_square(0, 0, 1), dtype=float)])]
    places = m.build_places(features, [(0.5, 0.5, "Town", False, None, "Brazil")])
    assert places[0][3] is None


# -- the layer --------------------------------------------------------------------

STATE = "35"
TOWNS = {"3500001": ("Alfa", "SP"), "3500002": ("Beta", "SP")}


@pytest.fixture
def state_files(monkeypatch):
    """A state box and two municipalities of it in the cache, as IBGE serves them."""
    monkeypatch.setattr(config, "FETCH_NEW_DATA_FROM_API", False)
    monkeypatch.setattr(weather_layer, "FETCH_NEW_DATA_FROM_API", False)
    monkeypatch.setattr(core_layer, "FETCH_NEW_DATA_FROM_API", False)
    def feature(code, ring):
        return {"type": "Feature", "properties": {"codarea": code},
                "geometry": {"type": "Polygon", "coordinates": [ring]}}
    store.write_bytes(store.path("ibge", "ufs.geojson"), json.dumps(
        {"features": [feature(STATE, _square(-47.0, -24.0, 1.0))]}).encode())
    store.write_bytes(m.minima_piece(STATE)[0], json.dumps({"features": [
        feature("3500001", _square(-47.0, -24.0, 0.5)), feature("3500002", _square(-46.5, -23.5, 0.5)),
    ]}).encode())
    store.write_bytes(store.path("ibge", ibge.NAMES_FILE), json.dumps([
        {"municipio-id": int(code), "municipio-nome": name, "UF-sigla": uf} for code, (name, uf) in TOWNS.items()
    ]).encode())
    m._ready.clear()
    m.load_names()
    yield
    m._ready.clear()


def _layer():
    layer = GlobalTempLayer()
    layer._capitals = []
    layer._cities = [(-49.0, -22.0, "Natural Earth town", False, 10_000_000, "Brazil")]
    return layer


def test_below_zoom_32_the_layer_keeps_the_natural_earth_cities(state_files):
    layer = _layer()
    names = [place[2] for place in layer.city_places(View(-49.0, -22.0, 16, 120, 40))]
    assert names == ["Natural Earth town"]
    assert not layer.is_due()


def test_from_zoom_32_the_municipalities_in_view_are_labelled_once_ready(state_files):
    layer = _layer()
    view = View(-46.5, -23.5, 64, 160, 50)
    # Not read yet: the Natural Earth cities stay, and the layer asks for a refresh.
    assert [place[2] for place in layer.city_places(view)] == ["Natural Earth town"]
    assert layer.is_due()
    m.prepare_state(STATE, layer._cities)  # what the refresh thread does
    assert not layer.is_due()
    # The Natural Earth list grows (it loads in the background): the places are redone.
    layer._capitals = [(-46.9, -23.9, "Capital", True, 1_000_000, "Brazil")]
    assert layer.is_due()
    places = list(layer.city_places(view))
    assert sorted(place[2] for place in places) == ["Alfa", "Beta"]
    assert all(place[5] == "Brazil · SP" and place[4] is None for place in places)


def test_the_refresh_reads_the_states_of_the_view(state_files, monkeypatch):
    layer = _layer()
    layer.enabled = True
    list(layer.city_places(View(-46.5, -23.5, 64, 160, 50)))
    monkeypatch.setattr(config, "FETCH_NEW_DATA_FROM_API", True)
    monkeypatch.setattr(ibge, "ensure_municipality_names", lambda: None)
    assert layer.refresh_if_due()
    assert m.is_ready(STATE, layer._city_count())


def test_offline_the_cached_files_are_still_read_once(state_files, monkeypatch):
    layer = _layer()
    layer.enabled = True
    list(layer.city_places(View(-46.5, -23.5, 64, 160, 50)))
    started = []
    monkeypatch.setattr(layer, "refresh_async", lambda: started.append(1))
    layer.tick()
    layer.tick()
    assert started == [1]  # once for these states, not on every tick
    assert layer.refresh_if_due()  # offline: reads the cache, downloads nothing
    assert m.is_ready(STATE, layer._city_count())


def test_one_failing_state_does_not_stop_the_others(state_files, monkeypatch):
    from terrascope_br.net import SourceError

    layer = _layer()
    layer.enabled = True
    layer._marker_states = ("11", STATE)  # "11" has no file and its download fails
    monkeypatch.setattr(config, "FETCH_NEW_DATA_FROM_API", True)
    monkeypatch.setattr(ibge, "ensure_municipality_names", lambda: None)
    def refuse(url, file):
        raise SourceError("HTTP 503")
    monkeypatch.setattr(ibge, "_download", refuse)
    with pytest.raises(SourceError):
        layer.refresh_if_due()
    assert m.is_ready(STATE, layer._city_count())


def test_a_state_not_ready_keeps_its_natural_earth_cities(state_files):
    layer = _layer()
    layer._cities = [(-46.4, -23.4, "Big", False, 10_000_000, "Brazil")]
    m.prepare_state(STATE, layer._cities)
    view = View(-46.5, -23.5, 64, 160, 50)
    list(layer.city_places(view))
    layer._marker_states = (STATE, "31")  # a second state, not read yet
    names = [place[2] for place in layer.city_places(view)]
    assert "Alfa" in names and "Big" in names


def test_a_place_across_the_coarse_state_border_finds_its_municipality(state_files):
    """The national state file says state 35 ends at -46.0; the municipality file of
    state 31 has a town just east of that. Its place is found in state 31."""
    from terrascope_br.sources.weather_providers import _found, municipality_at

    def feature(code, ring):
        return {"type": "Feature", "properties": {"codarea": code},
                "geometry": {"type": "Polygon", "coordinates": [ring]}}
    store.write_bytes(store.path("ibge", "ufs.geojson"), json.dumps({"features": [
        feature(STATE, _square(-47.0, -24.0, 1.0)), feature("31", _square(-45.9, -24.0, 1.0))]}).encode())
    store.write_bytes(m.minima_piece("31")[0], json.dumps({"features": [
        feature("3100001", [[-46.05, -23.6], [-45.5, -23.6], [-45.5, -23.4], [-46.05, -23.4], [-46.05, -23.6]]),
    ]}).encode())
    _found.clear()
    assert municipality_at(-23.5, -45.98) == "3100001"


def test_a_labelled_municipality_point_is_found_again_by_the_providers(state_files):
    from terrascope_br.sources.weather_providers import _found, municipality_at

    m.prepare_state(STATE, [])
    _found.clear()
    for lon, lat, code, _population, _capital, _area in m.ready_places(STATE):
        assert municipality_at(lat, lon) == code
