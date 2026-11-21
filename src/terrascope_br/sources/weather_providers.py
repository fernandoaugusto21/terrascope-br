"""Weather providers for Brazil (core entry point "terrascope.weather_providers").

* CgeProvider, for the municipality of São Paulo: the city's own weather
  centre (CGE, Centro de Gerenciamento de Emergências Climáticas). The current
  conditions come from its station at the Sé (centre of the city), the forecast
  from its extended forecast (today and the next two days, with the conditions
  of the night, morning, afternoon and evening). The CGE publishes HTML pages
  only, no API: they are read by their CSS classes.
* FederalProvider, for every other municipality of Brazil (and for São Paulo
  when the CGE fails): the forecast of INMET by IBGE code (5 days), or of
  CPTEC/INPE (7 days) when INMET fails; and the current conditions of the
  nearest INMET automatic station (see "INMET stations" below). When no station
  reading is close and recent enough, the core's next provider, Open-Meteo,
  gives the current conditions; the info box says which source answered.

INMET stations. With a personal key (token) of the INMET station API, set in
the environment variable TERRASCOPE_BR_INMET_TOKEN or in the file
<Terrascope config folder>/br/inmet_token.txt, the reading comes from the
station API (apitempo.inmet.gov.br): the automatic station nearest to the place,
from the station list. Without a key, or when INMET refuses it (HTTP 401/403, or
"CHAVE INVÁLIDA" with HTTP 200), the reading comes from the forecast service's
"nearest station" answer (apiprevmet3, by IBGE code), which needs no key. A
reading is used only from a station within STATION_MAX_KM of the place and at
most STATION_MAX_AGE old; otherwise the current conditions are left to the next
provider. A refused key is not sent again until the key changes.

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
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import numpy as np

from terrascope.core import config
from terrascope.layers.weather.city_weather import WeatherFetchError
from terrascope.layers.weather.providers import WeatherProvider, WeatherReading

from terrascope_br import store
from terrascope_br._i18n import N_, _
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
STATIONS_URL = "https://apitempo.inmet.gov.br/estacoes/T"          # automatic stations, no key
STATION_URL = "https://apitempo.inmet.gov.br/token/estacao/{start}/{end}/{station}/{token}"
NEAREST_STATION_URL = "https://apiprevmet3.inmet.gov.br/estacao/proxima/{code}"   # no key
TOKEN_ENV = "TERRASCOPE_BR_INMET_TOKEN"
TOKEN_FILE = ("br", "inmet_token.txt")   # under the Terrascope config folder
STATIONS_FILE = "inmet_estacoes.json"
STATIONS_SECONDS = 24 * 3600              # the station list is read again after a day
STATION_SECONDS = 10 * 60                 # a station answer is reused this long (several towns share one)
STATION_MAX_KM = 50.0                     # a station farther than this does not stand for the place
STATION_MAX_AGE = 3 * 3600                # an older reading is not "current"
INVALID_KEY_TEXT = "CHAVE INV"            # INMET's "CHAVE INVÁLIDA!" (sent with HTTP 200)
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

# The parts of the day as the forecast box shows them, by part number (hour // 6).
PART_NAMES = (N_("Overnight"), N_("Morning"), N_("Afternoon"), N_("Night"))
INMET_PARTS = {"manha": 1, "tarde": 2, "noite": 3}

def part_number(title: str) -> int | None:
    """Part of the day of a CGE title ("Madrug.", "Madrugada", "Manha"...), by its
    start, without accents; None when it is none of the four."""
    plain = _plain(title)
    for number, start in enumerate(("madrug", "manha", "tarde", "noite")):
        if plain.startswith(start):
            return number
    return None

def period(part: int, conditions: str) -> dict:
    """One part of the day for the core's forecast box ("periods" of a day)."""
    return {"name": _(PART_NAMES[part]), "code": wmo_code(conditions), "summary": conditions, "part": part}

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
        found_parts = [
            (_strip(name), _strip(conditions))
            for name, conditions in re.findall(
                r'<div class="cond-tempo">\s*<h1>(.*?)</h1>\s*<h2>(.*?)</h2>', block, flags=re.S)
        ]
        # A part whose title is not known is not drawn, but its conditions still count.
        periods = [period(part, conditions) for name, conditions in found_parts
                   if (part := part_number(name)) is not None]
        code, summary = _worst([conditions for _name, conditions in found_parts])
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
    parts = {part.get("part"): part.get("summary") for part in (today or {}).get("periods") or []
             if isinstance(part, dict)}
    summary = parts.get(min(3, max(0, now.hour // 6))) or (today or {}).get("summary")
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

def _clock(date: str, text) -> str | None:
    """"05h12" on a date -> "2026-11-21T05:12" (the core shows characters 11 to 16)."""
    found = re.fullmatch(r"\s*(\d{1,2})h(\d{2})\s*", str(text or ""))
    return f"{date}T{int(found.group(1)):02d}:{found.group(2)}" if found else None

def parse_inmet(payload: dict, code: str) -> list[dict]:
    """The days of an INMET forecast. The first days come in periods (morning,
    afternoon, night); the day takes the extremes and the worst conditions."""
    days = []
    for stamp, value in (payload.get(code) or {}).items():
        try:
            date = datetime.strptime(stamp, "%d/%m/%Y").date().isoformat()
        except ValueError:
            continue
        named = {name: part for name, part in value.items() if isinstance(part, dict)}
        parts = list(named.values()) or [value]
        highs = [_number(part.get("temp_max")) for part in parts]
        lows = [_number(part.get("temp_min")) for part in parts]
        highs, lows = [x for x in highs if x is not None], [x for x in lows if x is not None]
        code_, summary = _worst([str(part.get("resumo") or "") for part in parts])
        periods = [period(INMET_PARTS[name], str(part.get("resumo") or "").strip())
                   for name, part in named.items() if name in INMET_PARTS]
        periods.sort(key=lambda part: part["part"])
        days.append({
            "date": date, "code": code_, "summary": summary,
            "tmax": max(highs) if highs else None, "tmin": min(lows) if lows else None,
            "precip": None, "pop": None, "wind": None, "gust": None,
            "sunrise": _clock(date, parts[0].get("nascer")), "sunset": _clock(date, parts[0].get("ocaso")),
            "uv": None, "hours": [], "periods": periods,
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

# ---- INMET automatic stations (current conditions) --------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


class KeyRefused(Exception):
    """INMET refused the station API key (HTTP 401/403 or "CHAVE INVÁLIDA")."""


def token_file() -> Path | None:
    """<Terrascope config folder>/br/inmet_token.txt, or None with a core that does not say its folder."""
    folder = getattr(config, "USER_CONFIG_DIR", None)
    return Path(folder, *TOKEN_FILE) if folder is not None else None


def inmet_token() -> str:
    """The station API key: TERRASCOPE_BR_INMET_TOKEN, else the first line of
    token_file() that is not blank or a # comment; "" when neither is set."""
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        return token
    file = token_file()
    if file is None:
        return ""
    try:
        text = file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return next((line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")), "")


_refused: set[str] = set()    # keys INMET refused in this run (not sent again)


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def parse_station_reading(record: dict, now: datetime) -> dict | None:
    """The core's current conditions from one INMET station record (keys as the
    APIs send them: TEM_INS, VEN_VEL in m/s, VEN_DIR, DT_MEDICAO, HR_MEDICAO in
    UTC "HHMM"), or None without a temperature or when the reading is older than
    STATION_MAX_AGE (or in the future). The time is given in São Paulo time."""
    temperature = _number(record.get("TEM_INS"))
    if temperature is None:
        return None
    try:
        hour = str(record.get("HR_MEDICAO") or "").strip().zfill(4)
        observed = datetime.strptime(f"{record.get('DT_MEDICAO')} {hour}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    age = (now - observed).total_seconds()
    if not -600 <= age <= STATION_MAX_AGE:
        return None
    wind = _number(record.get("VEN_VEL"))
    return {
        "temperature": temperature,
        "windspeed": round(wind * 3.6, 1) if wind is not None else None,
        "winddirection": _number(record.get("VEN_DIR")),
        "weathercode": None, "summary": None,
        "time": observed.astimezone(SAO_PAULO_TIME).strftime("%Y-%m-%dT%H:%M"),
    }


def _stations() -> list[dict]:
    """The automatic stations (code, latitude, longitude, name), from a copy kept a day."""
    file = store.path("inmet", STATIONS_FILE)
    age = store.age(file)
    body = store.read_bytes(file) if age is not None and age < STATIONS_SECONDS else None
    if body is None:
        body = _fetch(STATIONS_URL)
        stations = _parse_stations(body)
        try:
            store.write_bytes(file, body)
        except OSError:
            pass
        return stations
    return _parse_stations(body)


def _parse_stations(body: bytes) -> list[dict]:
    try:
        items = json.loads(_text(body))
    except ValueError as error:
        raise WeatherFetchError(f"INMET station list unreadable: {error}") from error
    stations = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or str(item.get("CD_SITUACAO") or "Operante") != "Operante":
            continue
        lat, lon = _number(item.get("VL_LATITUDE")), _number(item.get("VL_LONGITUDE"))
        code = str(item.get("CD_ESTACAO") or "").strip()
        if code and lat is not None and lon is not None:
            stations.append({"code": code, "lat": lat, "lon": lon, "name": str(item.get("DC_NOME") or code)})
    return stations


def nearest_station(stations: list[dict], latitude: float, longitude: float) -> tuple[dict, float] | None:
    best = min(((distance_km(latitude, longitude, st["lat"], st["lon"]), st) for st in stations),
               key=lambda pair: pair[0], default=None)
    return (best[1], best[0]) if best is not None else None


def _keyed_reading(token: str, latitude: float, longitude: float, now: datetime) -> tuple[dict, str] | None:
    """(current conditions, station code) from the station API with a key, or None
    when no station is close enough or it has no recent reading. Raises
    KeyRefused, WeatherFetchError."""
    found = nearest_station(_stations(), latitude, longitude)
    if found is None or found[1] > STATION_MAX_KM:
        return None
    station = found[0]
    day = now.astimezone(timezone.utc).date()
    url = STATION_URL.format(start=(day - timedelta(days=1)).isoformat(), end=day.isoformat(),
                             station=station["code"], token=quote(token, safe=""))
    try:
        body = _page_bytes(url, STATION_SECONDS)
    except WeatherFetchError as error:
        if re.search(r"HTTP 40[13]\b", str(error)):
            raise KeyRefused(str(error)) from error
        raise
    text = _text(body).strip()
    if text.upper().startswith(INVALID_KEY_TEXT):
        raise KeyRefused(text)
    if not text:
        return None   # HTTP 204: no data for the period
    try:
        records = json.loads(text)
    except ValueError as error:
        raise WeatherFetchError(f"INMET station answer unreadable: {error}") from error
    for record in sorted((r for r in records if isinstance(r, dict)) if isinstance(records, list) else [],
                         key=lambda r: (str(r.get("DT_MEDICAO")), str(r.get("HR_MEDICAO")).zfill(4)), reverse=True):
        current = parse_station_reading(record, now)
        if current is not None:
            return current, station["code"]
    return None


def _keyless_reading(code: str, latitude: float, longitude: float, now: datetime) -> tuple[dict, str] | None:
    """(current conditions, station code) from the forecast service's nearest
    station for an IBGE municipality (no key), or None when it is too far or old."""
    try:
        answer = json.loads(_text(_page_bytes(NEAREST_STATION_URL.format(code=code), STATION_SECONDS)))
    except ValueError as error:
        raise WeatherFetchError(f"INMET nearest station unreadable: {error}") from error
    if not isinstance(answer, dict):
        return None
    station, record = answer.get("estacao") or {}, answer.get("dados") or {}
    if not isinstance(station, dict) or not isinstance(record, dict):
        return None
    lat, lon = _number(station.get("LATITUDE")), _number(station.get("LONGITUDE"))
    if lat is None or lon is None or distance_km(latitude, longitude, lat, lon) > STATION_MAX_KM:
        return None
    current = parse_station_reading(record, now)
    return (current, str(station.get("CODIGO") or record.get("CD_ESTACAO") or "")) if current else None


def station_reading(code: str, latitude: float, longitude: float, now: datetime | None = None) -> tuple[dict, str] | None:
    """The current conditions of the nearest INMET station: with the key when one
    is set and not refused, else (and when INMET refuses it) without it."""
    now = now or _now()
    token = inmet_token()
    if token and token not in _refused:
        try:
            return _keyed_reading(token, latitude, longitude, now)
        except KeyRefused:
            _refused.add(token)   # not sent again in this run; the keyless answer stands in
    return _keyless_reading(code, latitude, longitude, now)


_answers: dict[str, tuple[float, bytes]] = {}


def _page_bytes(url: str, keep: float) -> bytes:
    """GET with a short memory: several towns share a station."""
    hit = _answers.get(url)
    if hit is not None and time.monotonic() - hit[0] < keep:
        return hit[1]
    body = _fetch(url)
    _answers[url] = (time.monotonic(), body)
    return body


def _summary_now(forecast: list[dict], now: datetime) -> str | None:
    """The words of the running part of today's forecast (stations have no sky reading)."""
    local = now.astimezone(SAO_PAULO_TIME)
    today = next((day for day in forecast if day.get("date") == local.date().isoformat()), None)
    if today is None:
        return None
    parts = {part.get("part"): part.get("summary") for part in today.get("periods") or [] if isinstance(part, dict)}
    # INMET has no overnight part: the small hours take the night's words.
    return parts.get(min(3, max(0, local.hour // 6))) or parts.get(3) or today.get("summary")


class FederalProvider(WeatherProvider):
    """Every municipality of Brazil: the forecast of INMET, or CPTEC/INPE when INMET
    fails; the current conditions of the nearest INMET automatic station."""

    name = "INMET"
    priority = 100

    def covers(self, latitude: float, longitude: float) -> bool:
        return municipality_at(latitude, longitude) is not None

    def fetch(self, latitude: float, longitude: float) -> WeatherReading:
        code = municipality_at(latitude, longitude)
        forecast, forecast_source, errors = [], "", []
        try:
            payload = json.loads(_text(_fetch(INMET_URL.format(code=code))))
            forecast, forecast_source = parse_inmet(payload, code), "INMET"
        except (WeatherFetchError, ValueError, AttributeError) as inmet_error:
            try:
                xml = _text(_fetch(CPTEC_FORECAST_URL.format(city=_cptec_city(code))))
                forecast, forecast_source = parse_cptec(xml), "CPTEC/INPE"
            except (WeatherFetchError, ValueError, SourceError, OSError) as cptec_error:
                errors.append(f"INMET: {inmet_error}; CPTEC: {cptec_error}")
        current, current_source = None, ""
        now = _now()
        try:
            found = station_reading(code, latitude, longitude, now)
        except (WeatherFetchError, OSError) as station_error:
            errors.append(f"station: {station_error}")
            found = None
        if found is not None:
            current, station = found
            summary = _summary_now(forecast, now) if forecast_source == "INMET" else None
            if summary:
                current = {**current, "summary": summary, "weathercode": wmo_code(summary)}
            current_source = f"INMET {station}".strip()
        if current is None and not forecast:
            raise WeatherFetchError("; ".join(errors) or "no INMET data")
        return WeatherReading(current=current, forecast=forecast,
                              current_source=current_source, forecast_source=forecast_source)


FederalForecastProvider = FederalProvider   # the name of the first version (forecast only)


def providers() -> list[WeatherProvider]:
    """Entry point of "terrascope.weather_providers"."""
    return [CgeProvider(), FederalProvider()]
