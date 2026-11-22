"""Chuva Online frame decoding, colouring and the stale-data rule."""

import json
import time
from pathlib import Path

import pytest

from terrascope.core import config
from terrascope.core.view import View
from terrascope_br import store
from terrascope_br.sources import chuva_online as radar

DATA = Path(__file__).parent / "data"
SAO_PAULO_VIEW = (-46.7, -23.56, 16.0, 182, 50)


def _frame():
    return radar.decode_frame((DATA / "chuva_loop10.png").read_bytes())


def test_frame_mask_skips_the_colour_bar_and_the_date_label():
    _rgb, mask = _frame()
    assert mask.sum() > 1000
    assert not mask[:, 960:].any()  # colour bar is right of the disc
    assert not mask[:20, :].any()   # date label is at the top


def test_cell_colours_are_hex_and_saturated():
    rgb, mask = _frame()
    cells = radar.cell_colours(rgb, mask, View(*SAO_PAULO_VIEW))
    assert cells, "a view over the radar box should show rain cells"
    for colour in cells.values():
        r, g, b = int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)
        assert max(r, g, b) - min(r, g, b) >= radar.MIN_SATURATION


def test_view_outside_the_box_has_no_cells():
    rgb, mask = _frame()
    assert radar.cell_colours(rgb, mask, View(10.0, 40.0, 16.0, 182, 50)) == {}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    return tmp_path


def _layer_with_frame(epoch: int) -> radar.RadarLayer:
    """A layer whose cache holds the sample frame, stamped with `epoch`."""
    store.write_bytes(radar._frame_file(epoch), (DATA / "chuva_loop10.png").read_bytes())
    store.write_bytes(store.path("chuva_online", "frames.json"), json.dumps({
        "version": radar.MANIFEST_VERSION, "fetched": time.time(),
        "slots": {"1": epoch}, "frames": [epoch],
    }).encode("utf-8"))
    layer = radar.RadarLayer()
    layer.reload()
    return layer


def test_old_frame_is_hidden_and_announced(cache):
    layer = _layer_with_frame(int(time.time()) - 2 * 3600)
    assert not layer.build(View(*SAO_PAULO_VIEW)).bg_cells
    assert layer.status.startswith("no update since")


def test_fresh_frame_is_drawn(cache):
    layer = _layer_with_frame(int(time.time()) - 300)
    assert layer.build(View(*SAO_PAULO_VIEW)).bg_cells


# ---- levels and half cells (the detail of the core's weather radar) ---------------------

import numpy as np  # noqa: E402


def _levels():
    rgb, mask = radar.decode_frame((DATA / "chuva_loop10.png").read_bytes())
    return radar.frame_levels(rgb, mask), mask


def test_every_drawn_colour_is_a_band_of_the_colour_bar():
    levels, mask = _levels()
    assert len(radar.PALETTE) == 50                       # 10 to 60 dBZ, 1 dBZ each
    assert np.array_equal(levels > 0, mask)               # every drawn pixel has its band
    assert levels.max() <= 50


def test_a_half_cell_needs_half_of_its_points_wet_and_takes_their_mean_band():
    levels = np.zeros((10, 10), dtype=np.uint8)
    view = View(-46.735, -23.56, 16.0, 1, 1)
    full = radar.half_cell_levels(np.full((10, 10), 20, dtype=np.uint8), View(*radar_box_view()))
    assert full.shape == (2, 1) and (full == 20).all()
    sparse = levels.copy()
    sparse[0, 0] = 30                                     # one wet pixel in a hundred
    assert (radar.half_cell_levels(sparse, View(*radar_box_view())) == 0).all()
    assert radar.half_cell_levels(levels, view).shape == (2, 1)


def radar_box_view():
    """A one-cell view covering the whole radar box."""
    min_lon, min_lat, max_lon, max_lat = radar.BBOX
    lon_span = max_lon - min_lon
    zoom = 360.0 / lon_span
    return ((min_lon + max_lon) / 2, (min_lat + max_lat) / 2, zoom, 1, 1)


def test_even_halves_are_a_background_and_uneven_ones_a_half_block():
    levels = np.zeros((20, 20), dtype=np.uint8)
    levels[:10, :] = 40                                   # rain on the top half of the box only
    render = radar.radar_render(levels, View(*radar_box_view()))
    assert not render.bg_cells
    [marker] = render.markers
    assert marker.glyph == radar.HALF_BLOCK_UPPER and marker.color == radar.PALETTE_HEX[39]
    assert marker.bg_color is None                        # the dry half shows the map
    levels[:, :] = 40
    render = radar.radar_render(levels, View(*radar_box_view()))
    assert render.bg_cells == {(0, 0): radar.PALETTE_HEX[39]} and not render.markers
    levels[10:, :] = 12
    [marker] = radar.radar_render(levels, View(*radar_box_view())).markers
    assert (marker.color, marker.bg_color) == (radar.PALETTE_HEX[39], radar.PALETTE_HEX[11])


def test_the_frame_has_twice_the_rows_of_detail():
    levels, _mask = _levels()
    render = radar.radar_render(levels, View(*SAO_PAULO_VIEW))
    assert render.markers                                 # half blocks where the halves differ
    assert render.bg_cells
    for marker in render.markers:
        assert marker.glyph in (radar.HALF_BLOCK_UPPER, radar.HALF_BLOCK_LOWER)
        assert marker.color in radar.PALETTE_HEX


def test_the_radar_cells_select_nothing():
    assert radar.RadarLayer().hit_test_all(3, 3) == []
