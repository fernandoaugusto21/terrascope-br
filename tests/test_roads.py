"""The road network layer (br_viario): the file format, the zoom rule, the download and
cache (network mocked), the generator's pure parts, and what the layer draws."""

import importlib.util
import json
import os
import time
from pathlib import Path

import numpy as np
import pytest

from terrascope.core import config
from terrascope.core.view import View
from terrascope_br import roads_format as fmt
from terrascope_br import store
from terrascope_br.net import SourceError
from terrascope_br.sources import roads

ROOT = Path(__file__).resolve().parents[1]


def _build_module():
    spec = importlib.util.spec_from_file_location("build_roads", ROOT / "packaging" / "roads" / "build_roads.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_roads = _build_module()

SP_LON, SP_LAT = -46.63, -23.55


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "FETCH_NEW_DATA_FROM_API", True)
    return tmp_path


def _line(points):
    return np.asarray(points, dtype=np.float64)


def _polylines(lines):
    """Polylines of lon/lat arrays, as the file stores them."""
    if not lines:
        return fmt.Polylines(np.zeros((0, 2), dtype=np.int32), np.zeros(1, dtype=np.int32))
    xy = np.rint(np.concatenate(lines) * fmt.SCALE).astype(np.int32)
    sizes = [len(line) for line in lines]
    offsets = np.concatenate(([0], np.cumsum(sizes))).astype(np.int32)
    return fmt.Polylines(xy, offsets)


def _file_bytes(lines_by_class, tmp_path, built=None, coarse_only=False):
    """A road file in memory: lines_by_class maps a class name to its lon/lat polylines,
    which are written in every zoom range the class is drawn in (coarse_only: only the
    ranges of a file built before the finer ones)."""
    polylines = {}
    for name, _tags, _zoom in fmt.CLASSES:
        for index in fmt.ranges_of_class(name):
            if coarse_only and fmt.LOD_ZOOMS[index + 1] > fmt.COARSE_END:
                continue
            polylines[(name, index)] = _polylines(lines_by_class.get(name, []))
    target = tmp_path / "road-test.npz"
    meta = {"built": "2026-11-22T00:00:00Z", "built_epoch": int(built or time.time()), "stats": {}}
    fmt.write(target, polylines, meta)
    return target.read_bytes()


# -- the file format -----------------------------------------------------------------------

def test_format_round_trip_and_checks(tmp_path):
    data = _file_bytes({"motorway": [_line([(-46.7, -23.5), (-46.6, -23.5)])]}, tmp_path)
    meta = fmt.check(data)
    assert meta["format"] == fmt.FORMAT and meta["version"] == fmt.VERSION
    road = fmt.RoadFile(data)
    line = road.polylines("motorway", 0)
    assert line.count == 1
    assert np.allclose(line.degrees(), [[-46.7, -23.5], [-46.6, -23.5]], atol=1e-7)


def test_format_rejects_other_files_and_versions(tmp_path):
    other = tmp_path / "other.npz"
    np.savez(other, meta=np.array(json.dumps({"format": "something", "version": 1})))
    with pytest.raises(ValueError, match="not a br-viario"):
        fmt.check(other.read_bytes())
    wrong = tmp_path / "wrong.npz"
    np.savez(wrong, meta=np.array(json.dumps({"format": fmt.FORMAT, "version": 99})))
    with pytest.raises(ValueError, match="version"):
        fmt.check(wrong.read_bytes())


def test_format_rejects_a_file_without_every_class(tmp_path):
    polylines = {("motorway", 0): _polylines([_line([(0, 0), (1, 1)])])}
    target = tmp_path / "partial.npz"
    fmt.write(target, polylines, {})
    with pytest.raises(ValueError):
        fmt.check(target.read_bytes())


def test_validate_rejects_a_polyline_with_one_point():
    bad = fmt.Polylines(np.array([[1, 2]], dtype=np.int32), np.array([0, 1], dtype=np.int32))
    with pytest.raises(ValueError, match="fewer than 2"):
        fmt.validate_polylines(bad)


