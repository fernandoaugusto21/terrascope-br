"""Road network of the Região Metropolitana de São Paulo (br_viario), from OpenStreetMap.

The file is built ahead of time by packaging/roads/build_roads.py and published as an
asset of a GitHub release (format: terrascope_br.roads_format). The layer downloads it
the first time it is switched on, from the newest release that has it, keeps it in the
cache for a week before looking for a newer one, and works from the cache offline.

What is drawn, by zoom (roads_format.CLASSES): motorways and trunk roads from zoom 16,
primary and secondary from 64, tertiary from 256, local streets (residential,
unclassified, living street) from 512. Only the roads inside the view are drawn: each
class is rasterised on the braille grid of the view, and a cell that holds roads of two
classes shows the first one (the higher class, the order of CLASSES). Each class has its
own colour: with a core that has LayerRender.line_layers, one set of braille lines per class,
drawn in this layer's turn, so the IBGE outlines (after it in the tab) cover them. With an
older core, braille markers (drawn after every layer's lines, so above the outlines).

The layer is off when the tab opens (its own switch in the legend, the "on | off" line).
It draws nothing, and downloads nothing, while it is off.
"""

from __future__ import annotations

import json

import numpy as np

from terrascope.core.layer import LayerRender, Marker, SidebarLine
from terrascope.core.view import BRAILLE_BIT_WEIGHTS, View

from terrascope_br import roads_format, store
from terrascope_br._i18n import N_, _
from terrascope_br.base import SourceLayer, key
from terrascope_br.geo import view_key
from terrascope_br.net import SourceError, get
from terrascope_br.roads_format import RoadFile

# The core draws several line colours per layer from the version that added LayerRender.line_layers.
_LINE_LAYERS = "line_layers" in getattr(LayerRender, "__dataclass_fields__", {})

REPO = "fernandoaugusto21/terrascope-br"
# The Windows build keeps ten releases of its own and publishes more often than the weekly
# road file, so ten per page could leave the newest roads-* release off the first page.
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=100"
DOWNLOAD_TIMEOUT = 120.0
CHECK_SECONDS = 7 * 24 * 3600        # a newer road file is looked for after a week
STALE_SECONDS = 45 * 24 * 3600       # the legend says "no update since" past this age of the data
FILE_NAME = "br-viario.npz"

# One colour per class (the order of roads_format.CLASSES).
COLOURS = {
    "motorway": "#f2c14e",
    "trunk": "#f08a4b",
    "primary": "#e0607e",
    "secondary": "#b57edc",
    "tertiary": "#7fb8e6",
    "residential": "#9aa5b1",
}
LEGEND_ROW = 2                         # classes per legend row (three rows: see legend_lines)
# Legend labels, marked for translation (read with _() when the legend is built).
LABELS = {
    "motorway": N_("motorway"),
    "trunk": N_("trunk road"),
    "primary": N_("primary"),
    "secondary": N_("secondary"),
    "tertiary": N_("tertiary"),
    "residential": N_("local streets"),
}


def find_asset_url(releases: list) -> str:
    """Download address of the road file in the newest release that has it. `releases` is
    the GitHub API list, newest first; drafts are skipped. Raises SourceError when none has it."""
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        for asset in release.get("assets") or []:
            if isinstance(asset, dict) and asset.get("name") == roads_format.ASSET_NAME:
                url = asset.get("browser_download_url")
                if url:
                    return str(url)
    raise SourceError("no release has the road file yet")


