"""Chuva Online frame decoding, colouring and the stale-data rule."""

import time
from pathlib import Path

from terrascope.core.view import View
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


def test_old_frame_is_hidden_and_announced():
    layer = radar.RadarLayer()
    rgb, mask = _frame()
    layer.items = [{"rgb": rgb, "mask": mask}]
    layer.data_time = time.time() - 2 * 3600
    layer.status = layer.freshness_text()
    assert not layer.build(View(*SAO_PAULO_VIEW)).bg_cells
    assert layer.status.startswith("no update since")


def test_fresh_frame_is_drawn():
    layer = radar.RadarLayer()
    rgb, mask = _frame()
    layer.items = [{"rgb": rgb, "mask": mask}]
    layer.data_time = time.time() - 300
    assert layer.build(View(*SAO_PAULO_VIEW)).bg_cells