# -- zoom rule ---------------------------------------------------------------------------

@pytest.mark.parametrize("zoom, names", [
    (15.9, ()),
    (16, ("motorway", "trunk")),
    (63.9, ("motorway", "trunk")),
    (64, ("motorway", "trunk", "primary", "secondary")),
    (256, ("motorway", "trunk", "primary", "secondary", "tertiary")),
    (512, tuple(name for name, _tags, _zoom in fmt.CLASSES)),
    (1024, tuple(name for name, _tags, _zoom in fmt.CLASSES)),
])
def test_classes_by_zoom(zoom, names):
    assert fmt.classes_at(zoom) == names


@pytest.mark.parametrize("zoom, index", [
    (15.9, None), (16, 0), (63.9, 0), (64, 1), (255, 1), (256, 2), (511, 2), (512, 3), (1023.9, 3),
    (1024, 4), (2047, 4), (2048, 5), (4096, 5), (4097, None),
])
def test_zoom_ranges(zoom, index):
    assert fmt.range_index(zoom) == index


def test_each_class_is_stored_for_its_own_zoom_ranges_only():
    assert fmt.ranges_of_class("motorway") == (0, 1, 2, 3, 4, 5)
    assert fmt.ranges_of_class("primary") == (1, 2, 3, 4, 5)
    assert fmt.ranges_of_class("tertiary") == (2, 3, 4, 5)
    assert fmt.ranges_of_class("residential") == (3, 4, 5)


def test_tolerance_is_half_a_dot_at_the_finest_zoom_of_the_range():
    # One dot at zoom 1024 is about 66 m on the BRASIL tab's map (58 cell rows); half of it is the tolerance,
    # and each doubling of the zoom halves it: about 17 m at 2048 and 8 m at 4096.
    assert 30 < fmt.range_tolerance_metres(3) < 36
    assert 15 < fmt.range_tolerance_metres(4) < 18
    assert 7.5 < fmt.range_tolerance_metres(5) < 9
    assert fmt.range_tolerance_metres(0) > fmt.range_tolerance_metres(3) * 10
    assert fmt.range_tolerance_metres(5) < 10   # the finest range is finer than 10 m


def test_a_file_built_before_the_finer_ranges_still_reads_and_falls_back(tmp_path):
    data = _file_bytes({"motorway": [_line([(-46.7, -23.5), (-46.6, -23.5)])],
                        "residential": [_line([(-46.7, -23.6), (-46.6, -23.6)])]}, tmp_path, coarse_only=True)
    assert fmt.check(data)["format"] == fmt.FORMAT          # valid: the coarse ranges are all there
    road = fmt.RoadFile(data)
    assert not road.has("motorway", 4) and not road.has("motorway", 5)
    assert road.stored_range("motorway", 5) == 3            # the finest range the file has below 5
    assert road.stored_range("residential", 4) == 3
    assert road.stored_range("motorway", 2) == 2            # a range it has stays itself


def test_a_file_without_a_coarse_range_is_rejected(tmp_path):
    data = _file_bytes({"motorway": [_line([(-46.7, -23.5), (-46.6, -23.5)])]}, tmp_path)
    road = fmt.RoadFile(data)
    assert road.has("motorway", 4) and road.stored_range("motorway", 4) == 4
    polylines = {(name, index): _polylines([]) for name, _tags, _zoom in fmt.CLASSES
                 for index in fmt.ranges_of_class(name) if index != 2}
    target = tmp_path / "gap.npz"
    fmt.write(target, polylines, {})
    with pytest.raises(ValueError, match="zoom range 2"):
        fmt.check(target.read_bytes())


# -- the generator's pure parts ------------------------------------------------------------

