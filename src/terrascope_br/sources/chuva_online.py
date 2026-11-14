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
from terrascope.core.layer import LayerRender, SliderSpec
from terrascope.core.view import View

from terrascope_br import store
from terrascope_br._i18n import _
from terrascope_br.base import SourceLayer, key
from terrascope_br.net import SourceError, get

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

_decoded: OrderedDict[str, tuple[np.ndarray, np.ndarray]] = OrderedDict()

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

def frame_arrays(file: Path) -> tuple[np.ndarray, np.ndarray] | None:
    """(rgb, mask) of a stored frame, or None when it cannot be read. Decoding takes
    a few tens of milliseconds, so the last DECODED_FRAMES are kept."""
    name = str(file)
    if name in _decoded:
        _decoded.move_to_end(name)
        return _decoded[name]
    data = store.read_bytes(file)
    if data is None:
        return None
    try:
        arrays = decode_frame(data)
    except (OSError, ValueError):  # not an image, or cut short
        return None
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

    def stamp_file(self):
        return store.path("chuva_online", "frames.json")

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
        if _loop_settings()[0]:
            return min(self._index, len(self.items) - 1)
        return len(self.items) - 1

    def _advance(self, now: float) -> None:
        loop, frame_seconds, hold_seconds = _loop_settings()
        if not (self.enabled and loop) or self._pin is not None or len(self.items) < 2:
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
        if not self.enabled or not self.items or self.data_time is None:
            return None
        newest = len(self.items) - 1
        if len(self.items) < 2 or self.is_stale():
            # Nothing to step through, or the frames are too old to show as current.
            return SliderSpec(_("RADAR"), 2, 0, disabled=True)
        index = self._shown_index()
        minutes = round((self.items[index]["time"] - self.items[newest]["time"]) / 60)
        caption = _("LIVE") if index == newest else f"{minutes:+d}m"
        return SliderSpec(_("RADAR"), len(self.items), index, caption, marker=newest, wide=True)

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

    def build(self, view: View) -> LayerRender:
        if not self.items or self.data_time is None:
            return LayerRender(None, "#000000")
        if time.time() - self.data_time > STALE_SECONDS:
            return LayerRender(None, "#000000")  # old rain is not shown as current
        arrays = frame_arrays(self.items[self._shown_index()]["file"])
        if arrays is None:
            return LayerRender(None, "#000000")
        rgb, mask = arrays
        return LayerRender(None, "#000000", bg_cells=cell_colours(rgb, mask, view))

    def legend_key(self):
        return key(
            ("#00a800", _("weak")),
            ("#ffe00b", _("moderate")),
            ("#e50000", _("strong")),
        )
