"""Chuva Online radar: its on/off switch on the legend line (key T), the slider and the fetch
it hides while off, and the choice kept in the config folder between runs."""

import json
import time
from pathlib import Path

import pytest

from terrascope.core import config
from terrascope.core.view import View
from terrascope_br import store
from terrascope_br.sources import chuva_online as radar

SAO_PAULO_VIEW = View(-46.7, -23.56, 16.0, 182, 50)
RAIN = (Path(__file__).parent / "data" / "chuva_loop10.png").read_bytes()


@pytest.fixture
def folders(tmp_path, monkeypatch):
    """The cache and the config folder of the test, both temporary."""
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "USER_CONFIG_DIR", tmp_path / "config")
    return tmp_path / "config" / "br" / radar.SHOWN_FILE


def _layer() -> radar.RadarLayer:
    """A layer with three frames on disk, as fetch() leaves them, switched on for the tab."""
    now = int(time.time())
    epochs = [now - 600, now - 300, now - 60]
    for epoch in epochs:
        store.write_bytes(radar._frame_file(epoch), RAIN)
    store.write_bytes(store.path("chuva_online", "frames.json"), json.dumps({
        "version": radar.MANIFEST_VERSION, "fetched": time.time(), "slots": {}, "frames": epochs,
    }).encode("utf-8"))
    layer = radar.RadarLayer()
    layer.enabled = True
    layer.reload()
    return layer


def test_the_legend_line_is_the_switch_and_the_key_is_t(folders):
    layer = _layer()
    assert layer.toggle_key == "t"
    title = layer.legend_lines()[0]
    assert title.action == radar.SWITCH_ACTION == "br_radar.toggle"
    assert title.choices and title.choice_index == 0       # on
    assert len(layer.legend_lines()) == 2                   # the title, then the colour key


def test_the_key_switches_it_off_and_the_line_says_so(folders):
    layer = _layer()
    assert layer.handle_key(ord("x")) is False
    assert layer.handle_key(ord("T")) is True
    assert layer.shown is False
    assert layer.enabled is True, "the switch must not turn the layer off for the tab (its line would vanish)"
    lines = layer.legend_lines()
    assert len(lines) == 1 and lines[0].action == radar.SWITCH_ACTION and lines[0].choice_index == 1


def test_the_legend_line_switches_it_back_on(folders):
    layer = _layer()
    layer.press_legend(radar.SWITCH_ACTION)
    assert layer.shown is False and layer.legend_lines()[0].choice_index == 1
    layer.press_legend(radar.SWITCH_ACTION)
    assert layer.shown is True and layer.legend_lines()[0].choice_index == 0
    layer.press_legend("br_other.toggle")                   # another layer's action: not ours
    assert layer.shown is True


def test_off_the_slider_is_hidden_and_nothing_is_drawn(folders):
    layer = _layer()
    assert layer.slider() is not None and layer.build(SAO_PAULO_VIEW).bg_cells
    layer.handle_key(ord("t"))
    assert layer.slider() is None
    layer.toggle_slider_play()                              # no slider: nothing to play
    drawn = layer.render(SAO_PAULO_VIEW)
    assert not drawn.bg_cells and not drawn.markers and layer.last_markers == []
    layer._advance(10_000.0)
    assert layer.dirty is False


def test_off_it_does_not_fetch(folders, monkeypatch):
    calls = []
    layer = _layer()
    monkeypatch.setattr(layer, "fetch", lambda: calls.append(1))
    layer.handle_key(ord("t"))
    assert layer.is_due() is False
    assert layer.refresh_if_due(force=True) is False
    assert calls == []


def test_the_choice_is_saved_and_restored_on_the_next_run(folders):
    layer = _layer()
    layer.handle_key(ord("t"))
    assert json.loads(folders.read_text(encoding="utf-8")) == {"shown": False}
    assert radar.RadarLayer().shown is False               # a new run starts off
    restarted = _layer()
    restarted.press_legend(radar.SWITCH_ACTION)
    assert json.loads(folders.read_text(encoding="utf-8")) == {"shown": True}
    assert radar.RadarLayer().shown is True


def test_no_file_or_a_damaged_one_means_on(folders):
    assert radar.RadarLayer().shown is True                 # no file yet
    folders.parent.mkdir(parents=True)
    for text in ("{not json", '{"shown": "no"}', "[false]", '{"shown": 0}'):
        folders.write_text(text, encoding="utf-8")
        assert radar.RadarLayer().shown is True, text
    folders.write_text('{"shown": false}', encoding="utf-8")
    assert radar.RadarLayer().shown is False
