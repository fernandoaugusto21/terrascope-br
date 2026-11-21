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
describes. Nothing is drawn below MIN_ZOOM, where the city is a few cells."""

from __future__ import annotations

import io
import json
import re
import time
import unicodedata
from email.utils import parsedate_to_datetime

import numpy as np
from PIL import Image

from terrascope.core.layer import LayerRender, SidebarLine
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

AREA_PATTERN = re.compile(
    r'<area[^>]*shape="circle"[^>]*coords="\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*"[^>]*title="([^"]+)"', re.I)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(text.lower().split())


def parse_areas(page: str) -> list[tuple[str, str, int, int, int]]:
    """(region code, CGE name, x, y, radius) of each circle of the image map.
    Raises ValueError when no circle names a known region."""
    import html as html_module

    areas = []
    for x, y, radius, title in AREA_PATTERN.findall(page):
        name = html_module.unescape(title).strip()
        code = REGION_CODES.get(_plain(name))
        if code:
            areas.append((code, name, int(x), int(y), int(radius)))
    if not areas:
        raise ValueError("no known region on the CGE alert map")
    return areas


def pixel_state(red: int, green: int, blue: int) -> str:
    """The state a map pixel's colour stands for: yellow, orange and red are
    states; grey, white, black and the city's blue rivers are not (watch)."""
    if red < 170 or blue > red * 0.6:
        return "observacao"
    ratio = green / red
    if ratio > 0.8:
        return "atencao"
    if ratio > 0.35:
        return "iminencia"
    return "alerta"


def region_state(image: Image.Image, x: int, y: int, radius: int) -> str:
    """The state of the region whose circle is at (x, y): the most common state
    colour inside the circle, when enough of it carries one (the region's name is
    printed there in black); watch otherwise."""
    rgb = np.asarray(image.convert("RGB"), dtype=np.int32)
    height, width = rgb.shape[:2]
    ys, xs = np.mgrid[max(0, y - radius):min(height, y + radius + 1), max(0, x - radius):min(width, x + radius + 1)]
    inside = (xs - x) ** 2 + (ys - y) ** 2 <= radius * radius
    counts = {state: 0 for state in ORDER}
    for red, green, blue in rgb[ys[inside], xs[inside]].tolist():
        counts[pixel_state(red, green, blue)] += 1
    total = max(1, sum(counts.values()))
    colored = [(counts[state], ORDER.index(state), state) for state in ORDER[1:] if counts[state]]
    if not colored or sum(count for count, _i, _s in colored) < total * COLORED_SHARE:
        return "observacao"
    return max(colored)[2]


