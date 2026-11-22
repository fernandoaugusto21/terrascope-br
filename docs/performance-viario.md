# Cost of the road network layer (br_viario)

Measured on 2026-11-22, Windows 11, Python 3.13, on the file built from the Geofabrik extract
`sudeste-latest.osm.pbf` (864 MB, downloaded that day) by `packaging/roads/build_roads.py --source pbf`.
Numbers come from `scripts/bench_viario.py` (`--file` the built `.npz`), and from wall time and
process-tree memory of the build (psutil).

## The file

| | |
|---|---|
| Size on disk (npz, compressed) | 6.03 MiB |
| Build (`--source pbf`, one run) | 93.1 s: reading the extract 80.2 s, simplifying and writing 12.9 s |
| Build, second run (same input, same counts) | 89.5 s wall, 90.1 s measured |
| Peak memory of the build (process tree, RSS) | 3,215 MiB |
| Source | Geofabrik sudeste extract, OpenStreetMap, ODbL |

Roads kept: every way of the classes with a node in the box (`-47.21 -24.06 -45.69 -23.18`).
Ways and points before simplification, and the points kept per zoom range after it (range 0 = zoom 16-64,
1 = 64-256, 2 = 256-512, 3 = 512-1024; the tolerance is half a braille dot at the range's finest zoom):

| Class | Zooms | Ways | Points before | Points kept, by range |
|---|---|---:|---:|---|
| motorway | from 16 | 7,263 | 60,695 | 14,572 / 15,047 / 15,730 / 16,890 |
| trunk | from 16 | 6,165 | 45,301 | 12,337 / 12,465 / 12,738 / 13,302 |
| primary | from 64 | 16,040 | 120,993 | - / 32,565 / 33,238 / 34,617 |
| secondary | from 64 | 21,842 | 181,154 | - / 44,342 / 45,521 / 47,723 |
| tertiary | from 256 | 22,694 | 224,044 | - / - / 49,138 / 53,123 |
| residential (local streets) | from 512 | 206,152 | 1,849,254 | - / - / - / 482,753 |

Most of the file is local streets, and they are only stored at the finest range (33 m tolerance), because they
are only drawn from zoom 512 on.

## The layer, over São Paulo (per view change)

The layer builds its braille markers for a view when the view changes; the core draws them on every frame.
Each row is the median of 7 views (the centre moves 0.01 degrees between them), 108 x 58 cells (the BRASIL tab's
map area), with the file already read and the arrays of the zoom's ranges already prepared.

| Zoom | Classes drawn | Build, median | Build, slowest | Markers (cells) | Python heap peak of one build |
|---:|---|---:|---:|---:|---:|
| 16 | motorway, trunk | 2.1 ms | 3.8 ms | 91 | 1.18 MB |
| 64 | + primary, secondary | 6.2 ms | 10.9 ms | 982 | 3.37 MB |
| 256 | + tertiary | 10.4 ms | 17.9 ms | 4,191 | 2.92 MB |
| 512 | + local streets | 19.1 ms | 48.6 ms | 5,913 | 10.79 MB |
| 1024 | all (the tab's limit) | 11.5 ms | 41.5 ms | 6,058 | 3.71 MB |

The build rasterises every segment of a class in view at once with numpy (each segment sampled at least
once per pixel along its longer axis). The first version drew one `ImageDraw.line` per polyline and took
48 / 190 / 201 / 327 / 107 ms at the same zooms, mostly in the Python loop over tens of thousands of short
polylines; the cells it lit are the same in 98 to 100 % of the view, the rest differ by a dot. The build is
paid once per view change, not once per frame; the markers are then drawn by the core, one cell each.

### In the running application

Measured in the application itself (BRASIL tab, 130 x 45 terminal, over São Paulo, panning and zooming from
64 to 729, live data: CGE regions with their state fill, INMET warnings, Chuva Online radar, global rain and
temperatures), timing each map render and each frame drawn:

| Per pan or zoom step | Before | After |
|---|---:|---:|
| Map render, road layer off | 100 to 130 ms (CGE fill 85 to 110 ms) | 10 to 30 ms |
| Map render, road layer on | 290 to 335 ms | 25 to 40 ms |
| Whole frame (render and terminal output), road layer on | 300 to 390 ms | 60 to 120 ms |

The CGE regions and the INMET warnings share the test of which cells lie inside an area
(`inmet._inside_ring`); it now tests only the cells in each ring's bounding box, many edges at once, with
the same arithmetic as before (a test compares the two). What is left of a frame is mostly the terminal
output (`curses` refresh), in the core.

## Memory

| Step | Process RSS |
|---|---:|
| Before the file is read | 40.7 MB |
| After the file is opened and every class's arrays are prepared | 90.8 MB |
| After every zoom above | 84.2 MB |

Opening the file takes 0.012 s (metadata only); preparing all the arrays takes 0.06 s. The arrays of the
prepared classes stay in memory for the session: about 40 MB of the RSS above.

## Limits of these numbers

- One machine, one file, one view size (108 x 58 cells). Other terminal sizes change the number of cells
  rasterised but not the order of magnitude.
- The build per view change is now 2 to 20 ms (median); the line drawing is vectorised. The rest of a frame is the
  terminal output.
- The memory of the build itself (3.2 GB peak) is that of the generator on the full South-East extract, not of the
  plugin. The weekly workflow finished on a GitHub runner (7 GB) on 2026-11-22, in 89 s.
- The overpass route is not measured for CI: its public servers returned 502 and dropped connections during the
  tests, so it is only a slow fallback.

Reproduce: `python packaging/roads/build_roads.py --source pbf --pbf sudeste-latest.osm.pbf --out br-viario-rmsp.npz`,
then `python scripts/bench_viario.py --file br-viario-rmsp.npz` (needs psutil for the memory figures).
