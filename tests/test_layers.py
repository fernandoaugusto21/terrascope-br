"""Layer behaviour that needs no network: cache, refresh failures, legend text,
the info box and the municipality zoom rule."""

import time
from pathlib import Path

import pytest

from terrascope.core import config
from terrascope.core.view import View
from terrascope_br import store
from terrascope_br.net import SourceError
from terrascope_br.sources import iag, ibge, inpe

DATA = Path(__file__).parent / "data"


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    return tmp_path


def test_store_age_and_atomic_write(cache):
    file = store.path("demo", "value.txt")
    assert store.age(file) is None
    store.write_bytes(file, b"x")
    assert file.read_bytes() == b"x"
    assert not file.with_name(file.name + ".tmp").exists()
    assert store.age(file) < 60


def test_failed_fetch_keeps_the_old_cache(cache, monkeypatch):
    def down(*_args, **_kwargs):
        raise SourceError("HTTP 503")

    monkeypatch.setattr(iag, "get", down)
    store.write_bytes(store.path("iag", "quakes.txt"), (DATA / "iag_quakes.txt").read_bytes())
    layer = iag.QuakeLayer()
    layer.enabled = True
    with pytest.raises(SourceError):
        layer.refresh_if_due(force=True)
    assert layer.error == "HTTP 503"
    layer.reload()
    assert layer.items, "the last good data stays on screen"
    assert "updated" in layer.status or "no update since" in layer.status


def test_no_data_and_failed_fetch_is_announced_in_the_legend(cache, monkeypatch):
    def down(*_args, **_kwargs):
        raise SourceError("TimeoutError: timed out")

    monkeypatch.setattr(inpe, "get", down)
    layer = inpe.FireLayer()
    layer.enabled = True
    with pytest.raises(SourceError):
        layer.refresh_if_due(force=True)
    layer.reload()
    assert layer.items == []
    assert layer.status.startswith("unavailable:")
    assert any("unavailable" in line.text for line in layer.legend_lines())


def test_disabled_layer_never_fetches(cache, monkeypatch):
    calls = []
    monkeypatch.setattr(iag, "get", lambda *args, **kwargs: calls.append(args) or (200, b"", {}))
    layer = iag.QuakeLayer()
    assert layer.enabled is False
    assert layer.refresh_if_due(force=False) is False
    assert calls == []


def test_municipality_outlines_wait_for_a_zoom(cache):
    layer = ibge.MunicipalityOutlinesLayer()
    layer.enabled = True
    layer.visible(View(-52.0, -15.0, 1.0, 182, 50))
    assert layer.is_due() is False
    layer.visible(View(-52.0, -15.0, 8.0, 182, 50))
    assert layer.is_due() is True


def test_fire_selection_survives_a_reload(cache):
    now = time.time()
    layer = inpe.FireLayer()
    item = {"lat": -23.5, "lon": -46.6, "satellite": "NOAA-21", "epoch": now - 900}
    layer.items = [item]
    layer.select(dict(item))  # the selection is by value: items are rebuilt on reload
    markers = layer.build(View(-46.6, -23.5, 4.0, 182, 50)).markers
    assert markers and markers[0].selected
    rows = dict(layer.info_rows())
    assert rows["SATELLITE"] == "NOAA-21"
    assert "LOCAL" in rows and "UTC" in rows


def test_legend_has_title_status_and_credit(cache):
    layer = iag.QuakeLayer()
    layer.reload()
    texts = [line.text for line in layer.legend_lines()]
    assert texts[0] == layer.source_name
    assert "IAG-USP" in texts[-1]


def test_municipality_names_come_from_the_cache_only(cache):
    assert ibge.municipality_names() == {}
    store.write_bytes(store.path("ibge", ibge.NAMES_FILE),
                      b'[{"municipio-id": 3556354, "municipio-nome": "Vargem", "UF-sigla": "SP"}]')
    assert ibge.municipality_names()["3556354"] == ("Vargem", "SP")
