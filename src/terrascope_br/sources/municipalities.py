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

# state code -> (length of the city list used, places best first). Written by the
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


def build_places(features, cities) -> list[tuple[float, float, str, int | None, bool, float]]:
    """(lon, lat, IBGE code, population or None, has a capital, area) per
    municipality, best first. `features` as weather_providers._parse_coded gives
    them; `cities` are Natural Earth places (lon, lat, name, is_capital,
    population, country) that lend their population to the municipality they fall
    in (None stays None: unknown, not zero)."""
    population: dict[str, int | None] = {}
    capital: set[str] = set()
    for lon, lat, _name, is_capital, people, _country in cities:
        code = _code_at(features, lon, lat)
        if code is None:
            continue
        if people is not None:
            population[code] = max(population.get(code) or 0, int(people))
        else:
            population.setdefault(code, None)
        if is_capital:
            capital.add(code)
    places = []
    for code, _box, rings in features:
        lon, lat = interior_point(rings)
        area = max(ring_area(points) for points in rings)
        places.append((lon, lat, code, population.get(code), code in capital, area))
    places.sort(key=rank)
    return places


def rank(place) -> tuple:
    """Sort key of a place: those with a known city population first (biggest
    first), then by area (biggest first). Also used to merge several states."""
    population, area = place[3], place[5]
    return (population is None, -(population or 0), -area)


def is_ready(state: str, city_count: int) -> bool:
    """True when the places of `state` were worked out with a city list of this
    length (the Natural Earth list may arrive after a first pass that had none).
    No file access: the UI thread asks on every tick."""
    hit = _ready.get(state)
    return hit is not None and hit[0] == city_count


def prepare_state(state: str, cities, download: bool = True) -> bool:
    """Read one state's municipalities (downloading the file when missing or old,
    unless `download` is False) and work out their places. Runs on a refresh
    thread. False when the file is not there and may not be downloaded. Raises
    SourceError, OSError or ValueError."""
    file, url = minima_piece(state)
    if not download and not file.exists():
        return False
    with _lock:
        features = _coded_file(file, url, refresh=download)
        _ready[state] = (len(cities), build_places(features, cities))
    return True


def ready_places(state: str) -> list | None:
    """The places of a state, or None when they are not worked out yet."""
    hit = _ready.get(state)
    return hit[1] if hit is not None else None


_names: dict[str, tuple[str, str]] = {}


def names() -> dict[str, tuple[str, str]]:
    """IBGE code -> (name, UF), as load_names() last read it; {} before. No file
    access: the labels ask on every redraw."""
    return _names


def load_names() -> bool:
    """Read the IBGE municipality list from the cache (refresh thread). False when
    it is not there."""
    global _names
    value = ibge.municipality_names()
    if value:
        _names = value
    return bool(value)
