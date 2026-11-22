"""Build the road network file of the BRASIL tab (br_viario) from OpenStreetMap.

    python packaging/roads/build_roads.py --out out/br-viario-rmsp.npz
    python packaging/roads/build_roads.py --out out/x.npz --raw-dir raw/   # keeps the tile answers

The source of a regular build is a Geofabrik extract (--source pbf --pbf FILE, the
sudeste-latest.osm.pbf of download.geofabrik.de; a bulk download, which is what the OSM
API policy asks for). It needs pyosmium (the "osmium" package), which is a generator
dependency only, not a plugin one. The Overpass API (--source overpass, the default of the
earlier runs) is a FALLBACK for a build without an extract: its public servers drop
connections and answer 502 often, and the OSM policy asks frequent users not to use them
for bulk data, so it is not used in CI. Overpass queries the box by tiles of
the Região Metropolitana de São Paulo (roads only: the classes of
terrascope_br.roads_format.CLASSES). Each tile
answer is kept under --raw-dir when given, and read from there when it is already
there, so a run can be repeated without asking the server again. A way that crosses
two tiles comes back in both; it is kept once, by its id.

Each class is simplified (Douglas-Peucker, in metres) once per zoom range it is drawn
in, with the tolerance of roads_format.range_tolerance_metres(). The result is written
with roads_format.write(); the file is checked again by reading it back.

Data: © OpenStreetMap contributors, ODbL 1.0. The output is a derived database under the
same licence: it is published with the attribution in its meta entry and in the release.

Stdlib and numpy only. The module imports terrascope_br.roads_format from the source
tree (src/), so it runs without the Terrascope core installed.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from terrascope_br import roads_format as fmt  # noqa: E402

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
USER_AGENT = "terrascope-br-roads-builder/1.0 (https://github.com/fernandoaugusto21/terrascope-br)"
TILE_DEGREES = 0.2
PAUSE_SECONDS = 2.0          # between two tiles: the Overpass fair-use policy asks for a pause
ATTEMPTS = 4
RETRY_SECONDS = (15, 45, 120)


def tile_boxes(bbox: tuple[float, float, float, float], size: float) -> list[tuple[float, float, float, float]]:
    """(south, west, north, east) boxes covering `bbox` (west, south, east, north) in `size` steps."""
    west, south, east, north = bbox
    columns = math.ceil((east - west) / size - 1e-9)
    rows = math.ceil((north - south) / size - 1e-9)
    boxes = []
    for row in range(rows):
        for column in range(columns):
            boxes.append((
                south + row * size,
                west + column * size,
                min(north, south + (row + 1) * size),
                min(east, west + (column + 1) * size),
            ))
    return boxes


def overpass_query(box: tuple[float, float, float, float]) -> str:
    south, west, north, east = box
    classes = "|".join(fmt.HIGHWAY_VALUES)
    return (
        "[out:json][timeout:300];"
        f'way["highway"~"^({classes})$"]({south:.6f},{west:.6f},{north:.6f},{east:.6f});'
        "out geom;"
    )


def fetch_tile(box, raw_file: Path | None, log) -> dict:
    """The JSON answer of one tile: from raw_file when it is there, else from Overpass."""
    if raw_file is not None and raw_file.exists():
        return json.loads(raw_file.read_bytes())
    body = urllib.parse.urlencode({"data": overpass_query(box)}).encode("ascii")
    last_error: Exception | None = None
    for attempt in range(ATTEMPTS):
        url = OVERPASS_URLS[attempt % len(OVERPASS_URLS)]
        request = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=400) as response:
                raw = response.read()
            payload = json.loads(raw)
            if "elements" not in payload:
                raise ValueError(f"no elements in the answer: {str(payload)[:200]}")
            if raw_file is not None:
                raw_file.parent.mkdir(parents=True, exist_ok=True)
                raw_file.write_bytes(raw)
            return payload
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            last_error = error
            wait = RETRY_SECONDS[min(attempt, len(RETRY_SECONDS) - 1)]
            log(f"  tile {box}: {type(error).__name__}: {error}; retry in {wait} s ({url})")
            time.sleep(wait)
    raise RuntimeError(f"tile {box} failed after {ATTEMPTS} attempts: {last_error}")


def collect_ways(elements: list[dict], ways: dict[int, tuple[str, np.ndarray]]) -> None:
    """Add the roads of one answer to `ways` (id -> (class, lon/lat array)), by id."""
    for element in elements:
        if element.get("type") != "way" or element.get("id") in ways:
            continue
        highway = (element.get("tags") or {}).get("highway")
        geometry = element.get("geometry") or []
        class_name = fmt.HIGHWAY_TO_CLASS.get(highway)
        if class_name is None or len(geometry) < 2:
            continue
        points = np.array([(node["lon"], node["lat"]) for node in geometry], dtype=np.float64)
        ways[element["id"]] = (class_name, points)


def read_overpass(bbox, raw_dir: Path | None, log) -> dict[int, tuple[str, np.ndarray]]:
    ways: dict[int, tuple[str, np.ndarray]] = {}
    boxes = tile_boxes(bbox, TILE_DEGREES)
    log(f"overpass: {len(boxes)} tiles of {TILE_DEGREES} degrees")
    for number, box in enumerate(boxes, 1):
        raw_file = None
        if raw_dir is not None:
            raw_file = raw_dir / f"tile-{box[0]:.2f}-{box[1]:.2f}.json"
        fetched = raw_file is None or not raw_file.exists()
        payload = fetch_tile(box, raw_file, log)
        before = len(ways)
        collect_ways(payload["elements"], ways)
        log(f"  tile {number}/{len(boxes)} ({'fetched' if fetched else 'from raw'}): +{len(ways) - before} ways")
        if fetched and number < len(boxes):
            time.sleep(PAUSE_SECONDS)
    return ways


def read_pbf(path: Path, bbox, log) -> dict[int, tuple[str, np.ndarray]]:
    """Roads of a local OpenStreetMap extract (.osm.pbf), read with pyosmium."""
    try:
        import osmium
    except ImportError as error:
        raise SystemExit("--source pbf needs pyosmium (pip install osmium)") from error

    west, south, east, north = bbox
    ways: dict[int, tuple[str, np.ndarray]] = {}

    class Handler(osmium.SimpleHandler):
        def way(self, way):
            # Only the roads with a node in the box are kept (the same rule the Overpass
            # query uses): the arrays are built for those, not for every road of the extract.
            class_name = fmt.HIGHWAY_TO_CLASS.get(way.tags.get("highway", ""))
            if class_name is None or way.id in ways:
                return
            points = []
            inside = False
            for node in way.nodes:
                if not node.location.valid():
                    continue
                lon, lat = node.location.lon, node.location.lat
                points.append((lon, lat))
                inside = inside or (west <= lon <= east and south <= lat <= north)
            if inside and len(points) >= 2:
                ways[way.id] = (class_name, np.array(points, dtype=np.float64))

    log(f"pbf: {path}")
    handler = Handler()
    # locations=True: the node positions come from the file itself, indexed in memory.
    handler.apply_file(str(path), locations=True)
    return ways


def _metres(points: np.ndarray, lat0: float) -> np.ndarray:
    scale = math.cos(math.radians(lat0))
    return np.column_stack((
        points[:, 0] * fmt.METRES_PER_DEGREE_LAT * scale,
        points[:, 1] * fmt.METRES_PER_DEGREE_LAT,
    ))


def simplify_indices(xy: np.ndarray, tolerance: float) -> np.ndarray:
    """Indices of the vertices Douglas-Peucker keeps (first and last always kept)."""
    count = len(xy)
    keep = np.zeros(count, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, count - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        origin = xy[start]
        direction = xy[end] - origin
        inner = xy[start + 1:end] - origin
        length2 = float(direction @ direction)
        if length2 == 0.0:
            distance = np.hypot(inner[:, 0], inner[:, 1])
        else:
            t = np.clip((inner @ direction) / length2, 0.0, 1.0)
            offset = inner - t[:, None] * direction
            distance = np.hypot(offset[:, 0], offset[:, 1])
        worst = int(np.argmax(distance))
        if distance[worst] > tolerance:
            middle = start + 1 + worst
            keep[middle] = True
            stack.append((start, middle))
            stack.append((middle, end))
    return np.flatnonzero(keep)


def simplify_line(points: np.ndarray, tolerance: float) -> np.ndarray:
    """The kept vertices of one polyline (lon/lat), with consecutive duplicates removed first."""
    changed = np.ones(len(points), dtype=bool)
    changed[1:] = np.any(np.diff(points, axis=0) != 0, axis=1)
    points = points[changed]
    if len(points) <= 2:
        return points
    xy = _metres(points, float(points[:, 1].mean()))
    return points[simplify_indices(xy, tolerance)]


def build(ways: dict[int, tuple[str, np.ndarray]], log) -> tuple[dict, dict]:
    """(polylines per (class, range), statistics) of the collected ways."""
    by_class: dict[str, list[np.ndarray]] = {name: [] for name, _tags, _zoom in fmt.CLASSES}
    for class_name, points in ways.values():
        by_class[class_name].append(points)
    ways_per_class = Counter(class_name for class_name, _points in ways.values())
    stats: dict = {"ways": {}, "points": {}, "ranges": {}}
    polylines: dict[tuple[str, int], fmt.Polylines] = {}
    for name, _tags, _zoom in fmt.CLASSES:
        stats["ways"][name] = ways_per_class.get(name, 0)
        stats["points"][name] = sum(len(points) for points in by_class[name])
        for index in fmt.ranges_of_class(name):
            tolerance = fmt.range_tolerance_metres(index)
            started = time.perf_counter()
            lines = [simplify_line(points, tolerance) for points in by_class[name]]
            lines = [line for line in lines if len(line) >= 2]
            if lines:
                xy = np.rint(np.concatenate(lines) * fmt.SCALE).astype(np.int32)
                sizes = np.array([len(line) for line in lines], dtype=np.int64)
                offsets = np.concatenate(([0], np.cumsum(sizes))).astype(np.int32)
            else:
                xy = np.zeros((0, 2), dtype=np.int32)
                offsets = np.zeros(1, dtype=np.int32)
            polylines[(name, index)] = fmt.Polylines(xy, offsets)
            stats["ranges"][f"{name}/{index}"] = {
                "tolerance_m": round(tolerance, 1),
                "polylines": len(lines),
                "points": int(len(xy)),
                "seconds": round(time.perf_counter() - started, 2),
            }
            log(f"  {name:<12} range {index} (zoom {fmt.LOD_ZOOMS[index]:g}-{fmt.LOD_ZOOMS[index + 1]:g}): "
                f"tolerance {tolerance:6.1f} m, {len(lines):>6} lines, {len(xy):>8} points")
    return polylines, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="the .npz file to write")
    parser.add_argument("--source", choices=("overpass", "pbf"), default="overpass")
    parser.add_argument("--pbf", type=Path, help="with --source pbf: the .osm.pbf extract to read")
    parser.add_argument("--raw-dir", type=Path, help="overpass: keep the tile answers here, and read them when present")
    parser.add_argument("--bbox", type=float, nargs=4, default=fmt.BBOX, metavar=("W", "S", "E", "N"))
    args = parser.parse_args(argv)

    def log(message: str) -> None:
        print(message, flush=True)

    started = time.perf_counter()
    bbox = tuple(args.bbox)
    if args.source == "pbf":
        if args.pbf is None:
            parser.error("--source pbf needs --pbf")
        ways = read_pbf(args.pbf, bbox, log)
    else:
        ways = read_overpass(bbox, args.raw_dir, log)
    read_seconds = time.perf_counter() - started
    log(f"read {len(ways)} roads in {read_seconds:.1f} s")

    polylines, stats = build(ways, log)
    meta = {
        "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "built_epoch": int(time.time()),
        "source": (f"pbf extract {args.pbf.name} (Geofabrik): OpenStreetMap" if args.source == "pbf"
                   else "Overpass API (tiles): OpenStreetMap"),
        "attribution": fmt.ATTRIBUTION,
        "bbox": list(bbox),
        "lod_zooms": list(fmt.LOD_ZOOMS),
        "classes": [{"name": name, "min_zoom": zoom} for name, _tags, zoom in fmt.CLASSES],
        "stats": stats,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fmt.write(args.out, polylines, meta)
    fmt.check(args.out)  # read it back, every array
    total = time.perf_counter() - started
    log(f"wrote {args.out} ({args.out.stat().st_size / 2**20:.2f} MiB) in {total:.1f} s "
        f"(read {read_seconds:.1f} s, simplify and write {total - read_seconds:.1f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
