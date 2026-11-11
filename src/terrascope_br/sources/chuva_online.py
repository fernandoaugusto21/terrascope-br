"""Chuva Online (IAG-USP) 36 km radar: the ten loop frames as PNG, drawn as
cell colours over the bounding box the site uses for its own map.

Each frame is requested with If-Modified-Since, so an unchanged frame costs
one 304 answer. The time shown is the Last-Modified of the newest frame. If
that frame is older than 30 minutes, the radar is hidden and the legend says
when the site last updated, so old rain is never shown as current."""

from __future__ import annotations

import io
import json
import time
from datetime import datetime, timezone
from email.utils import formatdate, parsedate_to_datetime

import numpy as np
from PIL import Image

from terrascope.core.layer import LayerRender
from terrascope.core.view import View

from terrascope_br import store
from terrascope_br._i18n import _
from terrascope_br.base import SourceLayer, key
from terrascope_br.net import SourceError, get

FRAME_URL = "https://www.starnet.iag.usp.br/img_starnet/Radar_USP/pelletron_36km/loop/36km_loop_{index}.png"
FRAMES = 10
# Bounding box of the image (lon/lat), as in the site's chuvaonline_animacao_36km.js.
BBOX = (-47.134155, -23.930042, -46.337808, -23.192604)  # min lon, min lat, max lon, max lat
# Measured on a frame of 2026-11-07: the 36 km disc is centred at (500, 500) with
# radius 436 px in the 1000 x 1000 image. The colour bar and the date label sit outside it.
DISC_CENTER = 500.0
DISC_RADIUS = 436.0
# Precipitation colours are saturated; grey and black are borders, text and the bar.
MIN_SATURATION = 60
STALE_SECONDS = 30 * 60

_decoded: dict[str, tuple[float, np.ndarray, np.ndarray]] = {}

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

def _http_date(epoch: float) -> str:
    return formatdate(epoch, usegmt=True)

class RadarLayer(SourceLayer):
    name = "br_radar"
    toggle_key = "t"
    source_name = _("Chuva Online radar, 36 km")
    info_title = _("RADAR")
    credit = "IAG-USP"
    refresh_seconds = 600
    stale_after_seconds = STALE_SECONDS
    fetch_timeout = 30.0

    def stamp_file(self):
        return store.path("chuva_online", "frames.json")

    def _manifest(self) -> dict:
        data = store.read_bytes(self.stamp_file())
        if not data:
            return {}
        try:
            manifest = json.loads(data.decode("utf-8"))
        except ValueError:
            return {}
        return manifest if isinstance(manifest, dict) else {}

    def fetch(self) -> None:
        manifest = self._manifest()
        modified = dict(manifest.get("modified", {}))
        failures = []
        downloaded = 0
        unchanged = 0
        for index in range(1, FRAMES + 1):
            file = store.path("chuva_online", f"loop_{index}.png")
            headers_epoch = modified.get(str(index))
            try:
                status, body, headers = get(
                    FRAME_URL.format(index=index), 30.0, min_gap=1.0,
                    if_modified_since=_http_date(headers_epoch) if (headers_epoch and file.exists()) else None,
                )
            except SourceError as error:
                failures.append(f"{index}: {error}")
                continue
            if status == 304:
                unchanged += 1  # the copy we have is still the site's current frame
                continue
            last_modified = headers.get("Last-Modified") or headers.get("last-modified")
            try:
                epoch = parsedate_to_datetime(last_modified).timestamp() if last_modified else time.time()
                Image.open(io.BytesIO(body)).verify()
            except Exception as error:  # bad header or not an image
                failures.append(f"{index}: {error}")
                continue
            store.write_bytes(file, body)
            modified[str(index)] = epoch
            downloaded += 1
        if not modified:
            raise SourceError("; ".join(failures) or "no frame received")
        store.write_bytes(self.stamp_file(), json.dumps({
            "fetched": time.time(), "modified": modified,
        }).encode("utf-8"))
        # Frames that came back unchanged count as success: only a fetch that got no
        # usable frame at all, new or current, is a failure.
        if failures and downloaded == 0 and unchanged == 0:
            raise SourceError("; ".join(failures))

    def parse(self):
        manifest = self._manifest()
        modified = {int(key): float(value) for key, value in manifest.get("modified", {}).items()}
        if not modified:
            return [], None
        newest = max(modified, key=modified.get)
        file = store.path("chuva_online", f"loop_{newest}.png")
        data = store.read_bytes(file)
        if data is None:
            return [], None
        key = str(file)
        stamp = file.stat().st_mtime
        if key not in _decoded or _decoded[key][0] != stamp:
            rgb, mask = decode_frame(data)
            _decoded.clear()
            _decoded[key] = (stamp, rgb, mask)
        _, rgb, mask = _decoded[key]
        return [{"rgb": rgb, "mask": mask}], modified[newest]

    def build(self, view: View) -> LayerRender:
        if not self.items or self.data_time is None:
            return LayerRender(None, "#000000")
        if time.time() - self.data_time > STALE_SECONDS:
            return LayerRender(None, "#000000")  # old rain is not shown as current
        frame = self.items[0]
        return LayerRender(None, "#000000", bg_cells=cell_colours(frame["rgb"], frame["mask"], view))

    def legend_key(self):
        return key(
            ("#00a800", _("weak")),
            ("#ffe00b", _("moderate")),
            ("#e50000", _("strong")),
        )
