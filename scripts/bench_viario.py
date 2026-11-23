"""Cost of the road network layer (br_viario) over São Paulo, at the zooms of the BRASIL tab.

Reads a road file (packaging/roads/build_roads.py) through the layer's own reader, then,
for each zoom, builds the layer's markers for a view of the city, as the layer does when
the view changes (the layer keeps them until the next change). Reports:

  1. the time to open the file (its metadata) and the first use of each class's arrays;
  2. per zoom: the median build time over several views (each view is new, so nothing is
     cached), the number of braille markers, the markers per class, and the Python heap
     peak of one build (tracemalloc, measured apart from the timing);
  3. the process memory (RSS) before and after the reads, and the arrays the layer keeps.

The drawing of the markers by the core (one addstr per cell) is not in these numbers: it
needs a terminal, and is measured with the ConPTY harness instead.

Needs psutil for the memory figures (pip install psutil). Usage (from the checkout, with terrascope-br installed in editable mode):

    python scripts/bench_viario.py --file path/to/br-viario-rmsp.npz
    python scripts/bench_viario.py --file out.npz --zoom 16 64 256 512 1024 --views 7 --json out.json
"""

from __future__ import annotations

import argparse
import gc
import json
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path

from terrascope.core import config
from terrascope.core.view import View

from terrascope_br import roads_format, store
from terrascope_br.sources import roads

SAO_PAULO = (-46.63, -23.55)   # longitude, latitude of the city centre
COLS, ROWS = 108, 58           # the BRASIL tab's map area at 1 cell = 2 x 4 dots (see performance-municipios.md)
MB = 2**20


class _Cell:
    """One drawn braille cell: what the bench counts (its colour)."""

    __slots__ = ("color",)

    def __init__(self, color: str) -> None:
        self.color = color


def _markers_for(layer, view):
    """The cells the layer draws for a view, as the core gets them: its markers, and one cell per
    non-blank braille character of its line layers (a core with LayerRender.line_layers)."""
    render = layer._render_for(view)
    cells = list(render.markers)
    for lines, colour in render.line_layers or ():
        for row in lines:
            cells += [_Cell(colour) for char in row if char != "⠀"]
    return cells


def rss_bytes() -> int | None:
    """Resident memory of this process, in bytes (psutil, a development tool: not a plugin dependency)."""
    try:
        import psutil
    except ImportError:
        return None
    return int(psutil.Process().memory_info().rss)


def view_at(zoom: float, lon: float, lat: float) -> View:
    return View(lon, lat, zoom, COLS, ROWS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--zoom", type=float, nargs="+", default=[16, 64, 256, 512, 1024])
    parser.add_argument("--views", type=int, default=7, help="views per zoom (their centres move a little)")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    report: dict = {"file": str(args.file), "size_bytes": args.file.stat().st_size, "zooms": {}}
    with tempfile.TemporaryDirectory(prefix="viario-bench-") as tmp:
        config.CACHE_DIR = Path(tmp)
        store.write_bytes(store.path("viario", "br-viario.npz"), args.file.read_bytes())
        gc.collect()
        rss_before = rss_bytes()

        layer = roads.RoadsLayer()
        layer.shown = True
        started = time.perf_counter()
        layer.reload()
        report["open_seconds"] = round(time.perf_counter() - started, 4)
        report["built"] = layer._road.meta.get("built")
        report["stats"] = layer._road.meta.get("stats", {})

        started = time.perf_counter()
        for name, _tags, _zoom in roads_format.CLASSES:
            for index in roads_format.ranges_of_class(name):
                stored = layer._road.stored_range(name, index)   # a file built before the finer ranges
                if stored is not None:
                    layer._prepare(name, stored)
        report["prepare_all_seconds"] = round(time.perf_counter() - started, 3)
        gc.collect()
        report["rss_after_open_mb"] = round((rss_bytes() or 0) / MB, 1)
        report["rss_before_mb"] = round((rss_before or 0) / MB, 1)

        for zoom in args.zoom:
            layer._prepared = {}     # first build of this zoom reads its arrays again, like a new view
            layer._frame_key = None
            times, counts, per_class = [], [], {}
            for step in range(args.views):
                lon = SAO_PAULO[0] + 0.01 * step - 0.03
                lat = SAO_PAULO[1] + 0.005 * step - 0.015
                gc.collect()
                started = time.perf_counter()
                markers = _markers_for(layer, view_at(zoom, lon, lat))
                times.append(time.perf_counter() - started)
                counts.append(len(markers))
                if step == 0:
                    for marker in markers:
                        per_class[marker.color] = per_class.get(marker.color, 0) + 1
            tracemalloc.start()
            _markers_for(layer, view_at(zoom, SAO_PAULO[0], SAO_PAULO[1]))
            _current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            report["zooms"][str(zoom)] = {
                "classes": list(roads_format.classes_at(zoom)),
                "build_median_ms": round(statistics.median(times) * 1000, 2),
                "build_max_ms": round(max(times) * 1000, 2),
                "markers_median": int(statistics.median(counts)),
                "markers_by_colour_first_view": per_class,
                "python_heap_peak_mb": round(peak / MB, 2),
            }
            print(f"zoom {zoom:>6g}: build median {report['zooms'][str(zoom)]['build_median_ms']:8.2f} ms, "
                  f"max {report['zooms'][str(zoom)]['build_max_ms']:8.2f} ms, "
                  f"markers {report['zooms'][str(zoom)]['markers_median']:6d}, "
                  f"heap peak {report['zooms'][str(zoom)]['python_heap_peak_mb']:6.2f} MB", flush=True)

        gc.collect()
        report["rss_end_mb"] = round((rss_bytes() or 0) / MB, 1)
        print(f"file {args.file.stat().st_size / MB:.2f} MB, open {report['open_seconds']} s, "
              f"prepare all {report['prepare_all_seconds']} s, RSS {report['rss_before_mb']} -> "
              f"{report['rss_after_open_mb']} (open) -> {report['rss_end_mb']} MB (end)")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
