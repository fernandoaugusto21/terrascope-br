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

import html
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
        self._regions_retry_at = 0.0
        self._generation = 0            # counts parses: the cached drawings belong to one of them

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
        since = previous.get("last_modified") if previous.get("regions") else None
        status, image, headers = get(IMAGE_URL, 30.0, min_gap=1.0, if_modified_since=since)
        if status == 304:
            # The same image: nothing to read again; the file's new age says it was checked.
            store.write_bytes(self.stamp_file(), json.dumps(previous, ensure_ascii=False).encode("utf-8"))
            return
        _status, page, _headers = get(PAGE_URL, 30.0, min_gap=1.0)
        try:
            states = read_states(page.decode("utf-8", "replace"), image)
        except ValueError as error:
            raise SourceError(f"CGE alert map: {error}") from error
        modified = next((value for name, value in (headers or {}).items() if name.lower() == "last-modified"), None)
        store.write_bytes(self.stamp_file(), json.dumps(
            {"time": _modified(headers), "last_modified": modified, "regions": states},
            ensure_ascii=False).encode("utf-8"))

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
        self._generation += 1
        self._cells_key = None          # the inherited warning cells are drawn again too
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
        outline_key = (view_key(view), self._generation)
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
