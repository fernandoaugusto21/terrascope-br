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
| 16 | motorway, trunk | 47.8 ms | 54.4 ms | 91 | 0.43 MB |
| 64 | + primary, secondary | 190.4 ms | 198.1 ms | 981 | 1.15 MB |
| 256 | + tertiary | 200.8 ms | 208.1 ms | 4,179 | 1.10 MB |
| 512 | + local streets | 327.4 ms | 356.8 ms | 5,914 | 2.71 MB |
| 1024 | all (the tab's limit) | 106.7 ms | 128.4 ms | 6,062 | 1.53 MB |

The build time is the rasterisation of the visible polylines on the Python side (one `ImageDraw.line` per
polyline) plus the composition of the classes and the marker objects. It is paid once per view change, not
once per frame. A pan or a zoom step therefore costs about 0.1 to 0.35 s of build on this machine, which the
screen shows as a short pause. The markers are then drawn by the core, one cell each; that drawing is not
measured here (it needs a terminal; see the screenshots in the pull request).

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
- The build time per view change (0.1 to 0.35 s) is the main cost. A follow-up can cache the rasterised classes
  per tile, or vectorise the line drawing; it is not done in this change.
- The memory of the build itself (3.2 GB peak) is that of the generator on the full South-East extract, not of the
  plugin. The GitHub runner has 7 GB; the weekly workflow is not yet confirmed to finish there.
- The overpass route is not measured for CI: its public servers returned 502 and dropped connections during the
  tests, so it is only a slow fallback.

Reproduce: `python packaging/roads/build_roads.py --source pbf --pbf sudeste-latest.osm.pbf --out br-viario-rmsp.npz`,
then `python scripts/bench_viario.py --file br-viario-rmsp.npz` (needs psutil for the memory figures).
