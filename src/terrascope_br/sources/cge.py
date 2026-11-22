"""CGE flood-risk states by region of the city of São Paulo (br_inundation_cge).

The CGE (Centro de Gerenciamento de Emergências Climáticas, of the city hall)
publishes the state of each of the 32 subprefeituras as a map image, with no
API: https://www.cgesp.org/v3/mapas.jsp?arq=alertas shows the image
/cgesp/mapa_sp_geoserver.png and an HTML image map with one circle per region
(its centre on the image and its name). Each region is painted in the colour
of its state:

    Observação (grey)  ->  normal watch
    Atenção (yellow)   ->  attention
    Iminência de transbordamento (orange)  ->  a stream about to overflow
    Alerta (red)       ->  alert

The state of a region is read from the colours around its circle. The time of
the data is the image's Last-Modified header (the time printed in the image is
not read).

The region outlines are the subprefeituras of GeoSampa (city hall's map portal,
WFS layer geoportal:subprefeitura), downloaded once and kept 30 days, with the
points thinned out to about 110 m (a braille dot is 260 m at the BRASIL tab's
closest zoom).

Drawn like the INMET warnings (same colours over the same tint rule): every
region gets its outline; a region in attention, imminence or alert gets the
colour of its state (tinted inside from TINT_ZOOM on) and a marker the info box
describes. Nothing is drawn below MIN_ZOOM, where the city is a few cells.

By region, too:
- the name of each subprefeitura, from LABEL_MIN_ZOOM on (clicking it opens the
  region in the info box);
- the CGE flood points of the day (alagamentos.jsp, read with the states): a
  region with active points gets a mark, red when one is impassable, yellow when
  they are passable, with their number next to the name;
- in the info box of a selected region, the reading of the CGE station of that
  region and the CGE forecast. The CGE publishes one forecast for the whole city,
  not one per region, and the info box says so. Both are fetched in the
  background when the region is selected, and kept 10 and 30 minutes."""

from __future__ import annotations

import html
import io
import json
import math
import re
import threading
import time
import unicodedata
from datetime import datetime
from email.utils import parsedate_to_datetime

import numpy as np
from PIL import Image

from terrascope.core import config
from terrascope.core.layer import CLICK_RADIUS, LayerRender, Marker, SidebarLine
from terrascope.core.view import View

from terrascope_br import store
from terrascope_br._i18n import N_, _
from terrascope_br.base import SourceLayer, key
from terrascope_br.geo import Ring, draw_outlines, view_key
from terrascope_br.net import SourceError, get
from terrascope_br.sources import inmet

PAGE_URL = "https://www.cgesp.org/v3/mapas.jsp?arq=alertas"
IMAGE_URL = "https://www.cgesp.org/cgesp/mapa_sp_geoserver.png"
REGIONS_URL = ("https://wms.geosampa.prefeitura.sp.gov.br/geoserver/geoportal/wfs?service=WFS&version=2.0.0"
               "&request=GetFeature&typeNames=geoportal:subprefeitura&outputFormat=application/json"
               "&srsName=EPSG:4326")
REGIONS_SECONDS = 30 * 24 * 3600
REGIONS_RETRY_SECONDS = 3600        # wait after a failed outline refresh (the old outlines stay)
THIN_DEGREES = 0.001                # about 110 m: a point closer than this to the last kept one is dropped
MIN_ZOOM = 16.0                     # the city of São Paulo is a few cells below this
CITY_BOX = (-46.83, -24.01, -46.36, -23.36)   # west, south, east, north of the city
OUTLINE_COLOR = "#8c98a4"
COLORED_SHARE = 0.15                # share of a circle's pixels that must carry a state's colour

# State: (colour on the map of this layer, label). Most severe last.
STATES = {
    "observacao": ("#9aa5b1", N_("watch")),
    "atencao": ("#ffd60a", N_("attention")),
    "iminencia": ("#ff9500", N_("overflow imminent")),
    "alerta": ("#ff3b30", N_("alert")),
}
ORDER = tuple(STATES)

