"""Parsers of the public responses, run on trimmed samples from the day of capture."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from terrascope.core.view import View
from terrascope_br import net
from terrascope_br.geo import draw_outlines, rings_from_geojson
from terrascope_br.sources import iag, inpe, spaguas

DATA = Path(__file__).parent / "data"


def test_iag_table_parses_every_event():
    text = (DATA / "iag_quakes.txt").read_text(encoding="utf-8")
    quakes = iag.parse_text(text)
    rows = [line for line in text.splitlines()[1:] if line.strip()]
    assert len(quakes) == len(rows) > 0
    assert all(-34 <= quake["lat"] <= 6 and -74 <= quake["lon"] <= -34 for quake in quakes)


def test_iag_rejects_other_formats():
    with pytest.raises(ValueError):
        iag.parse_text("<html>error</html>")


def test_iag_magnitude_bands():
    assert iag.band_of(1.7)[0] == iag.BANDS[0][1]
    assert iag.band_of(3.0)[0] == iag.BANDS[1][1]
    assert iag.band_of(4.6)[0] == iag.BANDS[2][1]
    assert iag.band_of(6.2)[0] == iag.BANDS[3][1]
    assert iag.band_of(None)[1] == "o"


def test_inpe_csv_keeps_only_the_last_three_hours():
    text = (DATA / "inpe_focos_sample.csv").read_text(encoding="utf-8")
    # The data column runs up to 15:24 UTC; read at 18:00 every row is in the window.
    seen_at = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc).timestamp()
    rows = inpe.parse_csv(text, seen_at)
    assert rows and all(seen_at - row["epoch"] <= 3 * 3600 for row in rows)
    # Read more than three hours after the newest row of the file: nothing is kept.
    newest = max(
        datetime.strptime(line.split(",")[3].strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        for line in text.splitlines()[1:] if line.strip()
    )
    assert inpe.parse_csv(text, newest + 3 * 3600 + 1) == []


def test_inpe_index_selects_recent_files_oldest_first():
    html = (DATA / "inpe_index_sample.html").read_text(encoding="utf-8")
    now = datetime(2026, 11, 10, 20, 25, tzinfo=timezone.utc)
    names = inpe.wanted_files(html, now)
    assert names == sorted(names)
    assert 18 <= len(names) <= 20
    cutoff = now - inpe.WINDOW - timedelta(minutes=10)
    assert all(inpe.file_time(name) >= cutoff for name in names)


def test_spaguas_readings_map_colours_and_city_names():
    payload = (DATA / "spaguas_sample.json").read_bytes()
    readings = spaguas.parse_measurements(payload, {"3556354": ("Vargem", "SP")})
    assert len(readings) == 30
    assert any(reading["city"] == "Vargem/SP" for reading in readings)
    assert all(isinstance(reading["mm"], float) for reading in readings)


def test_spaguas_rain_colours():
    assert spaguas.rain_color(0.0) == (spaguas.DRY_COLOR, True)
    assert spaguas.rain_color(1.0)[0] == spaguas.RAIN_BANDS[0][1]
    assert spaguas.rain_color(5.0)[0] == spaguas.RAIN_BANDS[1][1]
    assert spaguas.rain_color(15.0)[0] == spaguas.RAIN_BANDS[2][1]
    assert spaguas.rain_color(30.0)[0] == spaguas.RAIN_BANDS[3][1]


def test_spaguas_rejects_a_payload_without_measurements():
    with pytest.raises(ValueError):
        spaguas.parse_measurements(b'{"error": "down"}', {})


def test_ibge_states_decode_and_draw():
    body = net.decode_body((DATA / "ibge_ufs.geojson.gz").read_bytes(), {"Content-Encoding": "gzip"})
    rings = rings_from_geojson(json.loads(body.decode("utf-8")))
    assert len(rings) >= 27
    lines = draw_outlines(rings, View(-52.0, -15.0, 1.0, 182, 50))
    assert len(lines) == 50
    assert any(char != "⠀" for line in lines for char in line)


def test_gzip_body_is_detected_without_a_header_and_bad_gzip_fails():
    raw = (DATA / "ibge_ufs.geojson.gz").read_bytes()
    assert net.decode_body(raw, {}).startswith(b"{")
    with pytest.raises(net.SourceError):
        net.decode_body(b"\x1f\x8bnot really gzip", {})