def test_tile_boxes_cover_the_box_without_gaps():
    boxes = build_roads.tile_boxes((-47.21, -24.06, -45.69, -23.18), 0.2)
    assert len(boxes) == 40  # 8 columns of 0.2 degrees by 5 rows
    assert min(b[0] for b in boxes) == pytest.approx(-24.06)
    assert max(b[2] for b in boxes) == pytest.approx(-23.18)
    assert max(b[3] for b in boxes) == pytest.approx(-45.69)


def test_overpass_query_asks_for_the_road_classes_in_the_box():
    query = build_roads.overpass_query((-23.7, -46.8, -23.5, -46.6))
    assert "(-23.700000,-46.800000,-23.500000,-46.600000)" in query
    assert "out geom" in query
    assert "motorway_link" in query and "living_street" in query


def test_collect_ways_keeps_one_copy_per_id_and_only_road_classes():
    ways = {}
    element = {"type": "way", "id": 7, "tags": {"highway": "primary"},
               "geometry": [{"lat": -23.5, "lon": -46.6}, {"lat": -23.6, "lon": -46.7}]}
    build_roads.collect_ways([element, dict(element), {"type": "way", "id": 8, "tags": {"highway": "service"},
                                                        "geometry": element["geometry"]},
                              {"type": "node", "id": 9}], ways)
    assert list(ways) == [7]
    assert ways[7][0] == "primary" and ways[7][1].shape == (2, 2)


def test_simplify_keeps_the_ends_and_the_far_corner():
    xy = np.array([[0, 0], [1, 0.01], [2, 0], [3, 0], [4, 0], [5, 50], [6, 0]], dtype=float)
    kept = build_roads.simplify_indices(xy, tolerance=5.0)
    assert kept[0] == 0 and kept[-1] == len(xy) - 1
    assert 5 in kept          # the spike at x=5 is far from the chord
    assert 1 not in kept      # 0.01 is within the tolerance


def test_simplify_line_drops_repeated_points():
    points = np.array([[-46.6, -23.5], [-46.6, -23.5], [-46.61, -23.51]])
    assert len(build_roads.simplify_line(points, 1.0)) == 2


# -- download, release lookup and cache (network mocked) ----------------------------------

def _releases(*entries):
    return [{"tag_name": tag, "draft": draft, "assets": [{"name": name, "browser_download_url": url}]
             if name else []} for tag, draft, name, url in entries]


def test_asset_is_taken_from_the_newest_release_that_has_it():
    releases = _releases(
        ("windows-1", False, "terrascope-br.zip", "https://x/zip"),
        ("roads-2", False, fmt.ASSET_NAME, "https://x/roads-2"),
        ("roads-1", False, fmt.ASSET_NAME, "https://x/roads-1"),
    )
    assert roads.find_asset_url(releases) == "https://x/roads-2"


def test_drafts_and_releases_without_the_file_are_skipped():
    releases = _releases(
        ("roads-3", True, fmt.ASSET_NAME, "https://x/draft"),
        ("windows-2", False, "terrascope-br.zip", "https://x/zip"),
        ("roads-1", False, fmt.ASSET_NAME, "https://x/roads-1"),
    )
    assert roads.find_asset_url(releases) == "https://x/roads-1"
    with pytest.raises(SourceError, match="no release"):
        roads.find_asset_url(releases[:2])


@pytest.fixture
def network(monkeypatch, tmp_path):
    """Routes roads.get to a fake GitHub: the release list, then the file. Counts the calls."""
    calls = []
    state = {"body": _file_bytes({"motorway": [_line([(-46.7, -23.5), (-46.6, -23.5)])]}, tmp_path)}

    def fake_get(url, timeout, **kwargs):
        calls.append(url)
        if "api.github.com" in url:
            listing = [{"tag_name": "roads-1", "draft": False,
                        "assets": [{"name": fmt.ASSET_NAME, "browser_download_url": "https://dl/roads-1.npz"}]}]
            return 200, json.dumps(listing).encode("utf-8"), {}
        if url == "https://dl/roads-1.npz":
            return 200, state["body"], {}
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(roads, "get", fake_get)
    return calls, state