# The CGE names the regions as on its image map; GeoSampa by acronym (sg_subprefeitura).
REGION_CODES = {
    "perus": "PR", "pirituba/jaragua": "PJ", "freguesia do o": "FO", "santana": "ST",
    "jacana/tremembe": "JT", "vila maria/vila guilherme": "MG", "casa verde": "CV", "lapa": "LA",
    "se": "SE", "mooca": "MO", "pinheiros": "PI", "butanta": "BT", "vila mariana": "VM",
    "ipiranga": "IP", "campo limpo": "CL", "santo amaro": "SA", "jabaquara": "JA",
    "cidade ademar": "AD", "m'boi mirim": "MB", "cidade dutra/socorro": "CS", "parelheiros": "PA",
    "vila prudente": "VP", "sapopemba": "SB", "aricanduva/vila formosa": "AF", "penha": "PE",
    "ermelino matarazzo": "EM", "sao miguel paulista": "MP", "itaquera": "IQ", "sao mateus": "SM",
    "itaim paulista": "IT", "guaianases": "GU", "cidade tiradentes": "CT",
}

AREA_TAG = re.compile(r"<area\b([^>]*)>", re.I)
ATTRIBUTE = re.compile(r"""([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
# Hue ranges (degrees) of the state colours. Mixing a colour with the grey or white
# of the map lowers its saturation but keeps its hue, so a half-strength fill is
# still read right; low saturation or a dark pixel is not a state.
MIN_SATURATION = 0.3
MIN_VALUE = 0.35
HUES = (("alerta", 0.0, 15.0), ("iminencia", 15.0, 48.0), ("atencao", 48.0, 75.0), ("alerta", 345.0, 360.0))
LEGEND_PIXELS = 20   # the image's legend has a swatch of each state: at least this many pixels each


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(text.lower().split())


def parse_areas(page: str) -> list[tuple[str, str, int, int, int]]:
    """(region code, CGE name, x, y, radius) of each circle of the image map. The
    attributes of each <area> are read in any order and quoting. Raises
    ValueError when no circle names a known region."""
    areas = []
    for attributes in AREA_TAG.findall(page):
        fields = {name.lower(): html.unescape(double or single).strip()
                  for name, double, single in ATTRIBUTE.findall(attributes)}
        code = REGION_CODES.get(_plain(fields.get("title", "")))
        numbers = re.findall(r"\d+", fields.get("coords", ""))
        if code and fields.get("shape", "circle").lower() == "circle" and len(numbers) == 3:
            x, y, radius = (int(number) for number in numbers)
            areas.append((code, fields["title"], x, y, radius))
    if not areas:
        raise ValueError("no known region on the CGE alert map")
    return areas


def pixel_states(rgb: np.ndarray) -> np.ndarray:
    """The state index (into ORDER) of each pixel of an (..., 3) RGB array: by hue
    for saturated, not too dark pixels (red, orange, yellow); 0 (watch) for grey,
    white, black and the blue of the rivers."""
    rgb = np.asarray(rgb, dtype=np.float64) / 255.0
    high, low = rgb.max(axis=-1), rgb.min(axis=-1)
    chroma = high - low
    saturation = np.divide(chroma, high, out=np.zeros_like(high), where=high > 0)
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        hue = np.where(high == red, ((green - blue) / chroma) % 6,
                       np.where(high == green, (blue - red) / chroma + 2, (red - green) / chroma + 4)) * 60.0
    coloured = (saturation >= MIN_SATURATION) & (high >= MIN_VALUE) & (chroma > 0)
    states = np.zeros(high.shape, dtype=np.int64)
    for name, start, end in HUES:
        states[coloured & (hue >= start) & (hue < end)] = ORDER.index(name)
    return states


def pixel_state(red: int, green: int, blue: int) -> str:
    """The state one pixel's colour stands for (see pixel_states)."""
    return ORDER[int(pixel_states(np.array([red, green, blue]))[()])]


def region_state(states: np.ndarray, x: int, y: int, radius: int) -> str:
    """The state of the region whose circle is at (x, y), from the per-pixel
    states of the image: the most common state colour inside the circle, when
    enough of it carries one (the region's name is printed there in black);
    watch otherwise."""
    height, width = states.shape
    ys, xs = np.mgrid[max(0, y - radius):min(height, y + radius + 1), max(0, x - radius):min(width, x + radius + 1)]
    inside = states[ys, xs][(xs - x) ** 2 + (ys - y) ** 2 <= radius * radius]
    counts = np.bincount(inside, minlength=len(ORDER))
    if counts[1:].sum() < max(1, inside.size) * COLORED_SHARE:
        return "observacao"
    best = max(range(1, len(ORDER)), key=lambda index: (counts[index], index))
    return ORDER[best]


def image_states(image_bytes: bytes) -> np.ndarray:
    """Per-pixel states of the alert image. Raises ValueError when it cannot be
    read or does not look like the CGE alert map (its legend has a swatch of each
    state colour), so a new look shows up as a failed update, not as all-watch."""
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
        rgb = np.asarray(image.convert("RGB"))
    except (OSError, ValueError) as error:
        raise ValueError(f"unreadable CGE map image: {error}") from error
    states = pixel_states(rgb)
    counts = np.bincount(states.ravel(), minlength=len(ORDER))
    if (counts[1:] < LEGEND_PIXELS).any():
        raise ValueError("the CGE map image has no legend with the state colours")
    return states


def read_states(page: str, image_bytes: bytes) -> dict[str, dict]:
    """Region code -> {"name", "state"} from the page and its image. Raises
    ValueError when either is not what the layer knows, or a circle of the page
    falls outside the image (the image was redrawn at another size)."""
    states = image_states(image_bytes)
    height, width = states.shape
    areas = parse_areas(page)
    if any(not (0 <= x < width and 0 <= y < height) for _code, _name, x, y, _radius in areas):
        raise ValueError("the CGE image map does not match the image size")
    return {code: {"name": name, "state": region_state(states, x, y, radius)}
            for code, name, x, y, radius in areas}


def thin(points: list, step: float = THIN_DEGREES) -> list:
    """The ring with points closer than `step` (degrees) to the last kept one
    dropped; the first and last points are kept."""
    if len(points) <= 4:
        return points
    kept = [points[0]]
    for point in points[1:-1]:
        if abs(point[0] - kept[-1][0]) >= step or abs(point[1] - kept[-1][1]) >= step:
            kept.append(point)
    kept.append(points[-1])
    return kept if len(kept) >= 4 else points


def compact_regions(payload: dict) -> dict:
    """The GeoSampa answer reduced to {code: {"name", "rings": [[[lon, lat], ...]]}}.
    Raises ValueError when no region is in it."""
    regions: dict[str, dict] = {}
    features = payload.get("features") if isinstance(payload, dict) else None
    for feature in features if isinstance(features, list) else []:
        if not isinstance(feature, dict) or not isinstance(feature.get("properties") or {}, dict):
            continue
        properties = feature.get("properties") or {}
        code = str(properties.get("sg_subprefeitura") or "").strip()
        geometry = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
        kind, coordinates = geometry.get("type"), geometry.get("coordinates") or []
        polygons = [coordinates] if kind == "Polygon" else coordinates if kind == "MultiPolygon" else []
        try:
            rings = [[[round(float(lon), 5), round(float(lat), 5)] for lon, lat, *_rest in thin(ring)]
                     for polygon in polygons for ring in polygon if len(ring) >= 4]
        except (TypeError, ValueError):
            continue
        if code and rings:
            regions[code] = {"name": str(properties.get("nm_subprefeitura") or code).title(), "rings": rings}
    if not regions:
        raise ValueError("no subprefeitura in the GeoSampa answer")
    return regions


# ---- flood points (alagamentos) and stations, by region ---------------------------
FLOODS_URL = "https://www.cgesp.org/v3/alagamentos.jsp"
STATIONS_URL = "https://www.cgesp.org/v3/estacoes-meteorologicas.jsp"
STATION_URL = "https://www.cgesp.org/v3/estacao.jsp?POSTO={post}"
STATIONS_SECONDS = 24 * 3600        # the station list rarely changes
STATION_SECONDS = 10 * 60           # a station page, as the weather provider keeps it

# Names the flood list may use that are not the image map's (the rest match REGION_CODES).
REGION_ALIASES = {
    "capela do socorro": "CS", "m boi mirim": "MB", "freguesia/brasilandia": "FO",
    "aricanduva/formosa/carrao": "AF", "aricanduva": "AF", "jacana": "JT", "tremembe": "JT",
}
# CGE station name (estacoes-meteorologicas.jsp) -> region. Stations outside the city
# (Riacho Grande, Mauá, Santana do Parnaíba) and the second Capela do Socorro station
# are left out; Casa Verde, Ermelino Matarazzo, Guaianases, Cidade Tiradentes and
# Sapopemba have no station of their own.
STATION_REGIONS = {
    "penha": "PE", "perus": "PR", "pirituba": "PJ", "freguesia do o": "FO", "santana/tucuruvi": "ST",
    "tremembe": "JT", "sao miguel paulista": "MP", "itaim paulista": "IT", "sao mateus": "SM",
    "se - cge": "SE", "butanta": "BT", "ipiranga": "IP", "santo amaro": "SA", "m boi mirim": "MB",
    "cidade ademar": "AD", "barragem parelheiros": "PA", "lapa": "LA", "campo limpo": "CL",
    "capela do socorro - subprefeitura": "CS", "vila formosa": "AF", "mooca": "MO", "itaquera": "IQ",
    "vila prudente": "VP", "vila maria / guilherme": "MG", "vila mariana": "VM", "jabaquara": "JA",
    "pinheiros": "PI",
}
# Flood point state, from the class of its icon: (active, passable).
FLOOD_CLASSES = {
    "ativo-transitavel": (True, True), "ativo-intransitavel": (True, False),
    "inativo-transitavel": (False, True), "inativo-intransitavel": (False, False),
}
# Zone of each region as the CGE flood list groups them; the list's own zone wins.
REGION_ZONES = {
    **dict.fromkeys(("SE",), N_("Centre")),
    **dict.fromkeys(("PR", "PJ", "FO", "CV", "ST", "JT", "MG"), N_("North zone")),
    **dict.fromkeys(("LA", "PI", "BT"), N_("West zone")),
    **dict.fromkeys(("VM", "IP", "JA", "SA", "CL", "MB", "AD", "CS", "PA"), N_("South zone")),
    **dict.fromkeys(("MO", "VP", "SB", "AF", "PE", "EM", "MP", "IQ", "SM", "IT", "GU", "CT"), N_("East zone")),
}
PASSABLE_COLOR = "#ffd60a"
IMPASSABLE_COLOR = "#ff3b30"
LABEL_COLOR = "#c9d1d9"
LABEL_MIN_ZOOM = 128.0              # the city is about 50 cells wide: room for the names
FLOOD_ROWS = 3                      # flood points listed in the info box


def region_code(name: str) -> str | None:
    """GeoSampa code of a region named as the CGE pages name it, or None."""
    plain = re.sub(r"\s*/\s*", "/", _plain(name))
    return REGION_CODES.get(plain) or REGION_ALIASES.get(plain)


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse_floods(page: str) -> dict[str, dict]:
    """Region code -> {"zone", "points": [{"active", "passable", "time", "street",
    "direction", "reference"}]} of the CGE flood list of a day. A day with no
    flood gives {}. Raises ValueError when the page is not the flood list."""
    if "tb-pontos-de-alagamentos" not in page and "alagamentos para essa data" not in page:
        raise ValueError("not the CGE flood list")
    regions: dict[str, dict] = {}
    zone = ""
    # The zone titles and the region tables come in page order.
    pattern = r'class="tit-bairros">(.*?)</h1>|<table class="tb-pontos-de-alagamentos">(.*?)</table>'
    for match in re.finditer(pattern, page, flags=re.S):
        if match.group(1) is not None:
            zone = _text(match.group(1))
            continue
        table = match.group(2)
        name = re.search(r'class="bairro[^"]*">(.*?)<hr', table, flags=re.S)
        code = region_code(_text(name.group(1))) if name else None
        if code is None:
            continue
        points = regions.setdefault(code, {"zone": zone, "points": []})["points"]
        for block in re.findall(r'<div class="ponto-de-alagamento">(.*?)</div>', table, flags=re.S):
            state = re.search(r'<li class="((?:in)?ativo-[a-z]+)"', block)
            active, passable = FLOOD_CLASSES.get(state.group(1) if state else "", (False, True))
            cells = [re.split(r"<br\s*/?>", cell)
                     for cell in re.findall(r'<li class="arial-descr-alag[^"]*">(.*?)</li>', block, flags=re.S)]
            first = cells[0] if cells else []
            when = _text(first[0]) if first else ""
            street = _text(first[1]) if len(first) > 1 else ""
            more = {}
            for line in cells[1] if len(cells) > 1 else []:
                label, _sep, value = _text(line).partition(":")
                more[_plain(label)] = value.strip()
            span = re.fullmatch(r"De (\S+) a (\S+)", when)
            points.append({
                "active": active, "passable": passable,
                "time": f"{span.group(1)}–{span.group(2)}" if span else when.removeprefix("De ").strip(),
                "street": street, "direction": more.get("sentido", ""), "reference": more.get("referencia", ""),
            })
    return regions


def parse_station_list(page: str) -> dict[str, str]:
    """Region code -> CGE station number, from the station list page."""
    found: dict[str, str] = {}
    for post, name in re.findall(r'estacao\.jsp\?POSTO=(\d+)"[^>]*>(.*?)</a>', page, flags=re.S):
        code = STATION_REGIONS.get(_plain(_text(name)))
        if code and code not in found:
            found[code] = post
    return found


def flood_summary(points: list[dict]) -> tuple[int, int, str | None]:
    """(active points, points of the day, marker colour or None): red when an
    active point is impassable, yellow when the active ones are passable."""
    active = [point for point in points if point["active"]]
    if not active:
        return 0, len(points), None
    colour = IMPASSABLE_COLOR if any(not point["passable"] for point in active) else PASSABLE_COLOR
    return len(active), len(points), colour


def _label_point(rings: list) -> tuple[float, float]:
    """A point inside the region for its name and marks (the centre of the biggest
    ring may fall outside a bent region)."""
    try:
        from terrascope_br.sources.municipalities import interior_point
        return interior_point([ring.points for ring in rings])
    except ImportError:   # a core without weather providers: the centre of the biggest ring
        biggest = max(rings, key=lambda ring: (ring.max_lon - ring.min_lon) * (ring.max_lat - ring.min_lat))
        centre = biggest.points[:-1].mean(axis=0) if len(biggest.points) > 1 else biggest.points[0]
        return float(centre[0]), float(centre[1])


def _number_text(value: float | None, unit: str) -> str:
    """12.5 -> "12,5 °C" in Portuguese (the core's regional format when it has one)."""
    if value is None:
        return ""
    try:
        from terrascope.core.i18n import format_number
    except ImportError:
        return f"{value:.1f}{unit}"
    return format_number(value, 1) + unit


def region_details(code: str, now: datetime | None = None) -> tuple[str, str]:
    """(station text, forecast text) of a region: the reading of its CGE station
    (temperature, rain since the station's last reset, wind), and the CGE forecast
    of today and tomorrow, which is the same for every region. A part that cannot
    be read says why in a few words."""
    try:
        from terrascope_br.sources import weather_providers as wp
    except ImportError:
        return _("needs a newer Terrascope"), _("needs a newer Terrascope")
    now = now or datetime.now(wp.SAO_PAULO_TIME)
    forecast: list[dict] = []
    try:
        forecast = wp.parse_cge_forecast(wp._page(wp.CGE_FORECAST_URL, wp.CGE_FORECAST_SECONDS))
    except (wp.WeatherFetchError, ValueError):
        pass
    station = _("no CGE station in this region")
    try:
        post = parse_station_list(wp._page(STATIONS_URL, STATIONS_SECONDS)).get(code)
        if post:
            page = wp._page(STATION_URL.format(post=post), STATION_SECONDS)
            reading = wp.parse_cge_station(page, forecast, now)
            rain = wp._number(wp._station_value(page, "Per. Atual"))
            parts = [_number_text(reading["temperature"], " °C")]
            if rain is not None:
                parts.append(_("rain %s") % _number_text(rain, " mm"))
            if reading.get("windspeed") is not None:
                parts.append(_("wind %s") % _number_text(reading["windspeed"], " km/h"))
            station = " · ".join(part for part in parts if part)
    except (wp.WeatherFetchError, ValueError):
        station = _("station unavailable")
    days = [day for day in forecast if day.get("date", "") >= now.date().isoformat()][:2]
    texts = []
    for name, day in zip((_("today"), _("tomorrow")), days):
        low, high = day.get("tmin"), day.get("tmax")
        span = f" {low:.0f}–{high:.0f} °C" if low is not None and high is not None else ""
        texts.append(f"{name}: {day.get('summary') or '—'}{span}")
    forecast_text = "; ".join(texts) + " " + _("(whole city: the CGE gives no forecast by region)") if texts \
        else _("forecast unavailable")
    return station, forecast_text


def _modified(headers: dict) -> float:
    """Epoch of a Last-Modified header, or now when there is none."""
    for name, value in (headers or {}).items():
        if name.lower() == "last-modified":
            try:
                return parsedate_to_datetime(value).timestamp()
            except (TypeError, ValueError):
                break
    return time.time()


class CgeAlertsLayer(inmet.InmetAlertsLayer):
    # Named so that it sorts after br_inmet_alerts and before br_radar (the draw order).
    name = "br_inundation_cge"
    # Every letter is taken by the app, the built-in layers or the other BR layers:
    # this layer is switched on its legend line only.
    toggle_key = ""
    switch_action = "br_inundation_cge.toggle"
    source_name = _("CGE flood risk, São Paulo city")
    info_title = _("CGE REGION")
    credit = "CGE"   # in the tab's credits; GeoSampa is named by credits() while the outlines show
    refresh_seconds = 300
    stale_after_seconds = 3600

    def __init__(self) -> None:
        super().__init__()
        self._outline_key = None
        self._outline_lines: list[str] = []
        self.regions: list[dict] = []   # every region, with its state (items: the ones in a state)
        self._regions_retry_at = 0.0
        self._generation = 0            # counts parses: the cached drawings belong to one of them
        self._drawn = False             # the outlines were drawn in the last frame
        self.floods_time: float | None = None
        self._details: dict[str, dict] = {}   # region code -> {"station", "forecast", "time"}
        self._details_lock = threading.Lock()
        self._details_loading: set[str] = set()

    # -- files ----------------------------------------------------------------------
    def stamp_file(self):
        return store.path("cge", "alertas.json")

    def regions_file(self):
        return store.path("cge", "subprefeituras.json")

    def _refresh_regions(self) -> None:
        """Download the outlines when missing or 30 days old. A failure keeps the
        outlines in the cache (raises only when there are none) and is not tried
        again for REGIONS_RETRY_SECONDS, so the states keep updating."""
        age = store.age(self.regions_file())
        if age is not None and (age < REGIONS_SECONDS or time.monotonic() < self._regions_retry_at):
            return
        try:
            _status, body, _headers = get(REGIONS_URL, 60.0, min_gap=1.0)
            compact = compact_regions(json.loads(body.decode("utf-8")))
        except (SourceError, UnicodeDecodeError, ValueError, TypeError, AttributeError) as error:
            self._regions_retry_at = time.monotonic() + REGIONS_RETRY_SECONDS
            if age is None:
                raise SourceError(f"GeoSampa regions: {error}") from error
            return
        store.write_bytes(self.regions_file(), json.dumps(compact).encode("utf-8"))

    def fetch(self) -> None:
        self._refresh_regions()
        previous: dict = {}
        try:
            loaded = json.loads((store.read_bytes(self.stamp_file()) or b"{}").decode("utf-8"))
            previous = loaded if isinstance(loaded, dict) else {}
        except (UnicodeDecodeError, ValueError):
            pass
        floods = {key: previous[key] for key in ("floods", "floods_time") if key in previous}
        try:
            _status, flood_page, _headers = get(FLOODS_URL, 30.0, min_gap=1.0)
            floods = {"floods": parse_floods(flood_page.decode("utf-8", "replace")), "floods_time": time.time()}
        except (SourceError, ValueError) as error:
            # The states still update; the last flood list stays, with its own time.
            floods["floods_error"] = str(error)
        since = previous.get("last_modified") if previous.get("regions") else None
        status, image, headers = get(IMAGE_URL, 30.0, min_gap=1.0, if_modified_since=since)
        if status == 304:
            # The same image: nothing to read again; the file's new age says it was checked.
            previous.pop("floods_error", None)
            store.write_bytes(self.stamp_file(), json.dumps({**previous, **floods}, ensure_ascii=False).encode("utf-8"))
            return
        _status, page, _headers = get(PAGE_URL, 30.0, min_gap=1.0)
        try:
            states = read_states(page.decode("utf-8", "replace"), image)
        except ValueError as error:
            raise SourceError(f"CGE alert map: {error}") from error
        modified = next((value for name, value in (headers or {}).items() if name.lower() == "last-modified"), None)
        store.write_bytes(self.stamp_file(), json.dumps(
            {"time": _modified(headers), "last_modified": modified, "regions": states, **floods},
            ensure_ascii=False).encode("utf-8"))

    def parse(self):
        data = store.read_bytes(self.stamp_file())
        shapes = store.read_bytes(self.regions_file())
        if data is None or shapes is None:
            self.regions = []
            return [], None
        document = json.loads(data.decode("utf-8"))
        outlines = json.loads(shapes.decode("utf-8"))
        floods = document.get("floods") if isinstance(document.get("floods"), dict) else {}
        cge_names = {code: str(region.get("name") or "").strip()
                     for code, region in (document.get("regions") or {}).items() if isinstance(region, dict)}
        floods_time = document.get("floods_time")
        self.floods_time = float(floods_time) if isinstance(floods_time, (int, float)) else None
        regions = []
        for code, shape in outlines.items():
            rings = [Ring(points) for points in shape["rings"] if len(points) >= 2]
            if not rings:
                continue
            state = (document.get("regions") or {}).get(code, {}).get("state", "observacao")
            state = state if state in STATES else "observacao"
            lon, lat = _label_point(rings)
            flood = floods.get(code) if isinstance(floods.get(code), dict) else {}
            regions.append({
                # The CGE's own name (accents and all) when its image map gave one.
                "id": f"cge-{code}", "code": code, "region": cge_names.get(code) or shape.get("name") or code,
                "state": state, "color": STATES[state][0], "rings": rings,
                "lon": lon, "lat": lat,
                "zone": str(flood.get("zone") or "") or (_(REGION_ZONES[code]) if code in REGION_ZONES else ""),
                "floods": [point for point in flood.get("points") or [] if isinstance(point, dict)],
                "area": sum((ring.max_lon - ring.min_lon) * (ring.max_lat - ring.min_lat) for ring in rings),
                # what InmetAlertsLayer's legend reads
                "severity": _(STATES[state][1]), "event": _(STATES[state][1]),
            })
        self.regions = regions
        self._generation += 1
        self._cells_key = None          # the inherited warning cells are drawn again too
        flagged = [region for region in regions if region["state"] != "observacao"]
        flagged.sort(key=lambda region: ORDER.index(region["state"]))  # most severe drawn last
        return flagged, float(document.get("time") or 0) or None

    def clear(self) -> None:
        super().clear()
        self._outline_key = None
        self._outline_lines = []
        self._drawn = False

    # -- drawing ----------------------------------------------------------------------
    def _in_view(self, view: View) -> bool:
        west, south, east, north = CITY_BOX
        return (view.zoom >= MIN_ZOOM and not (east < view.origin_lon or west > view.origin_lon + view.lon_span
                or north < view.origin_lat - view.lat_span or south > view.origin_lat))

    def build(self, view: View) -> LayerRender:
        self._drawn = bool(self.regions) and self._in_view(view)
        if not self._drawn:
            return LayerRender(None, OUTLINE_COLOR)
        outline_key = (view_key(view), self._generation)
        if outline_key != self._outline_key:
            self._outline_lines = draw_outlines([ring for region in self.regions for ring in region["rings"]], view)
            self._outline_key = outline_key
        if self.data_time is None or self.is_stale() or not self.items:
            # Old states are not shown as current; names and flood points still are.
            return LayerRender(self._outline_lines, OUTLINE_COLOR, markers=self._region_markers(view, set()))
        flagged = super().build(view)
        marked = {region["code"] for region in self.items}
        markers = [*flagged.markers, *self._region_markers(view, marked)]
        return LayerRender(self._outline_lines, OUTLINE_COLOR, markers=markers, bg_cells=flagged.bg_cells)

    def _floods_current(self) -> bool:
        return self.floods_time is not None and time.time() - self.floods_time <= self.stale_after_seconds

    def _region_markers(self, view: View, marked: set[str]) -> list[Marker]:
        """Each region's name from LABEL_MIN_ZOOM on, biggest region first, a name
        that would cover another one left out; and the number of active flood
        points after it (below that zoom, a mark alone). `marked` are the regions
        with a state marker at their point: their text starts after it."""
        floods_current = self._floods_current()
        named = view.zoom >= LABEL_MIN_ZOOM
        taken: set[tuple[int, int]] = set()
        markers: list[Marker] = []
        for region in sorted(self.regions, key=lambda region: -region["area"]):
            cell = view.to_cell(region["lon"], region["lat"])
            if cell is None:
                continue
            active, _today, colour = flood_summary(region["floods"]) if floods_current else (0, 0, None)
            if not named and colour is None:
                continue
            x = cell[0] + (2 if region["code"] in marked else 0)
            name = region["region"] if named else ""
            badge = f"≈{active}" if colour is not None else ""
            span = {(x + dx, cell[1]) for dx in range(len(name) + (1 if name and badge else 0) + len(badge))}
            if span & taken:
                continue
            taken |= span
            selected = self._is_selected(region)
            if name:
                markers.append(Marker(x, cell[1], LABEL_COLOR, label=name, item=region, selected=selected))
            if badge:
                start = x + len(name) + (1 if name else 0)
                markers.append(Marker(start, cell[1], colour, label=badge, item=region, selected=selected))
        return markers

    def hit_test_all(self, x: int, y: int, radius: float | None = None):
        """A click anywhere along a region's name or flood mark selects it."""
        if radius is None:
            radius = CLICK_RADIUS
        hits = []
        for marker in self.last_markers:
            if marker.item is None:
                continue
            span = len(marker.label) if marker.label else 1
            dx = max(marker.x - x, 0, x - (marker.x + span - 1))
            distance = math.hypot(dx, marker.y - y)
            if distance <= radius:
                hits.append((distance, marker.item))
        hits.sort(key=lambda hit: hit[0])
        seen, unique = set(), []
        for distance, item in hits:      # a name and its mark are one region
            if id(item) not in seen:
                seen.add(id(item))
                unique.append((distance, item))
        return unique

    def credits(self) -> list[str]:
        """The outlines' source and licence while they are drawn (see the core's
        Layer.credits): GeoSampa's licence, CC BY-SA 4.0, asks for both to be
        named. The licence comes first, so a narrow map cuts the rest. The CGE is
        in the tab's credits already."""
        if not (self.enabled and self.shown and self._drawn):
            return []
        return [_("GeoSampa (CC BY-SA 4.0), City of São Paulo")]

    def _flip(self) -> None:
        super()._flip()
        self._drawn = False   # set again by the next build() when switched back on

    # -- legend and info box ---------------------------------------------------------
    def legend_key(self):
        return key(*((colour, _(label)) for colour, label in STATES.values()),
                   (PASSABLE_COLOR, _("≈ flood, passable")), (IMPASSABLE_COLOR, _("≈ flood, impassable")))

    def legend_lines(self) -> list[SidebarLine]:
        # The base layer's: title with its on/off switch, then the key or what is wrong.
        return SourceLayer.legend_lines(self)

    def info_rows(self) -> list[tuple[str, str]]:
        region = self.selected
        if not region:
            return []
        updated = time.strftime("%d/%m %H:%M", time.localtime(self.data_time)) if self.data_time else _("N/A")
        rows = [(_("REGION"), region["region"])]
        if region.get("zone"):
            rows.append((_("ZONE"), region["zone"]))
        rows.append((_("STATE"), _(STATES[region["state"]][1])))
        rows.extend(self._flood_rows(region))
        details = self._region_details(region["code"])
        rows.append((_("STATION"), details.get("station") or _("loading…")))
        rows.append((_("FORECAST"), details.get("forecast") or _("loading…")))
        rows.append((_("UPDATED"), updated))
        rows.append((_("SOURCE"), "CGE"))
        return rows

    def _flood_rows(self, region: dict) -> list[tuple[str, str]]:
        if not self._floods_current():
            return [(_("FLOODS"), _("N/A"))]
        points = region.get("floods") or []
        active, today, _colour = flood_summary(points)
        if not today:
            return [(_("FLOODS"), _("none today"))]
        rows = [(_("FLOODS"), _("%(active)d active · %(today)d today") % {"active": active, "today": today})]
        # Active points first, then the latest of the day.
        ordered = sorted(points, key=lambda point: (not point["active"], point["time"]), reverse=False)
        for point in ordered[:FLOOD_ROWS]:
            mark = _("impassable") if not point["passable"] else _("passable")
            when = point["time"] + ("" if point["active"] else " " + _("(over)"))
            rows.append(("", f"{when} {point['street']} ({mark})"))
        return rows

    # -- station and forecast of a selected region (background) -------------------------
    def _region_details(self, code: str) -> dict:
        """What the info box shows for the region's station and the forecast; asks
        for them in the background when missing or 10 minutes old."""
        with self._details_lock:
            details = self._details.get(code)
            due = details is None or time.time() - details["time"] >= STATION_SECONDS
            if due and code not in self._details_loading and config.FETCH_NEW_DATA_FROM_API:
                self._details_loading.add(code)
                threading.Thread(target=self._load_details, args=(code,), name="terrascope-br-cge-region",
                                 daemon=True).start()
        if details is None and not config.FETCH_NEW_DATA_FROM_API:
            return {"station": _("offline"), "forecast": _("offline")}
        return details or {}

    def _load_details(self, code: str) -> None:
        try:
            station, forecast = region_details(code)
            with self._details_lock:
                self._details[code] = {"station": station, "forecast": forecast, "time": time.time()}
        finally:
            with self._details_lock:
                self._details_loading.discard(code)
            self.dirty = True
