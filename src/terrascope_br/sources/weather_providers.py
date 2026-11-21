"""Weather providers for Brazil (core entry point "terrascope.weather_providers").

* CgeProvider, for the municipality of São Paulo: the city's own weather
  centre (CGE, Centro de Gerenciamento de Emergências Climáticas). The current
  conditions come from its station at the Sé (centre of the city), the forecast
  from its extended forecast (today and the next two days, with the conditions
  of the night, morning, afternoon and evening). The CGE publishes HTML pages
  only, no API: they are read by their CSS classes.
* FederalForecastProvider, for every other municipality of Brazil (and for São
  Paulo when the CGE fails): the forecast of INMET by IBGE code (5 days), or of
  CPTEC/INPE (7 days) when INMET fails. Neither has current conditions without
  a key (the CPTEC airport readings answer "undefined"), so the core's next
  provider, Open-Meteo, gives those; the info box says so.

The municipality of a place is found in the IBGE outlines: the state from the
national state file (about 100 kB, the one the br_ufs layer keeps), then the
municipality from that state's own file in the coarsest quality, downloaded
the first time and kept for 30 days. The national municipality file is not
used. Everything here runs in the core's weather threads."""

from __future__ import annotations

import html
import json
import re
import threading
import time
import unicodedata
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import numpy as np

from terrascope.layers.weather.city_weather import WeatherFetchError
from terrascope.layers.weather.providers import WeatherProvider, WeatherReading

from terrascope_br import store
from terrascope_br.net import SourceError, get
from terrascope_br.sources import ibge

SAO_PAULO = "3550308"                  # IBGE code of the municipality of São Paulo
TIMEOUT = 20.0
CGE_FORECAST_URL = "https://www.cgesp.org/v3/previsao_estendida.jsp"
CGE_STATION_URL = "https://www.cgesp.org/v3/estacao.jsp?POSTO={station}"
CGE_STATION = "503"                    # "Sé - CGE", the station at the centre of the city
CGE_CURRENT_SECONDS = 10 * 60          # how long a CGE page is reused
CGE_FORECAST_SECONDS = 30 * 60
INMET_URL = "https://apiprevmet3.inmet.gov.br/previsao/{code}"
CPTEC_CITIES_URL = "https://servicos.cptec.inpe.br/XML/listaCidades?city={name}"
CPTEC_FORECAST_URL = "https://servicos.cptec.inpe.br/XML/cidade/7dias/{city}/previsao.xml"
CPTEC_IDS_FILE = "cptec_ids.json"
SAO_PAULO_TIME = ZoneInfo("America/Sao_Paulo")   # the CGE's periods and clock
BRAZIL_BOX = (-74.5, -34.5, -28.5, 5.6)           # west, south, east, north (with the islands)
# The coarsest IBGE outlines cut corners: a city on the coast or on an island (Santos,
# Vitória, Florianópolis) may fall just outside them. A place outside every outline
# takes the nearest one whose edge is within this distance (degrees, about 15 km).
NEAREST_TOLERANCE = 0.15

# ---- words -> WMO code (the core draws icons and colours from WMO codes) --------

# Most severe last: a day takes the code of its worst period.
_SEVERITY = (0, 1, 2, 3, 45, 51, 61, 71, 80, 95, 96)
_WORDS = (  # first match wins, so the specific phrases come first
    ("granizo", 96), ("tempestade", 95), ("trovoada", 95),
    ("pancada", 80), ("instavel", 80), ("neve", 71), ("chuvisco", 51), ("garoa", 51),
    ("chuva", 61), ("chuvoso", 61), ("nevoeiro", 45), ("neblina", 45),
    ("geada", 0),  # frost comes with clear, cold nights
    ("parcialmente nublado", 2), ("sol entre nuvens", 2), ("variacao de nebulosidade", 2),
    ("poucas nuvens", 1), ("predominio de sol", 1),
    ("muitas nuvens", 3), ("encoberto", 3), ("nublado", 3),
    ("ceu claro", 0), ("claro", 0), ("sol", 0),
)

def _plain(text: str) -> str:
    """Lower case, without accents, single spaces."""
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    return " ".join(text.lower().split())

def wmo_code(text: str) -> int | None:
    """The WMO code closest to a Portuguese description, or None."""
    plain = _plain(text)
    for word, code in _WORDS:
        if word in plain:
            return code
    return None

def _severity(code) -> int:
    return _SEVERITY.index(code) if code in _SEVERITY else -1

