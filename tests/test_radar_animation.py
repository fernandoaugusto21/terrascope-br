"""Chuva Online radar: the frames kept between fetches, the slider and the loop.

The site is a fake that answers like the real files (200 with Last-Modified, or
304 when If-Modified-Since is not older than the file). No request leaves the test."""

import io
import json
import time
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path

import pytest
from PIL import Image

from terrascope.core import config
from terrascope.core.view import View
from terrascope_br import store
from terrascope_br.net import SourceError
from terrascope_br.sources import chuva_online as radar

DATA = Path(__file__).parent / "data"
SAO_PAULO_VIEW = View(-46.7, -23.56, 16.0, 182, 50)
RAIN = (DATA / "chuva_loop10.png").read_bytes()
LOOP_KEY = "layers.weather.radar.animate"


def _blank_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (1000, 1000), (0, 0, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


BLANK = _blank_png()


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    radar._decoded.clear()
    return tmp_path


@pytest.fixture
def core_setting():
    """Set a core radar setting for one test and put the old value back."""
    saved = {}

    def set_value(path, value):
        saved.setdefault(path, config.get_path(path))
        config.set_path(path, value)

    yield set_value
    for path, value in saved.items():
        config.set_path(path, value)


class FakeSite:
    """The ten loop files. `slots[n] = (epoch, png)` is what file n holds now."""

    def __init__(self, monkeypatch):
        self.slots: dict[int, tuple[int, bytes]] = {}
        self.calls: list[tuple[int, str | None]] = []
        self.downloads = 0
        monkeypatch.setattr(radar, "get", self.get)

    def get(self, url, timeout, *, min_gap=1.0, if_modified_since=None):
        slot = int(url.rsplit("_", 1)[1].split(".")[0])
        self.calls.append((slot, if_modified_since))
        if slot not in self.slots:
            raise SourceError("HTTP 404")
        epoch, png = self.slots[slot]
        if if_modified_since and epoch <= parsedate_to_datetime(if_modified_since).timestamp():
            return 304, b"", {}
        self.downloads += 1
        return 200, png, {"Last-Modified": formatdate(epoch, usegmt=True)}


@pytest.fixture
def site(monkeypatch):
    return FakeSite(monkeypatch)


def _loop(now: int, count: int = 10, spacing: int = 180) -> dict[int, tuple[int, bytes]]:
    """Ten files, oldest first, `spacing` seconds apart, the newest ending at `now`."""
    return {slot: (now - (count - slot) * spacing, RAIN) for slot in range(1, count + 1)}


def _publish(frames: list[tuple[int, bytes]]):
    """Write frames the way fetch() leaves them, and load them as the layer does."""
    for epoch, png in frames:
        store.write_bytes(radar._frame_file(epoch), png)
    store.write_bytes(store.path("chuva_online", "frames.json"), json.dumps({
        "version": radar.MANIFEST_VERSION, "fetched": time.time(), "slots": {},
        "frames": [epoch for epoch, _png in frames],
    }).encode("utf-8"))
    layer = radar.RadarLayer()
    layer.enabled = True
    layer.reload()
    return layer


def _kept_on_disk() -> list[str]:
    return sorted(path.name for path in store.path("chuva_online").glob("*.png"))


# ---- what fetch() keeps -----------------------------------------------------------

def test_a_fetch_keeps_every_frame_of_the_loop(cache, site):
    now = int(time.time())
    site.slots = _loop(now)
    layer = radar.RadarLayer()
    layer.fetch()
    layer.reload()
    assert [item["time"] for item in layer.items] == [epoch for epoch, _png in _loop(now).values()]
    assert layer.data_time == now  # the newest file of the loop
    assert site.calls == [(slot, None) for slot in range(1, 11)]  # nothing known yet


def test_later_fetches_ask_conditionally_and_add_the_new_frame(cache, site):
    now = int(time.time())
    site.slots = _loop(now)
    layer = radar.RadarLayer()
    layer.fetch()
    site.slots[1] = (now + 60, RAIN)  # file 1 now holds a newer frame; the rest are unchanged
    site.calls.clear()
    site.downloads = 0
    layer.fetch()
    layer.reload()
    assert all(ims is not None for _slot, ims in site.calls)  # every known file is asked conditionally
    assert site.downloads == 1  # only the file that changed came back with a body
    assert len(layer.items) == 11
    assert layer.data_time == now + 60


def test_frames_older_than_an_hour_are_dropped_and_their_files_removed(cache, site):
    now = int(time.time())
    site.slots = {1: (now - 2 * 3600, RAIN), 2: (now - 2 * 3600 + 120, RAIN), 3: (now - 600, RAIN)}
    radar.RadarLayer().fetch()
    assert _kept_on_disk() == [f"frame_{now - 600}.png"]


def test_the_newest_frame_is_kept_even_when_the_site_is_old(cache, site):
    now = int(time.time())
    site.slots = {1: (now - 3 * 3600, RAIN), 2: (now - 3 * 3600 + 120, RAIN)}
    radar.RadarLayer().fetch()
    layer = radar.RadarLayer()
    layer.reload()
    assert [item["time"] for item in layer.items] == [now - 3 * 3600 + 120]
    assert layer.is_stale()  # shown as "no update since", not as current


def test_the_history_is_capped(cache, site, monkeypatch):
    monkeypatch.setattr(radar, "MAX_FRAMES", 3)
    now = int(time.time())
    site.slots = _loop(now)
    radar.RadarLayer().fetch()
    layer = radar.RadarLayer()
    layer.reload()
    assert [item["time"] for item in layer.items] == [now - 2 * 180, now - 180, now]


def test_a_failed_fetch_keeps_the_cache_as_it_was(cache, site):
    now = int(time.time())
    site.slots = _loop(now)
    layer = radar.RadarLayer()
    layer.fetch()
    before = (layer.stamp_file().read_bytes(), _kept_on_disk())
    site.slots = {}  # every request now fails with a 404
    with pytest.raises(SourceError):
        layer.fetch()
    assert (layer.stamp_file().read_bytes(), _kept_on_disk()) == before


def test_a_body_that_is_not_an_image_is_not_kept(cache, site):
    site.slots = {1: (int(time.time()), b"<html>maintenance</html>")}
    with pytest.raises(SourceError):
        radar.RadarLayer().fetch()
    assert _kept_on_disk() == []


def test_cache_of_an_older_version_is_replaced_and_its_files_removed(cache, site):
    now = int(time.time())
    store.write_bytes(store.path("chuva_online", "loop_1.png"), RAIN)
    store.write_bytes(store.path("chuva_online", "frames.json"), json.dumps(
        {"fetched": time.time(), "modified": {"1": now - 600}}).encode("utf-8"))
    site.slots = _loop(now, count=2)
    radar.RadarLayer().fetch()
    assert all(name.startswith("frame_") for name in _kept_on_disk())
    assert json.loads(store.path("chuva_online", "frames.json").read_bytes())["version"] == radar.MANIFEST_VERSION


def test_a_manifest_of_another_version_is_ignored_by_the_reader(cache):
    store.write_bytes(store.path("chuva_online", "frames.json"), json.dumps(
        {"version": 1, "frames": [int(time.time())]}).encode("utf-8"))
    assert radar.RadarLayer().parse() == ([], None)


def test_frames_whose_file_is_missing_are_not_listed(cache):
    now = int(time.time())
    store.write_bytes(store.path("chuva_online", "frames.json"), json.dumps({
        "version": radar.MANIFEST_VERSION, "slots": {}, "frames": [now - 60, now],
    }).encode("utf-8"))
    store.write_bytes(radar._frame_file(now), RAIN)
    items, data_time = radar.RadarLayer().parse()
    assert [item["time"] for item in items] == [now]
    assert data_time == now


# ---- decoded frames ----------------------------------------------------------------

def test_only_the_last_decoded_frames_are_kept_in_memory(cache):
    epochs = [1_000_000 + step for step in range(5)]
    for epoch in epochs:
        store.write_bytes(radar._frame_file(epoch), RAIN)
    for epoch in epochs:
        assert radar.frame_arrays(radar._frame_file(epoch)) is not None
    assert len(radar._decoded) == radar.DECODED_FRAMES
    assert str(radar._frame_file(epochs[0])) not in radar._decoded


def test_an_unreadable_frame_is_not_drawn(cache):
    store.write_bytes(radar._frame_file(5), b"not a png")
    assert radar.frame_arrays(radar._frame_file(5)) is None


# ---- the slider -------------------------------------------------------------------

def _three_frames(now: int):
    """Two frames with no rain, then the sample frame (with rain) as the newest."""
    return [(now - 1200, BLANK), (now - 600, BLANK), (now - 300, RAIN)]


def test_the_slider_steps_through_the_frames_and_marks_the_newest(cache):
    now = int(time.time())
    layer = _publish(_three_frames(now))
    spec = layer.slider()
    assert (spec.steps, spec.index, spec.marker, spec.caption) == (3, 2, 2, "LIVE")
    assert spec.wide and not spec.disabled
    assert layer.build(SAO_PAULO_VIEW).bg_cells  # the newest frame is drawn


def test_moving_the_slider_shows_that_frame_and_its_age(cache):
    now = int(time.time())
    layer = _publish(_three_frames(now))
    layer.set_slider(0)
    spec = layer.slider()
    assert spec.index == 0
    assert spec.caption == "-15m"  # 15 minutes before the newest frame
    assert not layer.build(SAO_PAULO_VIEW).bg_cells  # the blank frame has no rain


def test_moving_the_slider_back_to_the_newest_follows_live_again(cache):
    now = int(time.time())
    layer = _publish(_three_frames(now))
    layer.set_slider(0)
    layer.set_slider(2)
    assert layer._pin is None
    assert layer.slider().caption == "LIVE"


def test_a_stale_loop_has_a_dimmed_slider_and_no_rain(cache):
    layer = _publish([(int(time.time()) - 2 * 3600, RAIN), (int(time.time()) - 2 * 3600 + 60, RAIN)])
    spec = layer.slider()
    assert spec.disabled
    assert not layer.build(SAO_PAULO_VIEW).bg_cells


def test_one_frame_gives_a_dimmed_slider(cache):
    layer = _publish([(int(time.time()) - 60, RAIN)])
    assert layer.slider().disabled


def test_no_frames_means_no_slider(cache):
    layer = _publish([])
    assert layer.slider() is None
    assert not layer.build(SAO_PAULO_VIEW).bg_cells


def test_a_disabled_layer_has_no_slider(cache):
    layer = _publish(_three_frames(int(time.time())))
    layer.enabled = False
    assert layer.slider() is None


# ---- the loop --------------------------------------------------------------------

def test_the_loop_is_off_by_default_and_the_frame_only_moves_with_the_slider(cache):
    now = int(time.time())
    layer = _publish(_three_frames(now))
    layer._advance(100.0)
    layer._advance(200.0)
    assert layer._shown_index() == 2


def test_the_loop_follows_the_core_frame_timing(cache, core_setting):
    core_setting(LOOP_KEY, True)
    layer = _publish(_three_frames(int(time.time())))
    layer._advance(100.0)   # first step: frame 0 -> 1
    assert layer._shown_index() == 1
    layer._advance(100.5)   # 0.5 s is less than frame_seconds (0.6)
    assert layer._shown_index() == 1
    layer._advance(100.7)   # frame 1 -> 2, the newest
    assert layer._shown_index() == 2
    layer._advance(102.4)   # 1.7 s on the newest is less than frame_hold_last_seconds (1.8)
    assert layer._shown_index() == 2
    layer._advance(102.6)   # back to the first frame
    assert layer._shown_index() == 0


def test_a_slider_position_holds_the_loop(cache, core_setting):
    core_setting(LOOP_KEY, True)
    layer = _publish(_three_frames(int(time.time())))
    layer.set_slider(0)
    layer._advance(100.0)
    layer._advance(200.0)
    assert layer._shown_index() == 0


def test_tick_drives_the_loop(cache, core_setting, monkeypatch):
    core_setting(LOOP_KEY, True)
    layer = _publish(_three_frames(int(time.time())))  # cache just written: tick() does not download
    calls = []
    monkeypatch.setattr(layer, "_advance", lambda now: calls.append(now))
    layer.tick()
    assert len(calls) == 1
