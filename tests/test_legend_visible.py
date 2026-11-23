"""The BR layers leave their legend lines out while they draw nothing in the view (legend_visible), keep
them for a message, keep the title line of a layer with its own switch (its colour key leaves), and the radar's legend is a dBZ key of its colour bar."""

import json
import time
from pathlib import Path

import pytest

from terrascope.core import config
from terrascope.core.view import View
from terrascope_br import roads_format as fmt
from terrascope_br import store
from terrascope_br.sources import chuva_online as radar
from terrascope_br.sources import inpe, roads

DATA = Path(__file__).parent / "data"
SAO_PAULO_VIEW = View(-46.7, -23.56, 16.0, 182, 50)
FAR_VIEW = View(-60.0, 5.0, 64.0, 182, 50)   # over the north of the Amazon, far from every sample
RAIN = (DATA / "chuva_loop10.png").read_bytes()


@pytest.fixture
def folders(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "USER_CONFIG_DIR", tmp_path / "config")
    return tmp_path


def _fires(lat, lon):
    layer = inpe.FireLayer()
    layer.items = [{"lat": lat, "lon": lon, "satellite": "NOAA-21", "epoch": time.time() - 60}]
    layer.data_time = time.time()
    return layer


def test_a_point_layer_is_listed_only_while_a_point_is_in_view(folders):
    layer = _fires(-23.55, -46.63)                       # São Paulo
    layer.render(SAO_PAULO_VIEW)
    assert layer.legend_visible() is True
    layer.render(FAR_VIEW)
    assert layer.legend_visible() is False


def test_a_layer_with_no_data_or_a_failed_fetch_keeps_its_message(folders):
    layer = inpe.FireLayer()                             # no data yet: the legend says so
    layer.render(SAO_PAULO_VIEW)
    assert layer.legend_visible() is True
    layer = _fires(-23.55, -46.63)
    layer.error = "HTTP 503"
    layer.render(FAR_VIEW)
    assert layer.legend_visible() is True


def test_a_switched_off_layer_keeps_its_line_to_switch_it_back(folders):
    layer = roads.RoadsLayer()
    layer.shown = False
    layer.render(SAO_PAULO_VIEW)
    assert layer.legend_visible() is True


def test_the_roads_keep_their_switch_and_show_their_classes_only_while_a_road_is_in_view(folders):
    layer = roads.RoadsLayer()
    layer.enabled = True
    layer.shown = True
    store.write_bytes(store.path("viario", roads.FILE_NAME), _road_file(folders))
    layer.reload()
    layer.render(SAO_PAULO_VIEW)
    assert layer.legend_visible() is True
    assert len(layer.legend_lines()) > 1                  # title with its switch, then the classes
    layer.render(FAR_VIEW)
    # Nothing in view: the title stays with its switch (it can be switched off from here), the
    # classes leave the legend.
    assert layer.legend_visible() is True
    [title] = layer.legend_lines()
    assert title.action == roads.RoadsLayer.switch_action and title.choice_index == 0


def _road_file(tmp_path):
    import numpy as np

    from terrascope_br.roads_format import Polylines, write
    line = np.asarray([(-46.70, -23.55), (-46.55, -23.55)])
    polylines = {}
    for name, _tags, _zoom in fmt.CLASSES:
        for index in fmt.ranges_of_class(name):
            if name == "motorway":
                xy = np.rint(line * fmt.SCALE).astype(np.int32)
                polylines[(name, index)] = Polylines(xy, np.array([0, len(xy)], dtype=np.int32))
            else:
                polylines[(name, index)] = Polylines(np.zeros((0, 2), dtype=np.int32), np.zeros(1, dtype=np.int32))
    target = tmp_path / "roads-visible.npz"
    write(target, polylines, {"built": "2026-11-22T00:00:00Z", "built_epoch": int(time.time())})
    return target.read_bytes()


# ---- the radar -----------------------------------------------------------------------

def _radar(folders, frame, epoch):
    radar._decoded.clear()                                # decoded frames are kept by file name
    store.write_bytes(radar._frame_file(epoch), frame)
    store.write_bytes(store.path("chuva_online", "frames.json"), json.dumps({
        "version": radar.MANIFEST_VERSION, "fetched": time.time(), "slots": {}, "frames": [epoch],
    }).encode("utf-8"))
    layer = radar.RadarLayer()
    layer.enabled = True
    layer.reload()
    return layer


def test_the_radar_keeps_its_switch_and_shows_its_key_only_while_its_frame_has_rain_in_view(folders):
    rainy = _radar(folders, RAIN, int(time.time()) - 60)
    rainy.render(SAO_PAULO_VIEW)
    assert rainy.legend_visible() is True
    dry = _radar(folders, _blank_png(), int(time.time()) - 120)
    assert len(rainy.legend_lines()) > 1                  # title with its switch, then the dBZ key
    dry.render(SAO_PAULO_VIEW)
    assert dry.legend_visible() is True                   # the switch stays reachable
    [title] = dry.legend_lines()
    assert title.action == radar.RadarLayer.switch_action


def _blank_png() -> bytes:
    import io

    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGBA", (1000, 1000), (0, 0, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_the_radar_key_is_the_colour_bar_in_dbz():
    key = radar.RadarLayer().legend_key()
    assert [label for _glyph, _colour, label in key] == ["10", "20", "30", "40", "50+ dBZ"]
    assert [colour for _glyph, colour, _label in key] == [radar.PALETTE_HEX[index] for index in (0, 10, 20, 30, 40)]
