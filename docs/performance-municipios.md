# Municipality outlines: memory and frame cost

The `br_municipios` layer parses the IBGE municipality map (`municipios.geojson`, about 3.6 MB, downloaded once
and kept for 30 days) and draws its rings on the Braille grid when the view is zoomed in (zoom 8 and up). This
note measures what that costs in memory and CPU, and records the decision taken from the numbers.

The benchmark is `scripts/bench_municipios.py`. Run it from the checkout with `terrascope-br` installed in
editable mode:

    python scripts/bench_municipios.py --json results.json

## Environment

| | |
|---|---|
| OS | Windows 11 Pro, build 26300 |
| CPU | AMD64, family 25, model 117 (laptop class) |
| Python | 3.13.14, 64-bit, clean virtualenv |
| Terrascope core | 1.0.0 from the fork's `main` branch |
| numpy / Pillow | 2.5.4 / 13.0.0 |
| Data | IBGE municipalities map, `qualidade=minima`: 3,605,638 bytes, 5,602 rings (outer rings and holes of the municipalities) |
| Canvas | 120 x 40 terminal cells (240 x 160 sub-pixels), in memory, no curses |
| View | centre on the city of São Paulo (-46.63, -23.55), zoom 8, 16 and 64 |

Method:

- **Download**: the layer's own `fetch()`, into a temporary cache directory. Not timed as part of the result
  (it depends on the network; 0.27 to 0.46 s here).
- **RSS** (working set, Windows `GetProcessMemoryInfo`): read with tracemalloc **off**. tracemalloc keeps its own
  traces in memory, so RSS taken while it runs is inflated.
- **Python heap**: `tracemalloc` peak and retained bytes of the same steps, in a second pass.
- **Frame**: `draw_outlines()`, the function the layer calls, onto the in-memory Braille canvas, 21 frames per
  zoom, median reported. Timed with tracemalloc off.

## Results

Before and after are the same: no code under `src/` changed, so the figures below are the baseline for any later
change. Four full runs, ranges shown.

### Memory of the municipality layer

| Step | Time | Python heap peak | Python heap kept | RSS after step |
|---|---|---|---|---|
| Process with imports, before loading | | | | 55.2 to 55.5 MB |
| Load the file and parse the JSON | 0.05 to 0.07 s | 30.7 MB | 23.9 MB (the dict) | 81.2 to 81.5 MB |
| Convert to the layer's rings (JSON still alive) | 0.05 to 0.07 s | 3.8 MB | 27.6 MB in total | 84.8 to 85.1 MB |
| Release the JSON, keep the rings | | | 3.8 MB | 60.8 to 61.1 MB |

- Process peak RSS during the run: 88.8 to 89.1 MB.
- Transient cost (the JSON dict and the file bytes) is about 31 MB of Python heap, and about 26 MB of RSS. It
  happens only when the file is read (startup, or after a download), not per frame: the parsed rings are cached
  until the file changes.
- Steady cost kept by the layer: about 3.8 MB of heap for the 5,602 rings (numpy float64 arrays and one `Ring`
  object each).

Time with tracemalloc on was 0.29 to 0.44 s for the parse step; that figure is inflated by tracing and is not the
cost of the application. The 0.05 to 0.07 s above is the untraced run.

### Frame over São Paulo

Median of 21 frames per zoom, for each of four runs at 120 x 40 cells:

| Zoom | Median frame (range over 4 runs) | Rings drawn (ring x wrap shift) | Points drawn | Lit Braille cells |
|---|---|---|---|---|
| 8 | 26 to 34 ms | 3,309 | 70,492 | 2,148 |
| 16 | 20 to 23 ms | 2,058 | 37,140 | 3,036 |
| 64 | 6 to 7 ms | 228 | 3,692 | 2,070 |

Other terminal sizes, one run each, zoom 8 / 16 / 64 median:

- 200 x 60 cells: 29.7 / 18.3 / 6.0 ms (the slowest single frame at zoom 8 was 41.9 ms).
- 80 x 24 cells: 25.9 / 17.0 / 4.9 ms.

Where the frame time goes (separate probe, same machine, 21 frames):

- The ring scan (`touches()` for each of the 5,602 rings at each wrap shift) costs about 5 to 6 ms at every zoom.
  It is a fixed cost.
- The rest is drawing. At zoom 8 the drawing is most of the frame (about 31 ms of 37 ms in that probe), because
  about 70,000 points are converted and drawn; at zoom 64 the frame is almost only the scan.

## Decision

**No optimization is made.** The rule for this item is to optimize only when the cost is relevant, meaning more
than 50 MB or frames above about 50 ms. Neither is reached:

- Memory: about 31 MB of transient Python heap and 3.8 MB kept. The process peak is about 89 MB, of which about
  55 MB is the interpreter with its imports before the layer loads anything.
- Frames: median 26 to 34 ms at zoom 8, the most expensive zoom, on this machine. The slowest of the 21 frames
  in each run reached 39 to 48 ms at zoom 8 (120 x 40), and 42 ms in the 200 x 60 run. Those worst frames are
  close to 50 ms, so the margin is thin for them, even though the typical frame is well below. The median is
  the figure that matches what the screen shows, so it is the one used for the decision; see the first
  candidate below if the worst frames become a problem.

So no code changed in `src/`, and no tests were added for an optimization. The benchmark stays in the repository so
the numbers can be repeated.

If the margin closes (a slower machine, a larger terminal, or a longer municipality list), the candidates in order
of expected gain are:

1. Douglas-Peucker simplification per zoom level, to cut the roughly 70,000 points drawn at zoom 8. This
   addresses the drawing part, which dominates there.
2. A bounding-box grid over the rings, to skip the 5 to 6 ms scan. This helps most at high zoom, where the scan is
   most of the frame.
3. Float32 arrays or tuples instead of one numpy array per ring. This would lower the 3.8 MB kept, which is already
   small, so it is the lowest priority.

Each one needs a visual equivalence check (same lit Braille cells, or a documented tolerance) before it is merged.

## Limits

- One machine, one operating system, one city and one terminal size per run. Other hardware will differ.
- RSS is the Windows working set. Windows does not return freed heap pages to the system straight away, which is
  why RSS after release (61 MB) stays above the start (55 MB).
- Each run downloads the map from the IBGE API. The figures are for the file served on 2026-11-14 (5,602 rings).
