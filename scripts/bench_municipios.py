"""Memory and CPU cost of the IBGE municipality outlines.

Downloads the real municipalities map through the plugin's own fetch(), into a
temporary cache directory, then measures:

  1. RSS (process working set / resident set), with tracemalloc OFF, before and
     after loading + parsing the GeoJSON and after converting it to the layer's
     rings. Also the process peak RSS.
  2. Python heap with tracemalloc: peak and retained bytes of the same two steps.
     tracemalloc keeps its own traces in memory, so it is never on during 1.
  3. One frame over Sao Paulo at each zoom level, drawn onto an in-memory Braille
     canvas by the same draw_outlines() the layer calls (no curses). Timed with
     tracemalloc off, as the application runs.

Usage (from the checkout, with terrascope-br installed in editable mode):

    python scripts/bench_municipios.py
    python scripts/bench_municipios.py --zoom 8 16 64 --frames 21 --cols 120 --rows 40 --json out.json

Stdlib only apart from terrascope_br's own dependencies. RSS is read with the
Windows process API (ctypes) or from /proc on Linux.
"""

from __future__ import annotations

import argparse
import gc
import json
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

from terrascope.core import config
from terrascope.core.view import WRAP_SHIFTS, View

from terrascope_br import store
from terrascope_br.geo import draw_outlines, rings_from_geojson
from terrascope_br.sources import ibge

SAO_PAULO = (-46.63, -23.55)  # longitude, latitude of the city centre
BLANK_BRAILLE = "\u2800"  # an empty Braille cell
MB = 2**20


