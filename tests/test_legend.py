"""The legend of each layer stays within two lines, and says something only when the data is not fine."""

import time

import pytest

from terrascope.core.config import CFG  # noqa: F401  (settings are loaded)
from terrascope_br.sources import chuva_online, iag, ibge, inpe, spaguas


def _layers():
    fires = inpe.FireLayer()
    fires.items = [{"lat": -23.5, "lon": -46.6, "satellite": "NOAA-21", "epoch": time.time() - 60}]
    quakes = iag.QuakeLayer()
    quakes.items = [{"id": "x", "epoch": time.time(), "lat": -23.0, "lon": -46.0, "depth": 10.0,
                     "magnitude": 2.0, "mag_type": "mb", "place": "Test", }]
    gauges = spaguas.RainGaugeLayer()
    gauges.items = [{"lat": -23.5, "lon": -46.6, "mm": 1.0, "station": "s", "owner": "o",
                     "city": "c", "updated": time.time()}]
    radar = chuva_online.RadarLayer()
    radar.items = []
    states = ibge.StateOutlinesLayer()
    municipalities = ibge.MunicipalityOutlinesLayer()
    return [fires, quakes, gauges, radar, states, municipalities]


@pytest.mark.parametrize("layer", _layers(), ids=lambda layer: layer.name)
def test_fresh_layer_has_title_and_at_most_one_key_line(layer):
    layer.data_time = time.time()
    layer.error = ""
    layer.status = layer.freshness_text()
    lines = layer.legend_lines()
    assert len(lines) <= 2
    assert lines[0].text == layer.source_name and lines[0].bold
    assert all(line.text != "updated" for line in lines)  # "updated" is not repeated in the legend


@pytest.mark.parametrize("layer", _layers(), ids=lambda layer: layer.name)
def test_problem_replaces_the_key_with_one_message(layer):
    layer.data_time = time.time() - 365 * 24 * 3600 if layer.name == "br_ufs" else None
    layer.error = "HTTP 503"
    layer.status = layer.freshness_text()
    lines = layer.legend_lines()
    assert len(lines) == 2
    assert lines[1].text == layer.status


def test_stale_radar_says_when_it_was_last_updated():
    layer = chuva_online.RadarLayer()
    layer.data_time = time.time() - 2 * 3600
    layer.error = ""
    layer.status = layer.freshness_text()
    assert layer.legend_lines()[1].text.startswith("no update since")
