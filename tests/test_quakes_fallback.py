"""Earthquake layer with two sources: IAG-USP first, the USGS feed when IAG-USP cannot answer."""

import json
import time
from pathlib import Path

import pytest

from terrascope.core import config
from terrascope_br import store
from terrascope_br.net import SourceError
from terrascope_br.sources import iag

DATA = Path(__file__).parent / "data"
USP_BODY = (DATA / "iag_quakes.txt").read_bytes()
USP_HEADER_ONLY = USP_BODY.splitlines()[0] + b"\n"


def usgs_body() -> bytes:
    now_ms = int(time.time() * 1000)
    feed = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": "inside1",
             "properties": {"mag": 2.6, "place": "15 km S of Santos", "time": now_ms, "magType": "md"},
             "geometry": {"type": "Point", "coordinates": [-46.6, -23.9, 12.0]}},
            {"type": "Feature", "id": "inside2",
             "properties": {"mag": None, "place": None, "time": now_ms - 3600_000, "magType": None},
             "geometry": {"type": "Point", "coordinates": [-35.0, -8.0, None]}},
            {"type": "Feature", "id": "outside",
             "properties": {"mag": 5.0, "place": "Japan", "time": now_ms, "magType": "mb"},
             "geometry": {"type": "Point", "coordinates": [140.0, 36.0, 30.0]}},
        ],
    }
    return json.dumps(feed).encode("utf-8")


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    return tmp_path


def fake_sources(monkeypatch, usp, usgs):
    """usp / usgs: a (status, body) tuple to answer with, or an exception to raise."""
    def get(url, timeout, **kwargs):
        answer = usp if url.startswith(iag.QUERY_URL) else usgs
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        return status, body, {}

    monkeypatch.setattr(iag, "get", get)


def refresh(layer):
    layer.enabled = True
    return layer.refresh_if_due(force=True)


def test_iag_usp_is_the_main_source(cache, monkeypatch):
    fake_sources(monkeypatch, (200, USP_BODY), SourceError("must not be asked"))
    layer = iag.QuakeLayer()
    assert refresh(layer) is True
    layer.reload()
    assert layer.source_used == iag.SOURCE_USP
    assert layer.items and all(quake["source"] == iag.SOURCE_USP for quake in layer.items)
    assert layer.legend_title() == layer.source_name


def test_http_error_from_iag_uses_usgs(cache, monkeypatch):
    fake_sources(monkeypatch, SourceError("HTTP 500"), (200, usgs_body()))
    layer = iag.QuakeLayer()
    assert refresh(layer) is True
    layer.reload()
    assert layer.source_used == iag.SOURCE_USGS
    assert [quake["id"] for quake in layer.items] == ["inside1", "inside2"]  # Japan is outside the box
    assert layer.legend_title() == "USGS quakes (fallback), 30 days"
    layer.selected = layer.items[0]
    assert dict(layer.info_rows())["SOURCE"] == "USGS (fallback)"


@pytest.mark.parametrize("bad", [(200, b""), (200, b"<html>busy</html>"), (200, b"#Foo|Bar\n1|2\n")])
def test_empty_or_unreadable_iag_answer_uses_usgs(cache, monkeypatch, bad):
    fake_sources(monkeypatch, bad, (200, usgs_body()))
    layer = iag.QuakeLayer()
    refresh(layer)
    layer.reload()
    assert layer.source_used == iag.SOURCE_USGS


def test_header_only_iag_answer_is_a_valid_empty_catalogue(cache, monkeypatch):
    fake_sources(monkeypatch, (200, USP_HEADER_ONLY), SourceError("must not be asked"))
    layer = iag.QuakeLayer()
    refresh(layer)
    layer.reload()
    assert layer.source_used == iag.SOURCE_USP
    assert layer.items == []


def test_both_sources_failing_leaves_an_empty_layer_with_a_warning(cache, monkeypatch):
    fake_sources(monkeypatch, SourceError("HTTP 503"), SourceError("TimeoutError: timed out"))
    layer = iag.QuakeLayer()
    with pytest.raises(SourceError) as failure:
        refresh(layer)
    assert "IAG-USP: HTTP 503" in str(failure.value) and "USGS: TimeoutError" in str(failure.value)
    layer.reload()
    assert layer.items == []
    lines = layer.legend_lines()
    assert len(lines) == 2 and lines[1].text.startswith("unavailable:")


def test_both_sources_failing_keeps_the_last_good_data(cache, monkeypatch):
    store.write_bytes(store.path("iag", "source.txt"), iag.SOURCE_USP.encode("utf-8"))
    store.write_bytes(store.path("iag", "quakes.txt"), USP_BODY)
    fake_sources(monkeypatch, SourceError("HTTP 503"), SourceError("HTTP 503"))
    layer = iag.QuakeLayer()
    with pytest.raises(SourceError):
        refresh(layer)
    layer.reload()
    assert layer.items, "the last good catalogue stays on screen"
    assert layer.status.startswith("update failed:")