def _switched_on():
    layer = roads.RoadsLayer()
    layer.enabled = True
    layer.shown = True
    return layer


def test_layer_is_off_at_start_and_asks_for_nothing(cache, network):
    calls, _state = network
    layer = roads.RoadsLayer()
    layer.enabled = True
    assert layer.shown is False
    assert layer.is_due() is False
    assert layer.refresh_if_due(force=False) is False
    assert calls == []


def test_first_use_downloads_the_newest_file_and_then_works_offline(cache, network):
    calls, _state = network
    layer = _switched_on()
    assert layer.refresh_if_due(force=False) is True
    assert calls == ["https://api.github.com/repos/fernandoaugusto21/terrascope-br/releases?per_page=100",
                     "https://dl/roads-1.npz"]
    assert store.path("viario", "br-viario.npz").exists()
    layer.reload()
    assert layer.data_time is not None and layer._road is not None
    # Offline: the cache is read, nothing is asked.
    config.FETCH_NEW_DATA_FROM_API = False
    calls.clear()
    fresh = _switched_on()
    fresh.reload()
    assert fresh.refresh_if_due(force=True) is False
    assert calls == []
    assert fresh._road is not None


def test_a_fresh_cache_is_not_downloaded_again(cache, network):
    calls, _state = network
    layer = _switched_on()
    layer.refresh_if_due(force=False)
    calls.clear()
    assert layer.is_due() is False
    assert layer.refresh_if_due(force=False) is False
    assert calls == []


def test_a_week_old_cache_is_checked_again(cache, network, tmp_path):
    calls, state = network
    layer = _switched_on()
    layer.refresh_if_due(force=False)
    file = layer.stamp_file()
    old = time.time() - roads.CHECK_SECONDS - 60
    import os
    os.utime(file, (old, old))
    state["body"] = _file_bytes({"trunk": [_line([(-46.7, -23.4), (-46.6, -23.4)])]}, tmp_path,
                                built=time.time())
    calls.clear()
    assert layer.refresh_if_due(force=False) is True
    layer.reload()
    assert layer._road.polylines("trunk", 0).count == 1


def test_a_damaged_download_keeps_the_old_cache(cache, network):
    calls, state = network
    layer = _switched_on()
    layer.refresh_if_due(force=False)
    before = layer.stamp_file().read_bytes()
    old = time.time() - roads.CHECK_SECONDS - 60
    os.utime(layer.stamp_file(), (old, old))
    state["body"] = b"not an archive"
    with pytest.raises(SourceError, match="unreadable"):
        layer.refresh_if_due(force=False)
    assert layer.stamp_file().read_bytes() == before
    layer.reload()
    assert layer.data_time is not None   # the last good file is still read


def test_failed_download_is_said_in_the_legend(cache, monkeypatch):
    def down(url, timeout, **kwargs):
        raise SourceError("HTTP 503")

    monkeypatch.setattr(roads, "get", down)
    layer = _switched_on()
    with pytest.raises(SourceError):
        layer.refresh_if_due(force=True)
    layer.error = "HTTP 503"
    layer.reload()
    assert layer.status.startswith("unavailable") or layer.status.startswith("update failed")
    assert any("HTTP 503" in line.text for line in layer.legend_lines())


# -- what the layer draws ----------------------------------------------------------------

def _layer_with(tmp_path, cache, lines_by_class, built=None, coarse_only=False):
    store.write_bytes(store.path("viario", "br-viario.npz"),
                      _file_bytes(lines_by_class, tmp_path, built=built, coarse_only=coarse_only))
    layer = _switched_on()
    layer.reload()
    return layer


def _view(zoom, lon=SP_LON, lat=SP_LAT):
    return View(lon, lat, zoom, 108, 58)


