"""Regression tests for the code review of round 3 (items 21 to 29)."""

import http.client
import json
import time
from pathlib import Path

import numpy as np
import pytest

from terrascope.core import config
from terrascope.core.view import View
from terrascope_br import net, store
from terrascope_br.geo import Ring, draw_outlines
from terrascope_br.net import SourceError
from terrascope_br.sources import chuva_online, ibge, spaguas

DATA = Path(__file__).parent / "data"


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    return tmp_path


# ---- 21: the municipality layer asks for its download from a real build() ----

def test_municipalities_are_requested_after_a_zoomed_build_with_an_empty_cache(cache):
    layer = ibge.MunicipalityOutlinesLayer()
    layer.enabled = True
    assert layer.items == []                       # nothing was ever downloaded
    layer.build(View(-52.0, -15.0, 10.0, 182, 50))  # the path the app takes
    assert layer.is_due() is True


def test_municipalities_are_not_requested_when_zoomed_out(cache):
    layer = ibge.MunicipalityOutlinesLayer()
    layer.enabled = True
    layer.build(View(-52.0, -15.0, 1.0, 182, 50))
    assert layer.is_due() is False


# ---- 22 and 23: the legend reflects the state now, without a reload ---------

def test_a_failed_fetch_shows_in_the_legend_without_a_reload(cache):
    store.write_bytes(store.path("iag", "source.txt"), b"IAG-USP")
    store.write_bytes(store.path("iag", "quakes.txt"), (DATA / "iag_quakes.txt").read_bytes())
    from terrascope_br.sources import iag

    layer = iag.QuakeLayer()
    layer.reload()
    assert not layer.has_problem()
    layer.error = "HTTP 503"                       # what refresh_if_due() sets on failure
    assert layer.legend_lines()[1].text == "update failed: HTTP 503"


def test_ageing_data_turns_into_no_update_since_without_a_reload(cache):
    from terrascope_br.sources import iag

    store.write_bytes(store.path("iag", "source.txt"), b"IAG-USP")
    store.write_bytes(store.path("iag", "quakes.txt"), (DATA / "iag_quakes.txt").read_bytes())
    layer = iag.QuakeLayer()
    layer.reload()
    assert not layer.legend_lines()[-1].text.startswith("no update since")
    layer.data_time = time.time() - 3 * 24 * 3600   # time passes; nothing is reloaded
    assert layer.legend_lines()[1].text.startswith("no update since")


# ---- 24: no cache file means "no data yet", not an English error -------------

def test_missing_map_file_gives_no_data_not_an_error(cache):
    layer = ibge.StateOutlinesLayer()
    assert layer.parse() == ([], None)
    layer.reload()
    assert layer.legend_lines()[1].text == "no data yet"


# ---- 25: a damaged or partly wrong municipality file --------------------------

def test_a_damaged_municipality_file_is_removed_and_the_gauges_still_show(cache):
    file = store.path("ibge", ibge.NAMES_FILE)
    store.write_bytes(file, b'{"not": "a list"}')
    assert ibge.municipality_names() == {}
    assert not file.exists()
    layer = spaguas.RainGaugeLayer()
    layer.reload()                                  # no exception, the readings still parse
    assert layer.read_error == ""


def test_partly_wrong_municipality_entries_keep_the_valid_ones(cache, monkeypatch):
    body = json.dumps([
        {"municipio-id": 3556354, "municipio-nome": "Vargem", "UF-sigla": "SP"},
        {"municipio-id": "x", "municipio-nome": "Broken", "UF-sigla": "SP"},
        {"municipio-nome": "No code", "UF-sigla": "SP"},
    ]).encode("utf-8")
    monkeypatch.setattr(ibge, "get", lambda *a, **k: (200, body, {}))
    ibge.ensure_municipality_names()
    assert ibge.municipality_names() == {"3556354": ("Vargem", "SP")}
    saved = json.loads(store.read_bytes(store.path("ibge", ibge.NAMES_FILE)).decode("utf-8"))
    assert saved == [{"municipio-id": 3556354, "municipio-nome": "Vargem", "UF-sigla": "SP"}]


def test_an_invalid_municipality_list_is_not_written(cache, monkeypatch):
    monkeypatch.setattr(ibge, "get", lambda *a, **k: (200, b'{"oops": 1}', {}))
    with pytest.raises(SourceError):
        ibge.ensure_municipality_names()
    assert not store.path("ibge", ibge.NAMES_FILE).exists()


# ---- 26: a cut connection is a SourceError, not a crash ----------------------

class _Cut:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    headers = {}
    status = 200

    def read(self, *_):
        raise http.client.IncompleteRead(b"partial")


def test_an_incomplete_read_becomes_a_source_error(monkeypatch):
    monkeypatch.setattr(net.urllib.request, "urlopen", lambda *a, **k: _Cut())
    with pytest.raises(SourceError, match="IncompleteRead"):
        net.get("https://example.invalid/x", 5.0, min_gap=0.0)


def test_a_broken_deflate_body_becomes_a_source_error():
    import zlib

    with pytest.raises(SourceError):
        net.decode_body(b"\x1f\x8b" + zlib.compress(b"x")[:6], {})


# ---- 27: unchanged frames are a success --------------------------------------

def test_unchanged_frames_and_one_failure_is_not_a_failed_fetch(cache, monkeypatch):
    layer = chuva_online.RadarLayer()
    store.write_bytes(layer.stamp_file(), json.dumps({"fetched": time.time(), "modified": {"1": time.time()}}).encode())
    store.write_bytes(store.path("chuva_online", "loop_1.png"), (DATA / "chuva_loop10.png").read_bytes())

    def answer(url, timeout, **kwargs):
        if url.endswith("_1.png"):
            return 304, b"", {}
        raise SourceError("TimeoutError: timed out")

    monkeypatch.setattr(chuva_online, "get", answer)
    layer.fetch()  # must not raise: the current frame is still there


# ---- 28: the view can cross the antimeridian ----------------------------------

def test_an_outline_across_the_antimeridian_is_drawn_when_the_view_wraps():
    # A small square at lon -190..-180 is the same place as 170..180 east.
    ring = Ring([(-190.0, -5.0), (-180.0, -5.0), (-180.0, 5.0), (-190.0, 5.0), (-190.0, -5.0)])
    wrapped_view = View(175.0, 0.0, 3.0, 182, 50)
    lines = draw_outlines([ring], wrapped_view)
    assert any(char != "⠀" for line in lines for char in line)


def test_radar_cells_are_found_when_the_view_is_centred_past_180():
    rgb, mask = chuva_online.decode_frame((DATA / "chuva_loop10.png").read_bytes())
    # 313.3 east is -46.7: the same place as the radar box, seen across the antimeridian.
    cells = chuva_online.cell_colours(rgb, mask, View(313.3, -23.56, 16.0, 182, 50))
    assert cells


# ---- 29: parsed cache files are reused while unchanged -----------------------

def test_the_municipality_names_are_parsed_once_while_the_file_is_unchanged(cache, monkeypatch):
    store.write_bytes(store.path("ibge", ibge.NAMES_FILE),
                      b'[{"municipio-id": 3556354, "municipio-nome": "Vargem", "UF-sigla": "SP"}]')
    calls = []
    real = ibge._parse_names

    def counting(data):
        calls.append(1)
        return real(data)

    monkeypatch.setattr(ibge, "_parse_names", counting)
    ibge.municipality_names()
    ibge.municipality_names()
    assert len(calls) == 1