def _worst(texts: list[str]) -> tuple[int | None, str]:
    """(code, text) of the most severe description."""
    best: tuple[int | None, str] = (None, "")
    for text in texts:
        code = wmo_code(text)
        if best[0] is None or _severity(code) > _severity(best[0]):
            best = (code, text)
    return best

# CPTEC weather abbreviations (servicos.cptec.inpe.br/XML, "Siglas do tempo").
CPTEC_WEATHER = {
    "ec": "Encoberto com chuvas isoladas", "ci": "Chuvas isoladas", "c": "Chuva",
    "in": "Instável", "pp": "Possibilidade de pancadas de chuva", "cm": "Chuva pela manhã",
    "cn": "Chuva à noite", "pt": "Pancadas de chuva à tarde", "pm": "Pancadas de chuva pela manhã",
    "np": "Nublado e pancadas de chuva", "pc": "Pancadas de chuva", "pn": "Parcialmente nublado",
    "cv": "Chuvisco", "ch": "Chuvoso", "t": "Tempestade", "ps": "Predomínio de sol",
    "e": "Encoberto", "n": "Nublado", "cl": "Céu claro", "nv": "Nevoeiro", "g": "Geada",
    "ne": "Neve", "pnt": "Pancadas de chuva à noite", "psc": "Possibilidade de chuva",
    "pcm": "Possibilidade de chuva pela manhã", "pct": "Possibilidade de chuva à tarde",
    "pcn": "Possibilidade de chuva à noite", "npt": "Nublado com pancadas à tarde",
    "npn": "Nublado com pancadas à noite", "ncn": "Nublado com possibilidade de chuva à noite",
    "nct": "Nublado com possibilidade de chuva à tarde", "ncm": "Nublado com possibilidade de chuva pela manhã",
    "npm": "Nublado com pancadas pela manhã", "npp": "Nublado com possibilidade de chuva",
    "vn": "Variação de nebulosidade", "ct": "Chuva à tarde", "ppn": "Possibilidade de pancadas de chuva à noite",
    "ppt": "Possibilidade de pancadas de chuva à tarde", "ppm": "Possibilidade de pancadas de chuva pela manhã",
}

_COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")

def compass_degrees(text: str) -> float | None:
    """Degrees of a compass point ("SSE" -> 157.5; Portuguese "O" for west too)."""
    point = str(text or "").strip().upper().replace("O", "W")
    return _COMPASS.index(point) * 22.5 if point in _COMPASS else None

def _number(text) -> float | None:
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(text or ""))
    return float(match.group(0).replace(",", ".")) if match else None

