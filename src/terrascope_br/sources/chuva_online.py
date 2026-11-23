"""Chuva Online (IAG-USP) 36 km radar: cell colours over the bounding box the
site uses for its own map.

The site serves its last ten frames as ten PNG files, loop 1 (oldest) to loop 10
(newest). Two checks on 2026-11-14 showed the same frames two files further on, so
the ten files are a window that moves forward as new frames come. The source has
no archive: a frame can only be asked for through its file, and only while the
file still holds it. So the history is the frames this app has seen. Each frame
is kept under the Last-Modified of its file, and the frames of the last hour stay
in the cache.
The slider steps through them as the core's radar slider does, and the loop
follows the core's radar animation settings (layers.weather.radar.animate,
frame_seconds, frame_hold_last_seconds).

The legend line has the on/off switch (key T does the same). Off, the radar
draws nothing, fetches nothing and shows no slider. The choice is kept in
<Terrascope config folder>/br/radar.json between runs; a missing or unreadable file means on.

Each file is requested with If-Modified-Since, so an unchanged file costs one
304 answer. The time shown is the Last-Modified of the newest frame. If that
frame is older than 30 minutes, the radar is hidden and the legend says when the
site last updated, so old rain is never shown as current."""

from __future__ import annotations

import io
import json
import time
from collections import OrderedDict
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path

import numpy as np
from PIL import Image

from terrascope.core import config
from terrascope.core.layer import LayerRender, Marker, SliderSpec
from terrascope.core.view import View

from terrascope_br import store
from terrascope_br._i18n import _
from terrascope_br.base import SourceLayer, key
from terrascope_br.net import SourceError, get

# The core's slider has a play button from the version that added SliderSpec.playing.
_PLAY_BUTTON = "playing" in getattr(SliderSpec, "__dataclass_fields__", {})

FRAME_URL = "https://www.starnet.iag.usp.br/img_starnet/Radar_USP/pelletron_36km/loop/36km_loop_{index}.png"
SLOTS = 10  # the site's loop files, 1 to 10
# Frames seen within the last hour are kept for the slider, at most MAX_FRAMES.
HISTORY_SECONDS = 60 * 60
MAX_FRAMES = 30
MANIFEST_VERSION = 2
# Decoded frames held in memory: the one shown and its neighbours in the loop.
DECODED_FRAMES = 3
# Bounding box of the image (lon/lat), as in the site's chuvaonline_animacao_36km.js.
BBOX = (-47.134155, -23.930042, -46.337808, -23.192604)  # min lon, min lat, max lon, max lat
# Measured on a frame of 2026-11-07: the 36 km disc is centred at (500, 500) with
# radius 436 px in the 1000 x 1000 image. The colour bar and the date label sit outside it.
DISC_CENTER = 500.0
DISC_RADIUS = 436.0
# Precipitation colours are saturated; grey and black are borders, text and the bar.
MIN_SATURATION = 60
STALE_SECONDS = 30 * 60
SWITCH_ACTION = "br_radar.toggle"
DBZ_KEY = (10, 20, 30, 40, 50)   # the swatches of the legend key (the last one stands for 50 dBZ and more)
SHOWN_FILE = "radar.json"  # the on/off choice, in the config folder: {"shown": true|false}
# The colour bar of the frames ("Z(dBZ)", right of the disc): 50 bands of 1 dBZ, from 10-11
# (dark blue) up to 59-60 (pink), read from a frame of 2026-11-22 (column 995, rows 200-690).
# Every precipitation colour inside the disc is one of them, so a pixel's colour gives its dBZ.
PALETTE = (
    (1, 11, 129), (1, 21, 143), (1, 30, 157), (1, 40, 171), (0, 55, 255),
    (0, 59, 199), (0, 69, 213), (0, 78, 227), (0, 88, 241), (0, 149, 255),
    (0, 81, 0), (0, 93, 0), (0, 105, 0), (0, 118, 0), (0, 95, 0),
    (0, 143, 0), (0, 155, 0), (0, 168, 0), (0, 180, 0), (34, 160, 34),
    (0, 205, 0), (0, 217, 0), (0, 230, 0), (0, 242, 0), (35, 255, 0),
    (255, 127, 40), (255, 140, 42), (255, 156, 43), (255, 171, 44), (182, 97, 0),
    (255, 206, 47), (255, 212, 35), (255, 218, 23), (255, 224, 11), (255, 144, 0),
    (142, 0, 0), (154, 0, 0), (167, 0, 0), (179, 0, 0), (192, 0, 0),
    (204, 0, 0), (217, 0, 0), (229, 0, 0), (242, 0, 0), (255, 0, 0),
    (255, 0, 139), (255, 28, 151), (255, 56, 164), (255, 85, 177), (255, 113, 190),
)
PALETTE_HEX = tuple(f"#{r:02x}{g:02x}{b:02x}" for r, g, b in PALETTE)
_PALETTE_CODES = np.array([(r << 16) | (g << 8) | b for r, g, b in PALETTE], dtype=np.int64)
_PALETTE_ORDER = np.argsort(_PALETTE_CODES)
# Each half terminal cell is read from SUPERSAMPLE x SUPERSAMPLE points of the frame, as the
# core's weather radar does: it is drawn when at least MIN_COVERAGE of them have rain, in the
# colour of their mean level. Half blocks give two readings per cell, top and bottom.
SUPERSAMPLE = 3
MIN_COVERAGE = 0.5
HALF_BLOCK_UPPER = "\u2580"   # ▀
HALF_BLOCK_LOWER = "\u2584"   # ▄