def read_states(page: str, image_bytes: bytes) -> dict[str, dict]:
    """Region code -> {"name", "state"} from the page and its image. Raises ValueError."""
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except (OSError, ValueError) as error:
        raise ValueError(f"unreadable CGE map image: {error}") from error
    return {code: {"name": name, "state": region_state(image, x, y, radius)}
            for code, name, x, y, radius in parse_areas(page)}


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
    for feature in payload.get("features") or []:
        properties = (feature or {}).get("properties") or {}
        code = str(properties.get("sg_subprefeitura") or "").strip()
        geometry = (feature or {}).get("geometry") or {}
        kind, coordinates = geometry.get("type"), geometry.get("coordinates") or []
        polygons = [coordinates] if kind == "Polygon" else coordinates if kind == "MultiPolygon" else []
        rings = [[[round(lon, 5), round(lat, 5)] for lon, lat, *_rest in thin(ring)]
                 for polygon in polygons for ring in polygon if len(ring) >= 4]
        if code and rings:
            regions[code] = {"name": str(properties.get("nm_subprefeitura") or code).title(), "rings": rings}
    if not regions:
        raise ValueError("no subprefeitura in the GeoSampa answer")
    return regions


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
    credit = "CGE, GeoSampa"
    refresh_seconds = 300
    stale_after_seconds = 3600

    def __init__(self) -> None:
        super().__init__()
        self._outline_key = None
        self._outline_lines: list[str] = []
        self.regions: list[dict] = []   # every region, with its state (items: the ones in a state)

    # -- files ----------------------------------------------------------------------
    def stamp_file(self):
        return store.path("cge", "alertas.json")

    def regions_file(self):
        return store.path("cge", "subprefeituras.json")

    def fetch(self) -> None:
        regions_age = store.age(self.regions_file())
        if regions_age is None or regions_age >= REGIONS_SECONDS:
            _status, body, _headers = get(REGIONS_URL, 60.0, min_gap=1.0)
            try:
                compact = compact_regions(json.loads(body.decode("utf-8")))
            except (UnicodeDecodeError, ValueError, TypeError) as error:
                if regions_age is None:
                    raise SourceError(f"GeoSampa regions unreadable: {error}") from error
            else:
                store.write_bytes(self.regions_file(), json.dumps(compact).encode("utf-8"))
        _status, page, _headers = get(PAGE_URL, 30.0, min_gap=1.0)
        _status, image, headers = get(IMAGE_URL, 30.0, min_gap=1.0)
        try:
            states = read_states(page.decode("utf-8", "replace"), image)
        except ValueError as error:
            raise SourceError(f"CGE alert map: {error}") from error
        store.write_bytes(self.stamp_file(), json.dumps(
            {"time": _modified(headers), "regions": states}, ensure_ascii=False).encode("utf-8"))

    def parse(self):
        data = store.read_bytes(self.stamp_file())
        shapes = store.read_bytes(self.regions_file())
        if data is None or shapes is None:
            self.regions = []
            return [], None
        document = json.loads(data.decode("utf-8"))
        outlines = json.loads(shapes.decode("utf-8"))
        regions = []
        for code, shape in outlines.items():
            rings = [Ring(points) for points in shape["rings"] if len(points) >= 2]
            if not rings:
                continue
            state = (document.get("regions") or {}).get(code, {}).get("state", "observacao")
            state = state if state in STATES else "observacao"
            biggest = max(rings, key=lambda ring: (ring.max_lon - ring.min_lon) * (ring.max_lat - ring.min_lat))
            centre = biggest.points[:-1].mean(axis=0) if len(biggest.points) > 1 else biggest.points[0]
            regions.append({
                "id": f"cge-{code}", "code": code, "region": shape.get("name") or code,
                "state": state, "color": STATES[state][0], "rings": rings,
                "lon": float(centre[0]), "lat": float(centre[1]),
                # what InmetAlertsLayer's legend reads
                "severity": _(STATES[state][1]), "event": _(STATES[state][1]),
            })
        self.regions = regions
        flagged = [region for region in regions if region["state"] != "observacao"]
        flagged.sort(key=lambda region: ORDER.index(region["state"]))  # most severe drawn last
        return flagged, float(document.get("time") or 0) or None

    def clear(self) -> None:
        super().clear()
        self._outline_key = None
        self._outline_lines = []

    # -- drawing ----------------------------------------------------------------------
    def _in_view(self, view: View) -> bool:
        west, south, east, north = CITY_BOX
        return (view.zoom >= MIN_ZOOM and not (east < view.origin_lon or west > view.origin_lon + view.lon_span
                or north < view.origin_lat - view.lat_span or south > view.origin_lat))

    def build(self, view: View) -> LayerRender:
        if not self.regions or not self._in_view(view):
            return LayerRender(None, OUTLINE_COLOR)
        outline_key = (view_key(view), id(self.regions))
        if outline_key != self._outline_key:
            self._outline_lines = draw_outlines([ring for region in self.regions for ring in region["rings"]], view)
            self._outline_key = outline_key
        if self.data_time is None or self.is_stale() or not self.items:
            return LayerRender(self._outline_lines, OUTLINE_COLOR)  # old states are not shown as current
        flagged = super().build(view)
        return LayerRender(self._outline_lines, OUTLINE_COLOR, markers=flagged.markers, bg_cells=flagged.bg_cells)

    # -- legend and info box ---------------------------------------------------------
    def legend_key(self):
        return key(*((colour, _(label)) for colour, label in STATES.values()))

    def legend_lines(self) -> list[SidebarLine]:
        # The base layer's: title with its on/off switch, then the key or what is wrong.
        return SourceLayer.legend_lines(self)

    def info_rows(self) -> list[tuple[str, str]]:
        region = self.selected
        if not region:
            return []
        updated = time.strftime("%d/%m %H:%M", time.localtime(self.data_time)) if self.data_time else _("N/A")
        return [
            (_("REGION"), region["region"]),
            (_("STATE"), _(STATES[region["state"]][1])),
            (_("UPDATED"), updated),
            (_("SOURCE"), "CGE"),
        ]