def _cells(render):
    """The drawn cells, (x, y, colour, glyph), from the line layers (a core with
    LayerRender.line_layers) or from the markers (an older core)."""
    cells = [(marker.x, marker.y, marker.color, marker.glyph) for marker in render.markers]
    for lines, colour in getattr(render, "line_layers", None) or ():
        for y, row in enumerate(lines):
            cells += [(x, y, colour, char) for x, char in enumerate(row) if char != "⠀"]
    return cells


def _colours(cells):
    return {cell[2] for cell in cells}


def test_nothing_drawn_while_off(cache, tmp_path):
    layer = _layer_with(tmp_path, cache, {"motorway": [_line([(-46.7, -23.55), (-46.5, -23.55)])]})
    layer.shown = False
    assert _cells(layer.render(_view(512))) == []


def test_classes_are_drawn_from_their_zoom_only(cache, tmp_path):
    lines = {
        "motorway": [_line([(-46.75, -23.50), (-46.50, -23.50)])],
        "residential": [_line([(-46.75, -23.60), (-46.50, -23.60)])],
    }
    layer = _layer_with(tmp_path, cache, lines)
    at_256 = _cells(layer.render(_view(256)))
    assert _colours(at_256) == {roads.COLOURS["motorway"]}
    at_512 = _cells(layer.render(_view(512)))
    assert roads.COLOURS["residential"] in _colours(at_512)
    assert roads.COLOURS["motorway"] in _colours(at_512)


def test_a_class_has_its_own_colour_and_glyphs_are_braille(cache, tmp_path):
    layer = _layer_with(tmp_path, cache, {"motorway": [_line([(-46.70, -23.55), (-46.55, -23.55)])],
                                          "tertiary": [_line([(-46.70, -23.56), (-46.55, -23.56)])]})
    markers = _cells(layer.render(_view(512)))
    assert markers
    for cell in markers:
        assert "⠁" <= cell[3] <= "⣿"
    assert _colours(markers) == {roads.COLOURS["motorway"], roads.COLOURS["tertiary"]}


def test_only_the_roads_in_view_are_drawn(cache, tmp_path):
    far = _line([(-44.0, -22.0), (-43.9, -22.0)])       # far outside the view of São Paulo
    layer = _layer_with(tmp_path, cache, {"motorway": [far]})
    assert _cells(layer.render(_view(512))) == []
    near = _line([(-46.70, -23.55), (-46.55, -23.55)])
    layer = _layer_with(tmp_path, cache, {"motorway": [near, far]})
    rendered = _cells(layer.render(_view(512)))
    assert rendered and all(0 <= cell[0] < 108 and 0 <= cell[1] < 58 for cell in rendered)


def test_the_finer_zooms_draw_the_roads_of_their_own_ranges(cache, tmp_path):
    lines = {"motorway": [_line([(-46.70, -23.55), (-46.55, -23.55)])]}
    layer = _layer_with(tmp_path, cache, lines)
    for zoom in (1024, 2048, 4096):
        assert roads.COLOURS["motorway"] in _colours(_cells(layer.render(_view(zoom)))), zoom


def test_a_file_built_before_the_finer_ranges_draws_at_them_from_its_finest_range(cache, tmp_path):
    lines = {"motorway": [_line([(-46.70, -23.55), (-46.55, -23.55)])],
             "residential": [_line([(-46.70, -23.56), (-46.55, -23.56)])]}
    layer = _layer_with(tmp_path, cache, lines, coarse_only=True)
    assert layer._road is not None
    at_4096 = _cells(layer.render(_view(4096)))
    assert _colours(at_4096) == {roads.COLOURS["motorway"], roads.COLOURS["residential"]}
    assert _colours(_cells(layer.render(_view(1024)))) == _colours(at_4096)


def test_a_cell_shows_one_class_colour(cache, tmp_path):
    crossing = {"motorway": [_line([(-46.70, -23.55), (-46.55, -23.55)])],
                "residential": [_line([(-46.70, -23.55), (-46.55, -23.55)])]}
    layer = _layer_with(tmp_path, cache, crossing)
    markers = _cells(layer.render(_view(512)))
    cells = [(cell[0], cell[1]) for cell in markers]
    assert len(cells) == len(set(cells))     # one class per cell