_decoded: OrderedDict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = OrderedDict()

def decode_frame(png: bytes) -> tuple[np.ndarray, np.ndarray]:
    """(rgb, mask) of a frame. mask is True where precipitation is drawn."""
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    rgba = np.asarray(image, dtype=np.int16)
    rgb = rgba[:, :, :3].astype(np.uint8)
    spread = rgba[:, :, :3].max(axis=2) - rgba[:, :, :3].min(axis=2)
    yy, xx = np.mgrid[0:rgba.shape[0], 0:rgba.shape[1]]
    inside = (xx - DISC_CENTER) ** 2 + (yy - DISC_CENTER) ** 2 <= DISC_RADIUS ** 2
    mask = (rgba[:, :, 3] > 0) & (spread >= MIN_SATURATION) & inside
    return rgb, mask

def frame_levels(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Band of every pixel (uint8): 0 where no rain is drawn, else 1 (10-11 dBZ) to 50
    (59-60 dBZ), by its colour in PALETTE. A drawn colour that is not in it counts as none."""
    codes = (rgb[:, :, 0].astype(np.int64) << 16) | (rgb[:, :, 1].astype(np.int64) << 8) | rgb[:, :, 2]
    sorted_codes = _PALETTE_CODES[_PALETTE_ORDER]
    index = np.clip(np.searchsorted(sorted_codes, codes), 0, len(sorted_codes) - 1)
    known = (sorted_codes[index] == codes) & mask
    return np.where(known, _PALETTE_ORDER[index] + 1, 0).astype(np.uint8)


def half_cell_levels(levels: np.ndarray, view: View, supersample: int = SUPERSAMPLE) -> np.ndarray:
    """Band (0 = none) of every HALF terminal cell of the view, shape (2 * height, width):
    row 2*y is the top half of row y, 2*y + 1 its bottom half. Each is read from
    supersample x supersample points laid on the view's own grid; it has rain when at
    least MIN_COVERAGE of the points that fall on the frame have rain, and then the
    rounded mean band of those points."""
    min_lon, min_lat, max_lon, max_lat = BBOX
    height, width = levels.shape
    columns = view.width * supersample
    rows = view.height * 2 * supersample
    lons = view.origin_lon + (np.arange(columns) + 0.5) / columns * view.lon_span
    lons = (lons + 180.0) % 360.0 - 180.0   # a view across the antimeridian (see cell_colours)
    lats = view.origin_lat - (np.arange(rows) + 0.5) / rows * view.lat_span
    px = np.floor((lons - min_lon) / (max_lon - min_lon) * width).astype(np.int64)
    py = np.floor((max_lat - lats) / (max_lat - min_lat) * height).astype(np.int64)
    inside_x, inside_y = (px >= 0) & (px < width), (py >= 0) & (py < height)
    if not inside_x.any() or not inside_y.any():
        return np.zeros((view.height * 2, view.width), dtype=np.int64)
    samples = levels[np.clip(py, 0, height - 1)[:, None], np.clip(px, 0, width - 1)[None, :]].astype(np.float32)
    samples[~inside_y, :] = 0
    samples[:, ~inside_x] = 0
    shape = (view.height * 2, supersample, view.width, supersample)
    rain = (samples > 0).reshape(shape).sum(axis=(1, 3))
    total = samples.reshape(shape).sum(axis=(1, 3))
    count = supersample * supersample
    mean = np.divide(total, rain, out=np.zeros_like(total), where=rain > 0)
    return np.where(rain >= MIN_COVERAGE * count, np.rint(mean), 0).astype(np.int64)


def radar_render(levels: np.ndarray, view: View) -> LayerRender:
    """The frame on the view: a cell whose two halves have the same band is a background
    colour (outlines and names stay drawn over it); a cell whose halves differ is a half
    block, top band as the glyph's colour and bottom band as its background (or the map's,
    when that half is dry)."""
    halves = half_cell_levels(levels, view)
    top, bottom = halves[0::2], halves[1::2]
    bg_cells: dict[tuple[int, int], str] = {}
    markers: list[Marker] = []
    for y, x in zip(*np.nonzero((top > 0) | (bottom > 0))):
        high, low = int(top[y, x]), int(bottom[y, x])
        x, y = int(x), int(y)
        if high == low:
            bg_cells[(x, y)] = PALETTE_HEX[high - 1]
        elif high and low:
            markers.append(Marker(x=x, y=y, color=PALETTE_HEX[high - 1], bg_color=PALETTE_HEX[low - 1],
                                  glyph=HALF_BLOCK_UPPER))
        elif high:
            markers.append(Marker(x=x, y=y, color=PALETTE_HEX[high - 1], glyph=HALF_BLOCK_UPPER))
        else:
            markers.append(Marker(x=x, y=y, color=PALETTE_HEX[low - 1], glyph=HALF_BLOCK_LOWER))
    return LayerRender(None, "#000000", markers=markers, bg_cells=bg_cells)


def frame_arrays(file: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """(rgb, mask, levels) of a stored frame (see frame_levels), or None when it cannot be
    read. Decoding takes a few tens of milliseconds, so the last DECODED_FRAMES are kept."""
    name = str(file)
    if name in _decoded:
        _decoded.move_to_end(name)
        return _decoded[name]
    data = store.read_bytes(file)
    if data is None:
        return None
    try:
        rgb, mask = decode_frame(data)
    except (OSError, ValueError):  # not an image, or cut short
        return None
    arrays = (rgb, mask, frame_levels(rgb, mask))
    _decoded[name] = arrays
    while len(_decoded) > DECODED_FRAMES:
        _decoded.popitem(last=False)
    return arrays

def cell_colours(rgb: np.ndarray, mask: np.ndarray, view: View) -> dict[tuple[int, int], str]:
    """Colour of every terminal cell of `view` whose centre falls on a drawn
    pixel of the frame. The frame is sampled at each cell centre."""
    min_lon, min_lat, max_lon, max_lat = BBOX
    height, width = mask.shape
    cols = np.arange(view.width) + 0.5
    rows = np.arange(view.height) + 0.5
    lons = view.origin_lon + cols * view.lon_span / view.width
    # The view can cross the antimeridian: bring each cell centre back to [-180, 180),
    # so the box of the radar is found wherever the map is wrapped (see WRAP_SHIFTS in core).
    lons = (lons + 180.0) % 360.0 - 180.0
    lats = view.origin_lat - rows * view.lat_span / view.height
    px = np.floor((lons - min_lon) / (max_lon - min_lon) * width).astype(int)
    py = np.floor((max_lat - lats) / (max_lat - min_lat) * height).astype(int)
    valid_x = (px >= 0) & (px < width)
    valid_y = (py >= 0) & (py < height)
    if not valid_x.any() or not valid_y.any():
        return {}
    grid_y, grid_x = np.meshgrid(np.nonzero(valid_y)[0], np.nonzero(valid_x)[0], indexing="ij")
    sample_y = py[grid_y]
    sample_x = px[grid_x]
    hit = mask[sample_y, sample_x]
    cells: dict[tuple[int, int], str] = {}
    for y, x in zip(grid_y[hit].tolist(), grid_x[hit].tolist()):
        r, g, b = rgb[py[y], px[x]]
        cells[(x, y)] = f"#{int(r):02x}{int(g):02x}{int(b):02x}"
    return cells

def _http_date(epoch: int) -> str:
    return formatdate(epoch, usegmt=True)

def _saved_shown() -> bool:
    """The on/off choice of the last run (on when there is none, or it cannot be read)."""
    try:
        saved = json.loads((store.read_bytes(store.settings_path(SHOWN_FILE)) or b"{}").decode("utf-8"))
    except (ValueError, OSError):
        return True
    shown = saved.get("shown") if isinstance(saved, dict) else None
    return shown if isinstance(shown, bool) else True

def _save_shown(shown: bool) -> None:
    try:
        store.write_bytes(store.settings_path(SHOWN_FILE), json.dumps({"shown": shown}).encode("utf-8"))
    except OSError:
        pass  # the choice then lasts for this run only

def _frame_file(epoch: int) -> Path:
    return store.path("chuva_online", f"frame_{epoch}.png")

def _loop_settings() -> tuple[bool, float, float]:
    """(loop on, seconds per frame, seconds on the newest frame): the core's radar
    animation settings, read at each use so a command-line flag applies."""
    radar = "layers.weather.radar."
    return (
        bool(config.get_path(radar + "animate", False)),
        float(config.get_path(radar + "frame_seconds", 0.6)),
        float(config.get_path(radar + "frame_hold_last_seconds", 1.8)),
    )

class RadarLayer(SourceLayer):
    name = "br_radar"
    toggle_key = "t"
    switch_action = SWITCH_ACTION
    source_name = _("Chuva Online radar, 36 km")
    info_title = _("RADAR")
    credit = "IAG-USP"
    refresh_seconds = 600
    stale_after_seconds = STALE_SECONDS
    fetch_timeout = 30.0

    def __init__(self) -> None:
        super().__init__()
        self._index = 0                 # frame the loop is on
        self._last_step = 0.0
        self._pin: float | None = None  # frame time the slider holds (None = follow the loop or the newest)
        self._playing: bool | None = None  # play button; None = the core's radar animate setting
        self.shown = _saved_shown()        # the legend switch (SourceLayer.shown), as last left

    def stamp_file(self):
        return store.path("chuva_online", "frames.json")

    def is_due(self) -> bool:
        return self.shown and super().is_due()

    def _flip(self) -> None:
        super()._flip()
        _save_shown(self.shown)

    def _manifest(self) -> tuple[dict[int, int], list[int]]:
        """(slot -> Last-Modified of the file last seen there, frame times kept,
        oldest first). Empty when the file is missing, of another version or damaged."""
        data = store.read_bytes(self.stamp_file())
        if not data:
            return {}, []
        try:
            manifest = json.loads(data.decode("utf-8"))
            if not isinstance(manifest, dict) or manifest.get("version") != MANIFEST_VERSION:
                return {}, []
            slots = {int(slot): int(epoch) for slot, epoch in manifest.get("slots", {}).items()}
            frames = sorted({int(epoch) for epoch in manifest.get("frames", [])})
        except (ValueError, TypeError, AttributeError):
            return {}, []
        return slots, frames

    def fetch(self) -> None:
        slots, known_frames = self._manifest()
        frames = set(known_frames)
        now = int(time.time())
        failures = []
        downloaded = 0
        unchanged = 0
        for slot in range(1, SLOTS + 1):
            known = slots.get(slot)
            # Ask only when the frame the answer would be compared with is still on disk.
            conditional = known is not None and _frame_file(known).exists()
            try:
                status, body, headers = get(
                    FRAME_URL.format(index=slot), self.fetch_timeout, min_gap=1.0,
                    if_modified_since=_http_date(known) if conditional else None,
                )
            except SourceError as error:
                failures.append(f"{slot}: {error}")
                continue
            if status == 304:
                unchanged += 1  # the frame we have for this slot is still the site's
                continue
            last_modified = headers.get("Last-Modified") or headers.get("last-modified")
            try:
                epoch = int(parsedate_to_datetime(last_modified).timestamp()) if last_modified else now
                Image.open(io.BytesIO(body)).verify()
            except Exception as error:  # bad header or not an image
                failures.append(f"{slot}: {error}")
                continue
            store.write_bytes(_frame_file(epoch), body)
            slots[slot] = epoch
            frames.add(epoch)
            downloaded += 1
        # Frames that came back unchanged count as success: only a fetch that got no
        # usable frame at all, new or current, is a failure, and keeps the cache as it was.
        if downloaded == 0 and unchanged == 0:
            raise SourceError("; ".join(failures) or "no frame received")
        ordered = sorted(frames)
        cutoff = now - HISTORY_SECONDS
        # The newest frame is kept even when it is older than the window, so the legend can say when it was.
        kept = [epoch for epoch in ordered if epoch >= cutoff][-MAX_FRAMES:] or ordered[-1:]
        keep_names = {_frame_file(epoch).name for epoch in kept}
        for file in store.path("chuva_online").glob("*.png"):
            if file.name not in keep_names:  # frames out of the window, and the files of older versions
                try:
                    file.unlink()
                except OSError:
                    pass
        store.write_bytes(self.stamp_file(), json.dumps({
            "version": MANIFEST_VERSION,
            "fetched": time.time(),
            "slots": {str(slot): epoch for slot, epoch in slots.items()},
            "frames": kept,
        }).encode("utf-8"))

    def parse(self):
        _slots, frames = self._manifest()
        items = [{"time": epoch, "file": _frame_file(epoch)} for epoch in frames]
        items = [item for item in items if item["file"].exists()]
        if not items:
            return [], None
        return items, items[-1]["time"]

    # -- the loop and the slider -------------------------------------------------------

    def _shown_index(self) -> int:
        """Index in items of the frame drawn now: the frame the slider holds, else the
        loop's frame while the loop is on, else the newest. Needs items."""
        if self._pin is not None:
            return min(range(len(self.items)), key=lambda i: abs(self.items[i]["time"] - self._pin))
        if self._is_playing():
            return min(self._index, len(self.items) - 1)
        return len(self.items) - 1

    def _is_playing(self) -> bool:
        """The frames loop: after the play button, or by the core's radar animate setting
        until the button is first pressed."""
        return _loop_settings()[0] if self._playing is None else self._playing

    def _advance(self, now: float) -> None:
        _loop, frame_seconds, hold_seconds = _loop_settings()
        if not (self.enabled and self.shown and self._is_playing()) or self._pin is not None or len(self.items) < 2:
            return  # static: the frame only moves with the slider
        newest = self._index >= len(self.items) - 1
        if now - self._last_step >= (hold_seconds if newest else frame_seconds):
            self._index = 0 if newest else self._index + 1
            self._last_step = now
            self.dirty = True

    def tick(self) -> None:
        super().tick()
        self._advance(time.time())

    def slider(self) -> SliderSpec | None:
        if not self.enabled or not self.shown or not self.items or self.data_time is None:
            return None
        newest = len(self.items) - 1
        if len(self.items) < 2 or self.is_stale():
            # Nothing to step through, or the frames are too old to show as current.
            return SliderSpec(_("RADAR"), 2, 0, disabled=True)
        index = self._shown_index()
        minutes = round((self.items[index]["time"] - self.items[newest]["time"]) / 60)
        caption = _("LIVE") if index == newest else f"{minutes:+d}m"
        if _PLAY_BUTTON:
            return SliderSpec(_("RADAR"), len(self.items), index, caption, marker=newest, wide=True,
                              playing=self._is_playing() and self._pin is None)
        return SliderSpec(_("RADAR"), len(self.items), index, caption, marker=newest, wide=True)

    def toggle_slider_play(self) -> None:
        """Play: the loop goes on from the frame shown (from the oldest when LIVE is shown).
        Pause: that frame is held (the newest one is held as LIVE, so new frames still show)."""
        if not self.items:
            return
        if self._is_playing() and self._pin is None:
            shown = self._shown_index()
            newest = len(self.items) - 1
            self._pin = None if shown == newest else self.items[shown]["time"]
            self._playing = False
        else:
            shown = self._shown_index()
            self._index = 0 if shown == len(self.items) - 1 else shown
            self._pin = None
            self._playing = True
            self._last_step = time.time()
        self.dirty = True

    def set_slider(self, index: int) -> None:
        if not self.items:
            return
        index = max(0, min(len(self.items) - 1, index))
        self._index = index
        newest = len(self.items) - 1
        # Pinned by frame time, so the held frame stays put when newer frames arrive.
        self._pin = None if index == newest else self.items[index]["time"]
        self.dirty = True

    # -- drawing and legend --------------------------------------------------------------

    def hit_test_all(self, x: int, y: int, radius: float | None = None):
        return []   # the half-block cells are an overlay, not items to select

    def build(self, view: View) -> LayerRender:
        if not self.items or self.data_time is None:
            return LayerRender(None, "#000000")
        if time.time() - self.data_time > STALE_SECONDS:
            return LayerRender(None, "#000000")  # old rain is not shown as current
        arrays = frame_arrays(self.items[self._shown_index()]["file"])
        if arrays is None:
            return LayerRender(None, "#000000")
        return radar_render(arrays[2], view)

    def legend_key(self):
        # The key is the frame's own colour bar in dBZ: band k (k-th colour of PALETTE) is 10 + k - 1 dBZ,
        # so the swatch of 10 dBZ is PALETTE_HEX[0] and that of 50 dBZ PALETTE_HEX[40].
        return key(*((PALETTE_HEX[dbz - 10], f"{dbz}+ dBZ" if dbz == DBZ_KEY[-1] else str(dbz)) for dbz in DBZ_KEY))
