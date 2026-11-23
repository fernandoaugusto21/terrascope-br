"""File format of the road network layer (br_viario), shared by the generator
(packaging/roads/build_roads.py) and the layer (sources/roads.py).

The file is a numpy .npz (no pickles), published as a GitHub release asset. It holds
the OpenStreetMap roads of the Greater São Paulo box, one array pair per road class and
zoom range:

    <class>_<range>_xy    int32 (N, 2): longitude and latitude in degrees * SCALE
    <class>_<range>_off   int32 (M + 1): where each polyline starts in `xy`
    meta                  0-d unicode array: JSON with the format, the build time,
                          the source, the attribution and the point counts

A zoom range k runs from LOD_ZOOMS[k] up to LOD_ZOOMS[k + 1] (the last one ends at
LOD_ZOOMS[-1]). Each class is simplified (Douglas-Peucker) for the finest braille dot
of its range, so a range's geometry is never coarser than a dot there.

Files built before the finer ranges (zoom 1024 to 4096) have the ranges that end at or
below COARSE_END only. Those still read: at the finer zooms the layer draws the finest
range the file has (stored_range), which is a dot or two coarser there.

Pure numpy: no import of the Terrascope core, so the generator can run without it.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

FORMAT = "br-viario"
VERSION = 1
ASSET_NAME = "br-viario-rmsp.npz"
SCALE = 10_000_000                       # degrees -> int32 (about 1 cm)
BBOX = (-47.21, -24.06, -45.69, -23.18)  # west, south, east, north (Região Metropolitana de São Paulo)
ATTRIBUTION = "© OpenStreetMap contributors (ODbL 1.0)"
LAT_RANGE = 142.0                        # degrees of latitude in a full view (map.lat_min -58 .. lat_max 84)
DOT_ROWS = 58 * 4                        # braille dot rows of the BRASIL tab (58 cell rows)
METRES_PER_DEGREE_LAT = 111_200.0

# Zoom where each range starts; the last value is the end of the last range.
LOD_ZOOMS = (16.0, 64.0, 256.0, 512.0, 1024.0, 2048.0, 4096.0)
# The ranges that end at or below this zoom are in every file (see the header).
COARSE_END = 1024.0

# Road classes, top first: the first class is drawn over the later ones where two share a
# braille cell. (name, OpenStreetMap highway values, first zoom where it is drawn)
CLASSES = (
    ("motorway", ("motorway", "motorway_link"), 16.0),
    ("trunk", ("trunk", "trunk_link"), 16.0),
    ("primary", ("primary", "primary_link"), 64.0),
    ("secondary", ("secondary", "secondary_link"), 64.0),
    ("tertiary", ("tertiary", "tertiary_link"), 256.0),
    ("residential", ("residential", "unclassified", "living_street"), 512.0),
)
HIGHWAY_TO_CLASS = {tag: name for name, tags, _ in CLASSES for tag in tags}
HIGHWAY_VALUES = tuple(sorted(HIGHWAY_TO_CLASS))


def metres_per_dot(zoom: float) -> float:
    """Length of one braille dot (vertical) at this zoom, as the BRASIL tab shows it."""
    return LAT_RANGE / zoom / DOT_ROWS * METRES_PER_DEGREE_LAT


def range_tolerance_metres(range_index: int) -> float:
    """Douglas-Peucker tolerance of a zoom range: half a dot at its finest zoom."""
    return 0.5 * metres_per_dot(LOD_ZOOMS[range_index + 1])


def range_index(zoom: float) -> int | None:
    """The zoom range that holds this zoom, or None outside LOD_ZOOMS."""
    if zoom < LOD_ZOOMS[0] or zoom > LOD_ZOOMS[-1]:
        return None
    for index in range(len(LOD_ZOOMS) - 1):
        if zoom < LOD_ZOOMS[index + 1]:
            return index
    return len(LOD_ZOOMS) - 2


def class_min_zoom(name: str) -> float:
    for class_name, _tags, min_zoom in CLASSES:
        if class_name == name:
            return min_zoom
    raise KeyError(name)


def classes_at(zoom: float) -> tuple[str, ...]:
    """Names of the classes drawn at this zoom, top first."""
    return tuple(name for name, _tags, min_zoom in CLASSES if zoom >= min_zoom)


def ranges_of_class(name: str) -> tuple[int, ...]:
    """The zoom ranges in which a class is drawn (and so stored)."""
    return tuple(
        index for index in range(len(LOD_ZOOMS) - 1)
        if LOD_ZOOMS[index + 1] > class_min_zoom(name)
    )


def array_names(name: str, index: int) -> tuple[str, str]:
    return f"{name}_{index}_xy", f"{name}_{index}_off"


@dataclass
class Polylines:
    """One class in one zoom range: `xy` is (N, 2) int32, `offsets` (M + 1,) int32."""

    xy: np.ndarray
    offsets: np.ndarray

    def degrees(self) -> np.ndarray:
        """The vertices as float degrees, (N, 2) lon and lat."""
        return self.xy.astype(np.float64) / SCALE

    @property
    def count(self) -> int:
        return int(len(self.offsets) - 1)


def validate_polylines(polylines: Polylines) -> None:
    offsets = polylines.offsets
    if polylines.xy.ndim != 2 or polylines.xy.shape[1] != 2 or polylines.xy.dtype != np.int32:
        raise ValueError("coordinates are not an int32 (N, 2) array")
    if offsets.ndim != 1 or offsets.dtype != np.int32 or len(offsets) < 1:
        raise ValueError("offsets are not an int32 vector")
    if offsets[0] != 0 or offsets[-1] != len(polylines.xy) or np.any(np.diff(offsets) < 2):
        raise ValueError("offsets do not cover the coordinates, or a polyline has fewer than 2 points")


def write(target, polylines: dict[tuple[str, int], Polylines], meta: dict) -> None:
    """Write the file to a path or a binary file object. `meta` gets the format and version."""
    arrays = {}
    for (name, index), line in polylines.items():
        validate_polylines(line)
        xy_key, off_key = array_names(name, index)
        arrays[xy_key] = line.xy
        arrays[off_key] = line.offsets
    header = dict(meta, format=FORMAT, version=VERSION)
    arrays["meta"] = np.array(json.dumps(header, ensure_ascii=False, sort_keys=True))
    np.savez_compressed(target, **arrays)


class RoadFile:
    """A road file opened for reading. The arrays are read when first asked for."""

    def __init__(self, source) -> None:
        if isinstance(source, (bytes, bytearray)):
            source = io.BytesIO(bytes(source))
        elif isinstance(source, Path):
            source = str(source)
        self._archive = np.load(source, allow_pickle=False)
        self.meta = _read_meta(self._archive)
        self._cache: dict[tuple[str, int], Polylines] = {}

    def has(self, name: str, index: int) -> bool:
        xy_key, off_key = array_names(name, index)
        return xy_key in self._archive.files and off_key in self._archive.files

    def stored_range(self, name: str, index: int) -> int | None:
        """The range to draw a class from at range `index`: the same one when the file has it,
        else the finest one below it (a file built before the finer ranges). None when the file
        has the class in no range up to it."""
        for candidate in range(index, -1, -1):
            if self.has(name, candidate):
                return candidate
        return None

    def polylines(self, name: str, index: int) -> Polylines:
        key = (name, index)
        if key not in self._cache:
            xy_key, off_key = array_names(name, index)
            line = Polylines(np.asarray(self._archive[xy_key]), np.asarray(self._archive[off_key]))
            validate_polylines(line)
            self._cache[key] = line
        return self._cache[key]

    def close(self) -> None:
        self._archive.close()


def _read_meta(archive) -> dict:
    if "meta" not in archive.files:
        raise ValueError("no meta entry")
    meta = json.loads(str(archive["meta"]))
    if meta.get("format") != FORMAT:
        raise ValueError(f"not a {FORMAT} file (format {meta.get('format')!r})")
    if meta.get("version") != VERSION:
        raise ValueError(f"format version {meta.get('version')!r}, expected {VERSION}")
    return meta


def check(source) -> dict:
    """Open and validate a file, every array included. Returns its meta; raises ValueError
    (or OSError for a file that is not an archive) when the file is not usable. The finer
    ranges (above COARSE_END) are optional: a file built before them is still valid."""
    road_file = RoadFile(source)
    try:
        for name, _tags, _min_zoom in CLASSES:
            for index in ranges_of_class(name):
                if not road_file.has(name, index):
                    if LOD_ZOOMS[index + 1] <= COARSE_END:
                        raise ValueError(f"no {name} in zoom range {index}")
                    continue
                road_file.polylines(name, index)
        return road_file.meta
    except (KeyError, EOFError) as error:
        raise ValueError(f"damaged file: {error}") from error
    finally:
        road_file.close()