def test_legend_lists_the_classes_and_is_switched_by_its_line(cache, tmp_path):
    layer = _layer_with(tmp_path, cache, {})
    layer.shown = False
    line = layer.legend_lines()[0]
    assert line.action == "br_viario.toggle"
    assert line.choice_index == 1            # off
    layer.press_legend("br_viario.toggle")
    assert layer.shown is True
    assert len(layer.legend_key()) == len(roads.COLOURS)


def test_hit_test_selects_nothing(cache, tmp_path):
    layer = _layer_with(tmp_path, cache, {"motorway": [_line([(-46.70, -23.55), (-46.55, -23.55)])]})
    layer.render(_view(512))
    assert layer.hit_test_all(3, 3) == []


def test_zoom_outside_the_layer_draws_nothing(cache, tmp_path):
    layer = _layer_with(tmp_path, cache, {"motorway": [_line([(-46.70, -23.55), (-46.55, -23.55)])]})
    assert _cells(layer.render(_view(4))) == []


def test_pbf_reader_keeps_the_roads_with_a_node_in_the_box(tmp_path):
    """Needs pyosmium (generator only, not a plugin dependency): skipped where it is missing."""
    osmium = pytest.importorskip("osmium")
    path = tmp_path / "small.osm.pbf"
    writer = osmium.SimpleWriter(str(path))
    writer.add_node(osmium.osm.mutable.Node(id=1, location=(-46.6, -23.5)))
    writer.add_node(osmium.osm.mutable.Node(id=2, location=(-46.55, -23.5)))
    writer.add_node(osmium.osm.mutable.Node(id=3, location=(-44.0, -22.0)))
    writer.add_node(osmium.osm.mutable.Node(id=4, location=(-44.1, -22.0)))
    writer.add_way(osmium.osm.mutable.Way(id=10, tags={"highway": "motorway"}, nodes=[1, 2]))
    writer.add_way(osmium.osm.mutable.Way(id=11, tags={"highway": "residential"}, nodes=[3, 4]))
    writer.add_way(osmium.osm.mutable.Way(id=12, tags={"highway": "service"}, nodes=[1, 2]))
    writer.close()
    ways = build_roads.read_pbf(path, (-47.21, -24.06, -45.69, -23.18), lambda message: None)
    assert list(ways) == [10]
    assert ways[10][0] == "motorway"
    assert ways[10][1].shape == (2, 2)


def _row_width(line):
    """Columns a legend row takes: each item is its glyph, a space and its label; items are two apart."""
    items = line.legend_items
    return sum(len(glyph) + 1 + len(label) for glyph, _colour, label in items) + 2 * (len(items) - 1)


@pytest.mark.parametrize("language", ["en", "pt_BR"])
def test_legend_rows_fit_a_narrow_width(cache, tmp_path, language):
    """The six classes wrap onto short rows (the credits line and the scale bar must stay clear)."""
    from terrascope.core import i18n

    i18n.activate(language)
    try:
        layer = roads.RoadsLayer()
        layer.shown = True
        layer.data_time = time.time()
        layer._has_drawn = True         # as after a frame with roads in view
        lines = layer.legend_lines()
        assert len(lines) == 4          # title, then three rows of two classes
        # The title line is the last line of the block when the sidebar is full (the core then stacks it
        # on the credits row), so it stays short: the credits (GeoSampa, pt-BR about 48 columns) must show.
        assert len(lines[0].text) <= 14, lines[0].text
        rows = lines[1:]
        assert sum(len(row.legend_items) for row in rows) == len(roads.COLOURS)
        assert all(_row_width(row) <= 32 for row in rows), [_row_width(row) for row in rows]
    finally:
        i18n.activate("en")