def _text(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("latin-1")

def _fetch(url: str) -> bytes:
    try:
        return get(url, TIMEOUT, min_gap=1.0)[1]
    except SourceError as error:
        raise WeatherFetchError(str(error)) from error

# ---- where is a place: IBGE state, then municipality -----------------------------

_lock = threading.Lock()
_file_locks: dict[str, threading.Lock] = {}
_found: dict[tuple[float, float], str | None] = {}

def _file_lock(name: str) -> threading.Lock:
    with _lock:
        return _file_locks.setdefault(name, threading.Lock())

def _parse_coded(data: bytes) -> list[tuple[str, tuple[float, float, float, float], list[np.ndarray]]]:
    """(IBGE code, (west, south, east, north), rings) per feature of an IBGE outline file."""
    features = []
    for feature in json.loads(data.decode("utf-8")).get("features", []):
        code = str(((feature or {}).get("properties") or {}).get("codarea") or "")
        rings = ibge.rings_from_geojson({"features": [feature]})
        if code and rings:
            box = (min(r.min_lon for r in rings), min(r.min_lat for r in rings),
                   max(r.max_lon for r in rings), max(r.max_lat for r in rings))
            features.append((code, box, [ring.points for ring in rings]))
    return features

def _inside(rings: list[np.ndarray], lon: float, lat: float) -> bool:
    """Even-odd rule over every ring of a feature (holes included)."""
    inside = False
    for points in rings:
        x, y = points[:, 0], points[:, 1]
        x2, y2 = np.roll(x, -1), np.roll(y, -1)
        crosses = (y > lat) != (y2 > lat)
        with np.errstate(divide="ignore", invalid="ignore"):
            at = x + (lat - y) * (x2 - x) / (y2 - y)
        inside ^= bool(np.count_nonzero(crosses & (lon < at)) % 2)
    return inside

def _coded_file(file, url: str, refresh: bool = True):
    """The coded features of an outline file, downloaded first when missing (or, with
    `refresh`, when old). A file that cannot be read is removed, so the next call
    downloads it again."""
    with _file_lock(str(file)):
        if not file.exists() or (refresh and not ibge._is_fresh(file)):
            ibge._download(url, file)
        try:
            return ibge._cached(file, _parse_coded, tag="#coded")
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            file.unlink(missing_ok=True)
            raise ValueError(f"{file.name} unreadable: {error}") from error

def _edge_distance(rings: list[np.ndarray], lon: float, lat: float) -> float:
    """Distance in degrees from the place to the nearest edge of the rings."""
    nearest = float("inf")
    for points in rings:
        start, end = points[:-1], points[1:]
        segment = end - start
        length = np.einsum("ij,ij->i", segment, segment)
        place = np.array([lon, lat])
        with np.errstate(divide="ignore", invalid="ignore"):
            along = np.clip(np.einsum("ij,ij->i", place - start, segment) / length, 0.0, 1.0)
        along = np.nan_to_num(along)  # zero-length segments: their start point
        closest = start + segment * along[:, None]
        if len(closest):
            nearest = min(nearest, float(np.min(np.hypot(*(closest - place).T))))
    return nearest

def _codes_near(features, lon: float, lat: float) -> list[str]:
    """The feature containing the place first (when one does), then the others whose
    edge is within NEAREST_TOLERANCE, nearest first."""
    inside, near = [], []
    for code, (west, south, east, north), rings in features:
        if west <= lon <= east and south <= lat <= north and _inside(rings, lon, lat):
            inside.append(code)
        elif (west - NEAREST_TOLERANCE <= lon <= east + NEAREST_TOLERANCE
                and south - NEAREST_TOLERANCE <= lat <= north + NEAREST_TOLERANCE):
            distance = _edge_distance(rings, lon, lat)
            if distance <= NEAREST_TOLERANCE:
                near.append((distance, code))
    return inside[:1] + [code for _distance, code in sorted(near)]

def _code_at(features, lon: float, lat: float) -> str | None:
    """The feature containing the place; else the nearest one within NEAREST_TOLERANCE."""
    codes = _codes_near(features, lon, lat)
    return codes[0] if codes else None

def _strictly_in(features, lon: float, lat: float) -> str | None:
    """The feature containing the place, or None (no tolerance)."""
    for code, (west, south, east, north), rings in features:
        if west <= lon <= east and south <= lat <= north and _inside(rings, lon, lat):
            return code
    return None

def municipality_at(latitude: float, longitude: float) -> str | None:
    """IBGE code of the municipality at a place, or None outside Brazil. Raises
    WeatherFetchError when the outlines cannot be read."""
    west, south, east, north = BRAZIL_BOX
    if not (west <= longitude <= east and south <= latitude <= north):
        return None  # far from Brazil: no file is read or downloaded
    key = (round(latitude, 4), round(longitude, 4))
    if key in _found:
        return _found[key]
    try:
        # The br_ufs layer keeps this file fresh; here it is only downloaded when
        # missing, so the two never write it at the same time in normal use.
        states = _coded_file(store.path("ibge", "ufs.geojson"), ibge.MALHA_URL.format(level="UF"),
                             refresh=False)
        # The national state file is coarser than each state's municipality file, so
        # near a border the place may fall in the neighbour state: its municipality is
        # looked for in the state that contains it first, then in the states nearby.
        code = None
        nearest = None
        for state in _codes_near(states, longitude, latitude)[:3]:
            towns = _coded_file(
                store.path("ibge", f"mun-{state}-minima.geojson"),
                ibge.UF_MALHA_URL.format(code=state, level="&intrarregiao=municipio", quality="minima"),
            )
            code = _strictly_in(towns, longitude, latitude)
            if code is not None:
                break
            if nearest is None:
                nearest = _code_at(towns, longitude, latitude)
        code = code or nearest
    except (SourceError, OSError, ValueError) as error:
        raise WeatherFetchError(f"IBGE outlines: {error}") from error
    _found[key] = code
    return code

# ---- CGE (municipality of São Paulo) ---------------------------------------------

_pages: dict[str, tuple[float, str]] = {}

def _page(url: str, keep: float) -> str:
    hit = _pages.get(url)
    if hit is not None and time.monotonic() - hit[0] < keep:
        return hit[1]
    text = _text(_fetch(url))
    _pages[url] = (time.monotonic(), text)
    return text

def _strip(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())

PERIODS = ("Madrug.", "Manhã", "Tarde", "Noite")

def parse_cge_forecast(page: str) -> list[dict]:
    """The days of the CGE extended forecast, as the core's forecast days, each
    with its "periods" ([(period, conditions)]) and the worst of them as code and
    summary. Raises ValueError when the page has no day."""
    days = []
    for block in page.split('class="col-previsao"')[1:]:
        found = re.search(r'data-prev">\s*(\d{1,2})/(\d{1,2})<br/>\s*<span class="ano">(\d{4})', block)
        if not found:
            continue
        day, month, year = (int(group) for group in found.groups())
        periods = [
            (_strip(name), _strip(conditions))
            for name, conditions in re.findall(
                r'<div class="cond-tempo">\s*<h1>(.*?)</h1>\s*<h2>(.*?)</h2>', block, flags=re.S)
        ]
        code, summary = _worst([conditions for _name, conditions in periods])
        low = re.search(r'class="temp-min">([^<]*)<', block)
        high = re.search(r'class="temp-max">([^<]*)<', block)
        days.append({
            "date": f"{year:04d}-{month:02d}-{day:02d}",
            "code": code,
            "summary": summary,
            "tmin": _number(low.group(1)) if low else None,
            "tmax": _number(high.group(1)) if high else None,
            "precip": None, "pop": None, "wind": None, "gust": None,
            "sunrise": None, "sunset": None, "uv": None, "hours": [],
            "periods": periods,
        })
    if not days:
        raise ValueError("no day in the CGE forecast page")
    return days

def _station_value(page: str, label: str) -> str | None:
    """The value after `label` in the station tables, when the label is there once
    (an ambiguous label gives None, never another table's value)."""
    values = [
        _strip(value)
        for name, value in re.findall(r"<strong>(.*?)</strong></td>\s*<td[^>]*>(.*?)</td>", page, flags=re.S)
        if _strip(name).rstrip(":") == label
    ]
    return values[0] if len(values) == 1 else None

def _station_temperature(page: str) -> float | None:
    """The current temperature: the "Atual" row whose value is a temperature
    ("tempmedio"); the humidity, wind and pressure "Atual" rows are not. Exactly
    one such row, or None."""
    values = re.findall(
        r'<strong>Atual:</strong></td>\s*<td[^>]*>\s*<span class="tempmedio">([^<]*)<', page)
    return _number(values[0]) if len(values) == 1 else None

def period_now(hour: int) -> str:
    return PERIODS[min(3, max(0, hour // 6))]

def parse_cge_station(page: str, forecast: list[dict], now: datetime) -> dict:
    """Current conditions of a CGE station page, at `now` (São Paulo time). The
    conditions words come from the period of today's forecast (the station has no
    sky reading); a forecast that does not start today gives none. The page has no
    reading time, so `now` (the fetch) is given as the observation time. Raises
    ValueError without a temperature."""
    temperature = _station_temperature(page)
    if temperature is None:
        raise ValueError("no current temperature on the CGE station page")
    today = next((day for day in forecast if day.get("date") == now.date().isoformat()), None)
    periods = dict((today or {}).get("periods") or [])
    summary = periods.get(period_now(now.hour)) or (today or {}).get("summary")
    return {
        "temperature": temperature,
        "windspeed": _number(_station_value(page, "Velocidade")),
        "winddirection": compass_degrees(_station_value(page, "Direção")),
        "weathercode": wmo_code(summary) if summary else None,
        "summary": summary,
        "time": now.strftime("%Y-%m-%dT%H:%M"),
    }

class CgeProvider(WeatherProvider):
    """São Paulo (the municipality): the CGE of the city hall."""

    name = "CGE"
    priority = 200

    def covers(self, latitude: float, longitude: float) -> bool:
        return municipality_at(latitude, longitude) == SAO_PAULO

    def fetch(self, latitude: float, longitude: float) -> WeatherReading:
        forecast, current, errors = [], None, []
        try:
            forecast = parse_cge_forecast(_page(CGE_FORECAST_URL, CGE_FORECAST_SECONDS))
        except (WeatherFetchError, ValueError) as error:
            errors.append(f"forecast: {error}")
        try:
            page = _page(CGE_STATION_URL.format(station=CGE_STATION), CGE_CURRENT_SECONDS)
            current = parse_cge_station(page, forecast, datetime.now(SAO_PAULO_TIME))
        except (WeatherFetchError, ValueError) as error:
            errors.append(f"station: {error}")
        if current is None and not forecast:
            raise WeatherFetchError("; ".join(errors))
        return WeatherReading(current=current, forecast=forecast)

# ---- INMET and CPTEC (federal, every municipality) --------------------------------

def parse_inmet(payload: dict, code: str) -> list[dict]:
    """The days of an INMET forecast. The first days come in periods (morning,
    afternoon, night); the day takes the extremes and the worst conditions."""
    days = []
    for stamp, value in (payload.get(code) or {}).items():
        try:
            date = datetime.strptime(stamp, "%d/%m/%Y").date().isoformat()
        except ValueError:
            continue
        parts = [part for part in value.values() if isinstance(part, dict)] or [value]
        highs = [_number(part.get("temp_max")) for part in parts]
        lows = [_number(part.get("temp_min")) for part in parts]
        highs, lows = [x for x in highs if x is not None], [x for x in lows if x is not None]
        code_, summary = _worst([str(part.get("resumo") or "") for part in parts])
        days.append({
            "date": date, "code": code_, "summary": summary,
            "tmax": max(highs) if highs else None, "tmin": min(lows) if lows else None,
            "precip": None, "pop": None, "wind": None, "gust": None,
            "sunrise": None, "sunset": None, "uv": None, "hours": [],
        })
    days.sort(key=lambda day: day["date"])
    if not days:
        raise ValueError("no day in the INMET forecast")
    return days

def parse_cptec(xml: str) -> list[dict]:
    days = []
    for block in re.findall(r"<previsao>(.*?)</previsao>", xml, flags=re.S):
        field = dict(re.findall(r"<(\w+)>([^<]*)</\1>", block))
        summary = CPTEC_WEATHER.get(field.get("tempo", ""), "")
        days.append({
            "date": field.get("dia"), "code": wmo_code(summary), "summary": summary,
            "tmax": _number(field.get("maxima")), "tmin": _number(field.get("minima")),
            "uv": _number(field.get("iuv")),
            "precip": None, "pop": None, "wind": None, "gust": None,
            "sunrise": None, "sunset": None, "hours": [],
        })
    if not days:
        raise ValueError("no day in the CPTEC forecast")
    return days

def _known_cptec_ids(file) -> dict:
    try:
        known = json.loads((store.read_bytes(file) or b"{}").decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    return known if isinstance(known, dict) else {}

def _cptec_city(code: str) -> str:
    """The CPTEC id of an IBGE municipality, found by name and state once and kept
    ("" = CPTEC does not list it, also kept). The lock covers the file only, not
    the lookups on the network."""
    file = store.path("cptec", CPTEC_IDS_FILE)
    with _file_lock(str(file)):
        known = _known_cptec_ids(file)
    if code in known:
        if not known[code]:
            raise WeatherFetchError(f"CPTEC does not list municipality {code}")
        return known[code]
    try:
        ibge.ensure_municipality_names()
    except SourceError as error:
        raise WeatherFetchError(f"IBGE names: {error}") from error
    name, uf = ibge.municipality_names().get(code, ("", ""))
    if not name:
        raise WeatherFetchError(f"no IBGE name for {code}")
    xml = _text(_fetch(CPTEC_CITIES_URL.format(name=quote(_plain(name)))))
    city = next((found for found_name, found_uf, found in re.findall(
        r"<nome>([^<]*)</nome><uf>([^<]*)</uf><id>(\d+)</id>", xml)
        if _plain(found_name) == _plain(name) and found_uf == uf), "")
    with _file_lock(str(file)):
        known = _known_cptec_ids(file)
        known[code] = city
        try:
            store.write_bytes(file, json.dumps(known).encode("utf-8"))
        except OSError:
            pass  # not kept: looked up again next time
    if not city:
        raise WeatherFetchError(f"CPTEC does not list {name}/{uf}")
    return city

class FederalForecastProvider(WeatherProvider):
    """Every municipality of Brazil: INMET, or CPTEC/INPE when INMET fails.
    Forecast only."""

    name = "INMET"
    priority = 100

    def covers(self, latitude: float, longitude: float) -> bool:
        return municipality_at(latitude, longitude) is not None

    def fetch(self, latitude: float, longitude: float) -> WeatherReading:
        code = municipality_at(latitude, longitude)
        try:
            payload = json.loads(_text(_fetch(INMET_URL.format(code=code))))
            return WeatherReading(forecast=parse_inmet(payload, code), forecast_source="INMET")
        except (WeatherFetchError, ValueError, AttributeError) as inmet_error:
            try:
                xml = _text(_fetch(CPTEC_FORECAST_URL.format(city=_cptec_city(code))))
                return WeatherReading(forecast=parse_cptec(xml), forecast_source="CPTEC/INPE")
            except (WeatherFetchError, ValueError, SourceError, OSError) as cptec_error:
                raise WeatherFetchError(f"INMET: {inmet_error}; CPTEC: {cptec_error}") from cptec_error

def providers() -> list[WeatherProvider]:
    """Entry point of "terrascope.weather_providers"."""
    return [CgeProvider(), FederalForecastProvider()]