class RoadsLayer(SourceLayer):
    name = "br_viario"
    toggle_key = ""                              # the legend line switches it (like br_inundation_cge)
    switch_action = "br_viario.toggle"
    source_name = _("Roads (OSM)")
    info_title = _("ROAD")
    credit = "OpenStreetMap"
    refresh_seconds = CHECK_SECONDS
    stale_after_seconds = STALE_SECONDS

    def __init__(self) -> None:
        super().__init__()
        self.shown = False                       # off when the tab opens; the legend switch turns it on
        self._road: RoadFile | None = None
        self._prepared: dict[tuple[str, int], tuple] = {}
        self._frame_key = None
        self._render = LayerRender(None, COLOURS["residential"])

    # -- download and cache -------------------------------------------------------------
    def stamp_file(self):
        return store.path("viario", FILE_NAME)

    def is_due(self) -> bool:
        return self.shown and super().is_due()

    def fetch(self) -> None:
        file = self.stamp_file()
        age = store.age(file)
        if age is not None and age < CHECK_SECONDS:
            return
        _status, body, _headers = get(_latest_asset(), DOWNLOAD_TIMEOUT, min_gap=1.0)
        try:
            roads_format.check(body)
        except (ValueError, OSError, KeyError) as error:
            raise SourceError(f"road file unreadable: {error}") from error
        store.write_bytes(file, body)

    def parse(self):
        # The file is read into memory, not opened: a download replaces it on disk, which
        # Windows does not allow while a handle is open.
        data = store.read_bytes(self.stamp_file())
        if data is None:
            raise OSError("no road file in the cache")
        road = RoadFile(data)
        built = road.meta.get("built_epoch")
        if not isinstance(built, (int, float)):
            raise ValueError("the road file has no build time")
        return road, float(built)

    def reload(self) -> None:
        super().reload()
        self._road = self.items if isinstance(self.items, RoadFile) else None
        self.items = []                          # the arrays live in self._road, read as drawn
        self._prepared = {}
        self._frame_key = None
        self._render = LayerRender(None, COLOURS["residential"])

    def clear(self) -> None:
        super().clear()
        self._road = None
        self._prepared = {}
        self._frame_key = None
        self._render = LayerRender(None, COLOURS["residential"])

    # -- drawing ------------------------------------------------------------------------
    def _prepare(self, name: str, index: int):
        """(degrees, start, end, bounding box) of the polylines of one class and range, read
        once per file: start and end index the vertices of each polyline."""
        key = (name, index)
        if key not in self._prepared:
            lines = self._road.polylines(name, index)
            degrees = lines.degrees() if lines.count else np.zeros((0, 2))
            starts = lines.offsets[:-1].astype(np.int64)
            ends = lines.offsets[1:].astype(np.int64)
            if lines.count:
                west = np.minimum.reduceat(degrees[:, 0], starts)
                east = np.maximum.reduceat(degrees[:, 0], starts)
                south = np.minimum.reduceat(degrees[:, 1], starts)
                north = np.maximum.reduceat(degrees[:, 1], starts)
            else:
                west = east = south = north = np.zeros(0)
            # The polyline of each vertex, and the segments (a vertex and the next one of the
            # same polyline), so a view's segments are picked without a loop over polylines.
            line_of = np.repeat(np.arange(len(starts)), ends - starts)
            segments = np.flatnonzero(line_of[:-1] == line_of[1:])
            self._prepared[key] = (degrees, starts, ends, (west, south, east, north), line_of, segments)
        return self._prepared[key]

    def _rasterize(self, view: View, name: str, index: int) -> np.ndarray:
        """Braille values (height, width) of one class: the dots of its lines in view.

        Every segment in view is sampled at least once per pixel along its longer axis, all
        segments of the class at once (numpy), and the pixels hit are lit. One
        ImageDraw.line per polyline gave the same cells (98 to 100 % of them over São Paulo,
        the rest one dot apart) but cost 30 to 100 ms per class at zoom 64, mostly in the
        Python loop over tens of thousands of short polylines."""
        degrees, starts, ends, (west, south, east, north), line_of, segments = self._prepare(name, index)
        pixels = np.zeros((view.pixel_height, view.pixel_width), dtype=bool)
        if len(segments):
            lon0, lat0 = view.origin_lon, view.origin_lat
            lon1, lat1 = lon0 + view.lon_span, lat0 - view.lat_span
            chosen = (east >= lon0) & (west <= lon1) & (north >= lat1) & (south <= lat0)
            seg = segments[chosen[line_of[segments]]]
            xa = (degrees[seg, 0] - lon0) * view.scale_x
            ya = (lat0 - degrees[seg, 1]) * view.scale_y
            dx = (degrees[seg + 1, 0] - lon0) * view.scale_x - xa
            dy = (lat0 - degrees[seg + 1, 1]) * view.scale_y - ya
            samples = np.ceil(np.maximum(np.abs(dx), np.abs(dy))).astype(np.int64) + 1
            owner = np.repeat(np.arange(len(seg)), samples)
            starts = np.repeat(np.cumsum(samples) - samples, samples)
            step = (np.arange(samples.sum()) - starts) / np.maximum(samples - 1, 1)[owner]
            xs = np.floor(xa[owner] + dx[owner] * step).astype(np.int64)
            ys = np.floor(ya[owner] + dy[owner] * step).astype(np.int64)
            inside = (xs >= 0) & (xs < view.pixel_width) & (ys >= 0) & (ys < view.pixel_height)
            pixels[ys[inside], xs[inside]] = True
        dots = pixels.reshape(view.height, 4, view.width, 2)
        return (dots * BRAILLE_BIT_WEIGHTS[np.newaxis, :, np.newaxis, :]).sum(axis=(1, 3))

    def _cells_for(self, view: View):
        """(class names, braille value of each cell, rank of the class that owns it, -1 for
        none) of the view. A cell crossed by several classes keeps the top class's dots."""
        zoom = min(max(view.zoom, roads_format.LOD_ZOOMS[0]), roads_format.LOD_ZOOMS[-1])
        names = roads_format.classes_at(view.zoom)
        index = roads_format.range_index(zoom)
        chosen = np.zeros((view.height, view.width), dtype=np.int64)
        owner = np.full((view.height, view.width), -1, dtype=np.int64)
        if self._road is None or not names or index is None:
            return [], chosen, owner
        for rank, name in enumerate(names):   # top class first
            stored = self._road.stored_range(name, index)   # an older file: its finest range below
            if stored is None:
                continue
            values = self._rasterize(view, name, stored)
            free = (values > 0) & (owner < 0)
            chosen[free] = values[free]
            owner[free] = rank
        return names, chosen, owner

    def _render_for(self, view: View) -> LayerRender:
        names, chosen, owner = self._cells_for(view)
        if _LINE_LAYERS:
            # One set of braille lines per class, each in its colour, drawn in this layer's
            # turn: the IBGE outlines (listed after this layer in the tab) cover them.
            layers = []
            for rank, name in enumerate(names):
                cells = np.where(owner == rank, 0x2800 + chosen, 0x2800)
                if (owner == rank).any():
                    layers.append((["".join(map(chr, row)) for row in cells.tolist()], COLOURS[name]))
            return LayerRender(None, COLOURS["residential"], line_layers=layers)
        # An older core draws a layer's lines in one colour, and markers after every layer's
        # lines: one marker per cell, so the roads sit above the IBGE outlines there.
        markers = [Marker(x=int(x), y=int(y), color=COLOURS[names[owner[y, x]]], glyph=chr(0x2800 + int(chosen[y, x])))
                   for y, x in zip(*np.nonzero(owner >= 0))]
        return LayerRender(None, COLOURS["residential"], markers=markers)

    def build(self, view: View):
        if self._road is None:
            return LayerRender(None, COLOURS["residential"])
        key_ = view_key(view)
        if key_ != self._frame_key:
            self._render = self._render_for(view)
            self._frame_key = key_
        return self._render

    def hit_test_all(self, x: int, y: int, radius: float | None = None):
        return []                                # the roads have no items to select

    # -- legend -------------------------------------------------------------------------
    def legend_key(self):
        return key(*((COLOURS[name], _(LABELS[name])) for name in COLOURS))

    def legend_lines(self) -> list[SidebarLine]:
        # The six classes are wrapped onto short rows, two to a row: one row of all six is
        # about 90 columns, and a row of three is up to 48 (pt_BR); both would cover the map's
        # credits line at the left and the scale bar.
        lines = super().legend_lines()
        if not self.shown or self.has_problem():
            return lines
        items = self.legend_key()
        rows = [SidebarLine(text="", legend_items=items[start:start + LEGEND_ROW])
                for start in range(0, len(items), LEGEND_ROW)]
        return lines[:1] + rows


def _latest_asset() -> str:
    """Download address of the newest road file, from the releases of this repository."""
    _status, body, _headers = get(RELEASES_URL, DOWNLOAD_TIMEOUT, min_gap=1.0)
    try:
        releases = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise SourceError(f"release list unreadable: {error}") from error
    if not isinstance(releases, list):
        raise SourceError("release list unreadable")
    return find_asset_url(releases)