def rss_bytes(peak: bool = False) -> int | None:
    """Working set (Windows) or resident set (Linux), in bytes: the current one, or
    with peak=True the highest one the process has reached so far."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32")
        psapi = ctypes.WinDLL("psapi")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(),
                                          ctypes.byref(counters), counters.cb):
            return None
        return int(counters.PeakWorkingSetSize if peak else counters.WorkingSetSize)
    field = "VmHWM:" if peak else "VmRSS:"
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith(field):
                    return int(line.split()[1]) * 1024
    except OSError:
        return None
    return None


def _mb(value: int | None) -> float | None:
    return None if value is None else round(value / MB, 2)


def load_and_parse(file):
    """What the layer does first with the cache file: read it and parse the JSON."""
    data = store.read_bytes(file)
    return json.loads(data.decode("utf-8"))


def measure_rss(file) -> dict:
    """RSS before/after load+parse and after conversion. tracemalloc must be off."""
    gc.collect()
    rss_start = rss_bytes()
    started = time.perf_counter()
    payload = load_and_parse(file)
    load_seconds = time.perf_counter() - started
    rss_loaded = rss_bytes()

    started = time.perf_counter()
    rings = rings_from_geojson(payload)
    convert_seconds = time.perf_counter() - started
    rss_converted = rss_bytes()

    del payload
    gc.collect()
    rss_released = rss_bytes()
    process_peak = rss_bytes(peak=True)
    del rings
    return {
        "rss_before_mb": _mb(rss_start),
        "load_parse": {"seconds": round(load_seconds, 4),
                       "rss_after_mb": _mb(rss_loaded),
                       "rss_delta_mb": _mb(rss_loaded - rss_start) if None not in (rss_loaded, rss_start) else None},
        "convert": {"seconds": round(convert_seconds, 4),
                    "rss_after_mb": _mb(rss_converted),
                    "rss_delta_mb": _mb(rss_converted - rss_loaded) if None not in (rss_converted, rss_loaded) else None},
        "rss_after_release_mb": _mb(rss_released),
        "process_peak_rss_mb": _mb(process_peak),
    }


def measure_heap(file) -> tuple[dict, list]:
    """tracemalloc peak and retained bytes of load+parse and of the conversion.
    Returns the report and the rings, which the frame timings use."""
    gc.collect()
    traced_start = tracemalloc.get_traced_memory()[0]

    tracemalloc.reset_peak()
    started = time.perf_counter()
    payload = load_and_parse(file)
    load_seconds = time.perf_counter() - started
    load_current, load_peak = tracemalloc.get_traced_memory()

    tracemalloc.reset_peak()
    started = time.perf_counter()
    rings = rings_from_geojson(payload)
    convert_seconds = time.perf_counter() - started
    convert_current, convert_peak = tracemalloc.get_traced_memory()

    # The JSON is released; what remains is what the layer keeps in memory.
    del payload
    gc.collect()
    kept = tracemalloc.get_traced_memory()[0] - traced_start
    report = {
        "load_parse": {"seconds": round(load_seconds, 4),
                       "peak_mb": _mb(load_peak - traced_start),
                       "retained_mb": _mb(load_current - traced_start)},
        "convert": {"seconds": round(convert_seconds, 4),
                    "peak_mb": _mb(convert_peak - load_current),
                    "retained_mb": _mb(convert_current - load_current),
                    "retained_with_json_alive_mb": _mb(convert_current - traced_start)},
        "rings_kept_after_release_mb": _mb(kept),
    }
    return report, rings


def count_frame(rings, view: View) -> dict:
    """What the frame has to draw, to explain its cost. A ring drawn at a wrap
    shift counts once per shift, so ring_draws can exceed the number of rings."""
    draws = 0
    points = 0
    for ring in rings:
        for shift in WRAP_SHIFTS:
            if ring.touches(view, shift):
                draws += 1
                points += len(ring.points)
    return {"rings": len(rings), "ring_draws": draws, "points_drawn": points}


def time_frames(rings, view: View, frames: int) -> dict:
    timings = []
    lines: list[str] = []
    for _ in range(frames):
        started = time.perf_counter()
        lines = draw_outlines(rings, view)
        timings.append((time.perf_counter() - started) * 1000.0)
    lit_cells = sum(1 for line in lines for ch in line if ch != BLANK_BRAILLE)
    return {
        "frame_ms_median": round(statistics.median(timings), 2),
        "frame_ms_min": round(min(timings), 2),
        "frame_ms_max": round(max(timings), 2),
        "lit_cells": lit_cells,
    }


def run(zooms: list[float], frames: int, cols: int, rows: int, cache: Path) -> dict:
    config.CACHE_DIR = cache
    layer = ibge.MunicipalityOutlinesLayer()
    layer.enabled = True

    started = time.perf_counter()
    layer.fetch()  # the plugin's own download; writes <cache>/br/ibge/municipios.geojson
    download_seconds = time.perf_counter() - started
    file = store.path("ibge", layer.cache_name)

    rss = measure_rss(file)            # first, so the process peak is not inflated by tracemalloc
    tracemalloc.start()
    try:
        heap, rings = measure_heap(file)
    finally:
        tracemalloc.stop()

    views = {}
    for zoom in zooms:
        view = View(SAO_PAULO[0], SAO_PAULO[1], zoom, cols, rows)
        views[str(zoom)] = {**count_frame(rings, view), **time_frames(rings, view, frames)}

    return {
        "file_bytes": file.stat().st_size,
        "download_seconds": round(download_seconds, 3),
        "rings": len(rings),
        "rss": rss,
        "heap_tracemalloc": heap,
        "frame": {"cols": cols, "rows": rows, "center": SAO_PAULO, "by_zoom": views},
    }


def environment() -> dict:
    import importlib.metadata as md
    import platform

    def version(name: str) -> str:
        try:
            return md.version(name)
        except md.PackageNotFoundError:
            return "not installed"

    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor() or "unknown",
        "terrascope": version("terrascope"),
        "numpy": version("numpy"),
        "pillow": version("Pillow"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--zoom", type=float, nargs="+", default=[8.0, 16.0, 64.0])
    parser.add_argument("--frames", type=int, default=21)
    parser.add_argument("--cols", type=int, default=120)
    parser.add_argument("--rows", type=int, default=40)
    parser.add_argument("--json", type=Path, help="also write the results to this file")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="bench-municipios-") as cache_dir:
        result = run(args.zoom, args.frames, args.cols, args.rows, Path(cache_dir))
    result["environment"] = environment()
    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if args.json:
        args.json.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