def test_with_line_layers_the_roads_are_lines_one_colour_per_class(cache, tmp_path):
    if not roads._LINE_LAYERS:
        pytest.skip("the core has no LayerRender.line_layers")
    layer = _layer_with(tmp_path, cache, {"motorway": [_line([(-46.70, -23.55), (-46.55, -23.55)])],
                                          "tertiary": [_line([(-46.70, -23.56), (-46.55, -23.56)])]})
    render = layer.render(_view(512))
    assert render.markers == []          # lines: the IBGE outlines drawn after this layer cover them
    assert [colour for _lines, colour in render.line_layers] == [roads.COLOURS["motorway"], roads.COLOURS["tertiary"]]
    assert all(len(lines) == 58 and all(len(row) == 108 for row in lines) for lines, _colour in render.line_layers)


def test_an_older_core_gets_markers(cache, tmp_path, monkeypatch):
    monkeypatch.setattr(roads, "_LINE_LAYERS", False)
    layer = _layer_with(tmp_path, cache, {"motorway": [_line([(-46.70, -23.55), (-46.55, -23.55)])]})
    render = layer.render(_view(512))
    assert render.markers and not getattr(render, "line_layers", None)


# ---- the area of the RMSP (generator) ----------------------------------------------

def _square(west, south, size):
    return [[west, south], [west + size, south], [west + size, south + size], [west, south + size], [west, south]]


def _mesh(*features):
    return {"features": [{"properties": {"codarea": str(code)}, "geometry": {"type": "Polygon", "coordinates": [ring]}}
                         for code, ring in features]}


def test_municipality_rings_takes_the_codes_asked_and_fails_on_a_missing_one():
    mesh = _mesh((1, _square(0, 0, 1)), (2, _square(1, 0, 1)), (3, _square(5, 5, 1)))
    assert len(build_roads.municipality_rings(mesh, [1, 2])) == 2
    with pytest.raises(SystemExit):
        build_roads.municipality_rings(mesh, [1, 4])


def test_area_mask_of_two_neighbours_is_their_union():
    # Two squares sharing an edge: their common border is counted twice by the even-odd
    # rule, which still leaves each one's inside (and so the union) inside.
    rings = build_roads.municipality_rings(_mesh((1, _square(0, 0, 1)), (2, _square(1, 0, 1))), [1, 2])
    bbox = (-0.5, -0.5, 2.5, 1.5)
    mask = build_roads.area_mask(rings, bbox, 0.1)
    def at(lon, lat):
        return bool(mask[int((bbox[3] - lat) / 0.1), int((lon - bbox[0]) / 0.1)])
    assert at(0.5, 0.5) and at(1.5, 0.5) and at(0.97, 0.5) and at(1.03, 0.5)
    assert not at(-0.3, 0.5) and not at(2.3, 0.5) and not at(1.0, 1.3)


def test_clip_ways_keeps_a_road_with_a_node_inside():
    rings = build_roads.municipality_rings(_mesh((1, _square(0, 0, 1))), [1])
    bbox = (-1.0, -1.0, 2.0, 2.0)
    mask = build_roads.area_mask(rings, bbox, 0.05)
    ways = {
        10: ("primary", np.array([[0.5, 0.5], [3.0, 0.5]])),     # starts inside, leaves the box
        11: ("primary", np.array([[-0.8, -0.8], [-0.6, -0.6]])),  # outside
        12: ("residential", np.array([[1.5, 1.5], [0.9, 0.9]])),  # ends inside
    }
    assert set(build_roads.clip_ways(ways, mask, bbox, 0.05)) == {10, 12}


def test_rings_box_adds_the_margin():
    rings = build_roads.municipality_rings(_mesh((1, _square(-47.0, -24.0, 1.0))), [1])
    assert build_roads.rings_box(rings, 0.01) == (-47.01, -24.01, -45.99, -22.99)


def test_the_rmsp_has_39_municipalities():
    assert len(build_roads.RMSP_CODES) == 39
    assert build_roads.RMSP_CODES[3550308] == "São Paulo"
