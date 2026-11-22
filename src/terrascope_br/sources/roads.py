"""Road network of the Greater São Paulo box (br_viario), from OpenStreetMap.

The file is built ahead of time by packaging/roads/build_roads.py and published as an
asset of a GitHub release (format: terrascope_br.roads_format). The layer downloads it
the first time it is switched on, from the newest release that has it, keeps it in the
cache for a week before looking for a newer one, and works from the cache offline.

What is drawn, by zoom (roads_format.CLASSES): motorways and trunk roads from zoom 16,
primary and secondary from 64, tertiary from 256, local streets (residential,
unclassified, living street) from 512. Only the roads inside the view are drawn: each
class is rasterised on the braille grid of the view, and a cell that holds roads of two
classes shows the first one (the higher class, the order of CLASSES). Each class has its
own colour: the cells are braille markers, because a layer's own lines have one colour. Markers are
drawn after every layer's lines, so the roads sit above the IBGE outlines (accepted; see _markers_for).

The layer is off when the tab opens (its own switch in the legend, the "on | off" line).
It draws nothing, and downloads nothing, while it is off.
"""

from __future__ import annotations

import json

import numpy as np
from PIL import Image, ImageDraw

from terrascope.core.layer import LayerRender, Marker, SidebarLine
from terrascope.core.view import BRAILLE_BIT_WEIGHTS, View

from terrascope_br import roads_format, store
from terrascope_br._i18n import N_, _
from terrascope_br.base import SourceLayer, key
from terrascope_br.geo import view_key
from terrascope_br.net import SourceError, get
from terrascope_br.roads_format import RoadFile

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
        self._markers: list[Marker] = []

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
        self._markers = []

    def clear(self) -> None:
        super().clear()
        self._road = None
        self._prepared = {}
        self._frame_key = None
        self._markers = []

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
            self._prepared[key] = (degrees, starts, ends, (west, south, east, north))
        return self._prepared[key]

    def _rasterize(self, view: View, name: str, index: int) -> np.ndarray:
        """Braille values (height, width) of one class: the dots of its lines in view."""
        degrees, starts, ends, (west, south, east, north) = self._prepare(name, index)
        image = Image.new("L", (view.pixel_width, view.pixel_height), 0)
        if len(starts):
            lon0, lat0 = view.origin_lon, view.origin_lat
            lon1, lat1 = lon0 + view.lon_span, lat0 - view.lat_span
            chosen = np.flatnonzero((east >= lon0) & (west <= lon1) & (north >= lat1) & (south <= lat0))
            draw = ImageDraw.Draw(image)
            for line in chosen.tolist():
                points = degrees[starts[line]:ends[line]]
                xs = (points[:, 0] - lon0) * view.scale_x
                ys = (lat0 - points[:, 1]) * view.scale_y
                draw.line(list(zip(xs.tolist(), ys.tolist())), fill=255, width=1)
        dots = np.asarray(image).reshape(view.height, 4, view.width, 2) > 0
        return (dots * BRAILLE_BIT_WEIGHTS[np.newaxis, :, np.newaxis, :]).sum(axis=(1, 3))

    def _markers_for(self, view: View) -> list[Marker]:
        # Why markers and not lines: the core draws a layer's braille lines in ONE colour
        # (LayerRender.line_color), and draws every layer's markers after all lines. A
        # colour per class therefore needs one marker per braille cell. The consequence,
        # accepted: the roads are drawn above the IBGE outlines, not below them.
        zoom = min(max(view.zoom, roads_format.LOD_ZOOMS[0]), roads_format.LOD_ZOOMS[-1])
        names = roads_format.classes_at(view.zoom)
        index = roads_format.range_index(zoom)
        if self._road is None or not names or index is None:
            return []
        chosen = np.zeros((view.height, view.width), dtype=np.int64)
        owner = np.full((view.height, view.width), -1, dtype=np.int64)
        taken = np.zeros((view.height, view.width), dtype=bool)
        for rank, name in enumerate(names):   # top class first: a cell keeps the first class's dots
            values = self._rasterize(view, name, index)
            free = (values > 0) & ~taken
            chosen[free] = values[free]
            owner[free] = rank
            taken |= free
        markers = []
        for y, x in zip(*np.nonzero(taken)):
            colour = COLOURS[names[owner[y, x]]]
            markers.append(Marker(x=int(x), y=int(y), color=colour, glyph=chr(0x2800 + int(chosen[y, x]))))
        return markers

    def build(self, view: View):
        if self._road is None:
            return LayerRender(None, COLOURS["residential"])
        key_ = view_key(view)
        if key_ != self._frame_key:
            self._markers = self._markers_for(view)
            self._frame_key = key_
        return LayerRender(None, COLOURS["residential"], markers=self._markers)

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
