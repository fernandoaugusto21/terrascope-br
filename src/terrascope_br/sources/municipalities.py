"""One place per municipality, for the city temperatures of the BRASIL tab.

From MARKER_MIN_ZOOM on (the zoom where the outlines switch to one file per
state), the br_global_temp layer labels the municipalities of the states in
view instead of the Natural Earth cities. Each municipality gets one point
inside its outline, worked out from the state's coarsest IBGE file: the same
file the weather providers use to find the municipality of a place, so the
point of a municipality always falls in that municipality. Names come from the
IBGE municipality list (municipios_nivelado.json).

The order decides which labels win where they would overlap: municipalities
with a Natural Earth city first, by that city's population, then the rest by
area. Downloads and parsing happen on the layer's refresh thread
(prepare_state); the UI thread only reads what is ready (ready_places)."""

from __future__ import annotations

import threading

import numpy as np

from terrascope_br import store
from terrascope_br.sources import ibge
from terrascope_br.sources.weather_providers import _code_at, _coded_file, _inside

MARKER_MIN_ZOOM = ibge.UF_DETAIL_ZOOM
SCAN_FRACTIONS = (0.5, 0.35, 0.65, 0.2, 0.8)  # heights (of the box) tried for a point inside

# state code -> (modification time of its file, places best first). Written by the
# refresh thread, read by the UI thread (one dict assignment per state).
_ready: dict[str, tuple[float, list[tuple[float, float, str, int | None, bool]]]] = {}
_lock = threading.Lock()


def minima_piece(state: str):
    """(cache file, URL) of the municipalities of one state, coarsest quality."""
    return (store.path("ibge", f"mun-{state}-minima.geojson"),
            ibge.UF_MALHA_URL.format(code=state, level="&intrarregiao=municipio", quality="minima"))


def ring_area(points: np.ndarray) -> float:
    """Area of a ring, in square degrees (shoelace)."""
    x, y = points[:, 0], points[:, 1]
    return abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))) / 2.0


def interior_point(rings: list[np.ndarray]) -> tuple[float, float]:
    """A point inside a feature (its rings, holes included): the centre of its
    biggest ring when that lies inside, otherwise the middle of the widest inside
    run of a horizontal line across the feature."""
    biggest = max(rings, key=ring_area)
    centre_lon, centre_lat = (float(value) for value in biggest[:-1].mean(axis=0)) if len(biggest) > 1 \
        else (float(biggest[0, 0]), float(biggest[0, 1]))
    if _inside(rings, centre_lon, centre_lat):
        return centre_lon, centre_lat
    south, north = float(biggest[:, 1].min()), float(biggest[:, 1].max())
    best: tuple[float, float, float] | None = None  # (width, lon, lat)
    for fraction in SCAN_FRACTIONS:
        lat = south + (north - south) * fraction
        crossings = []
        for points in rings:
            x, y = points[:, 0], points[:, 1]
            x2, y2 = np.roll(x, -1), np.roll(y, -1)
            crosses = (y > lat) != (y2 > lat)
            with np.errstate(divide="ignore", invalid="ignore"):
                at = x + (lat - y) * (x2 - x) / (y2 - y)
            crossings.extend(at[crosses].tolist())
        crossings.sort()
        for west, east in zip(crossings[0::2], crossings[1::2]):
            if best is None or east - west > best[0]:
                best = (east - west, (west + east) / 2.0, lat)
    if best is None:
        return centre_lon, centre_lat
    return best[1], best[2]


def build_places(features, cities) -> list[tuple[float, float, str, int | None, bool]]:
    """(lon, lat, IBGE code, population or None, has a capital) per municipality,
    best first. `features` as weather_providers._parse_coded gives them; `cities`
    are Natural Earth places (lon, lat, name, is_capital, population, country)
    that lend their population to the municipality they fall in."""
    population: dict[str, int] = {}
    capital: set[str] = set()
    for lon, lat, _name, is_capital, people, _country in cities:
        code = _code_at(features, lon, lat)
        if code is None:
            continue
        population[code] = max(population.get(code, 0), int(people or 0))
        if is_capital:
            capital.add(code)
    ranked = []
    for code, _box, rings in features:
        lon, lat = interior_point(rings)
        area = max(ring_area(points) for points in rings)
        ranked.append(((-population.get(code, -1), -area), (lon, lat, code, population.get(code), code in capital)))
    ranked.sort(key=lambda pair: pair[0])
    return [place for _rank, place in ranked]


def is_ready(state: str) -> bool:
    """True when the places of `state` match its file on disk."""
    file, _url = minima_piece(state)
    hit = _ready.get(state)
    try:
        return hit is not None and hit[0] == file.stat().st_mtime
    except OSError:
        return False


def prepare_state(state: str, cities) -> None:
    """Download (when missing or old) and read one state's municipalities, and
    work out their places. Runs on a refresh thread. Raises SourceError,
    OSError or ValueError."""
    file, url = minima_piece(state)
    with _lock:
        features = _coded_file(file, url)
        _ready[state] = (file.stat().st_mtime, build_places(features, cities))


def ready_places(state: str) -> list | None:
    """The places of a state, or None when they are not worked out yet."""
    hit = _ready.get(state)
    return hit[1] if hit is not None else None


_names: tuple[float, dict[str, tuple[str, str]]] | None = None


def names() -> dict[str, tuple[str, str]]:
    """IBGE code -> (name, UF), kept here while the file is unchanged: the parsed
    files of ibge.py are a small shared cache, and the labels ask for the names on
    every redraw. {} until the list is downloaded."""
    global _names
    file = store.path("ibge", ibge.NAMES_FILE)
    try:
        stamp = file.stat().st_mtime
    except OSError:
        return {}
    if _names is not None and _names[0] == stamp:
        return _names[1]
    value = ibge.municipality_names()
    if value:
        _names = (stamp, value)
    return value
