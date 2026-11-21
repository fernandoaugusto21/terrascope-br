"""IBGE: state outlines, municipality outlines (from a zoom on) and the
municipality name list. Each file is downloaded once and kept for 30 days.

The outlines have levels of detail by zoom. Up to UF_DETAIL_ZOOM the national
files are used (one for the states, one for all municipalities, both in the
coarsest quality). From UF_DETAIL_ZOOM on, only the states that the view shows
are used, each from its own file: "intermediaria" quality, and "maxima" from
UF_MAX_DETAIL_ZOOM on. A zoomed-in view never asks for a national file, so the
national municipality mesh is downloaded and drawn only for the views of the
whole country or of a region."""

from __future__ import annotations

import json
import time

from terrascope.core import config
from terrascope.core.layer import LayerRender
from terrascope.core.view import View

from terrascope_br import store
from terrascope_br._i18n import _
from terrascope_br.base import SourceLayer
from terrascope_br.geo import draw_outlines, rings_from_geojson, view_key
from terrascope_br.net import SourceError, get

MALHA_URL = ("https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR"
             "?formato=application/vnd.geo%2Bjson&intrarregiao={level}&qualidade=minima")
# One state: its outline (level "") or its municipalities (level "&intrarregiao=municipio").
UF_MALHA_URL = ("https://servicodados.ibge.gov.br/api/v3/malhas/estados/{code}"
                "?formato=application/vnd.geo%2Bjson{level}&qualidade={quality}")
MUNICIPALITIES_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios?view=nivelado"
DOWNLOAD_TIMEOUT = 60.0
VALIDITY_SECONDS = 30 * 24 * 3600      # 30 days
MUNICIPALITY_MIN_ZOOM = 8.0            # municipalities are drawn from this zoom on
UF_DETAIL_ZOOM = 32.0                  # from here on: one file per state in view, "intermediaria"
UF_MAX_DETAIL_ZOOM = 128.0             # from here on: "maxima"
OUTLINE_COLOR = "#7fa3c0"
NAMES_FILE = "municipios_nivelado.json"

# Parsed cache files, by path (and parser tag): (modification time, parsed value). A
# file is parsed again only when it changes, so reload() on the UI thread does not
# reread megabytes.
_parsed: dict[str, tuple[float, object]] = {}

def _is_fresh(file) -> bool:
    age = store.age(file)
    return age is not None and age < VALIDITY_SECONDS

def _cached(file, parse, tag: str = ""):
    """parse(bytes) for `file`, reusing the last result while the file is unchanged.
    `tag` tells apart two parsers of the same file. Raises OSError when the file is
    missing."""
    stamp = file.stat().st_mtime
    key = str(file) + tag
    hit = _parsed.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    data = store.read_bytes(file)
    if data is None:
        raise OSError(f"cannot read {file}")
    value = parse(data)
    _parsed[key] = (stamp, value)
    return value

def _is_parsed(file, tag: str = "") -> bool:
    """True when `file` was parsed already and has not changed since."""
    hit = _parsed.get(str(file) + tag)
    try:
        return hit is not None and hit[0] == file.stat().st_mtime
    except OSError:
        return False

def _download(url: str, file) -> None:
    _status, body, _headers = get(url, DOWNLOAD_TIMEOUT, min_gap=1.0)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise SourceError(f"IBGE returned an unreadable map: {error}") from error
    if not isinstance(payload, dict) or not payload.get("features"):
        raise SourceError("IBGE map has no features")
    store.write_bytes(file, body)

def _download_map(level: str, file) -> None:
    _download(MALHA_URL.format(level=level), file)

def _parse_rings(data: bytes):
    rings = rings_from_geojson(json.loads(data.decode("utf-8")))
    return rings

def _read_rings(file):
    """(rings, epoch of the file), or ([], None) when the map was never downloaded."""
    try:
        rings = _cached(file, _parse_rings)
    except OSError:
        return [], None
    return rings, time.time() - (store.age(file) or 0.0)

def _read_epoch(file) -> float | None:
    age = store.age(file)
    return None if age is None else time.time() - age

def state_rings() -> list:
    """The state outlines of the cache, read without any download: [] until the
    map has been fetched once. Other layers use it for their own shapes."""
    return _read_rings(store.path("ibge", "ufs.geojson"))[0]

def _parse_boxes(data: bytes) -> dict[str, tuple[float, float, float, float]]:
    """IBGE state code -> (west, south, east, north) of its outline."""
    boxes: dict[str, tuple[float, float, float, float]] = {}
    for feature in json.loads(data.decode("utf-8")).get("features", []):
        code = str(((feature or {}).get("properties") or {}).get("codarea") or "")
        rings = rings_from_geojson({"features": [feature]})
        if not code or not rings:
            continue
        boxes[code] = (min(r.min_lon for r in rings), min(r.min_lat for r in rings),
                       max(r.max_lon for r in rings), max(r.max_lat for r in rings))
    return boxes

def state_boxes() -> dict[str, tuple[float, float, float, float]]:
    """IBGE state code -> bounding box, from the cached national state file; {}
    until it has been downloaded."""
    try:
        return _cached(store.path("ibge", "ufs.geojson"), _parse_boxes, tag="#boxes")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}

def states_in_view(view: View) -> tuple[str, ...]:
    """Codes of the states whose box overlaps the view, in code order."""
    west, north = view.origin_lon, view.origin_lat
    east, south = west + view.lon_span, north - view.lat_span
    return tuple(sorted(
        code for code, (w, s, e, n) in state_boxes().items()
        if not (e < west or w > east or n < south or s > north)
    ))

def detail_quality(zoom: float) -> str | None:
    """IBGE quality of the per-state files at this zoom, or None below UF_DETAIL_ZOOM
    (the national files are used there)."""
    if zoom >= UF_MAX_DETAIL_ZOOM:
        return "maxima"
    if zoom >= UF_DETAIL_ZOOM:
        return "intermediaria"
    return None

class _OutlineLayer(SourceLayer):
    level = "UF"
    cache_name = "ufs.geojson"
    uf_level = ""                  # level part of UF_MALHA_URL for this layer
    min_zoom = 0.0                 # nothing is drawn (or downloaded) below this zoom
    refresh_seconds = VALIDITY_SECONDS
    stale_after_seconds = 365 * 24 * 3600
    credit = "IBGE"
    line_color = OUTLINE_COLOR

    def __init__(self) -> None:
        super().__init__()
        self._lines_key = None
        self._lines: list[str] = []
        # Files the current view needs, as (file, url) pairs, and the ones that
        # `items` holds now. They differ while a new level of detail loads.
        self._wanted: tuple = self.start_pieces()
        self._loaded: tuple = ()

    # -- which files a view needs -----------------------------------------------------
    def national_piece(self):
        return (store.path("ibge", self.cache_name), MALHA_URL.format(level=self.level))

    def start_pieces(self) -> tuple:
        """The files read at startup, before any view is known."""
        return (self.national_piece(),)

    def pieces_for(self, view: View) -> tuple:
        if view.zoom < self.min_zoom:
            return ()
        quality = detail_quality(view.zoom)
        if quality is None:
            return (self.national_piece(),)
        codes = states_in_view(view)
        if not codes:
            # No state box known yet (national state file not cached): the national
            # file of the states is cheap and brings the boxes; never the national
            # municipalities at this zoom.
            return (self.national_piece(),) if self.level == "UF" else ()
        prefix = "mun" if self.uf_level else "uf"
        return tuple(
            (store.path("ibge", f"{prefix}-{code}-{quality}.geojson"),
             UF_MALHA_URL.format(code=code, level=self.uf_level, quality=quality))
            for code in codes
        )

    # -- refresh cycle -----------------------------------------------------------------
    def stamp_file(self):
        return store.path("ibge", self.cache_name)

    def is_due(self) -> bool:
        wanted = self._wanted
        if not wanted:
            return False
        if any(not _is_fresh(file) for file, _url in wanted):
            return True
        # Downloaded but not read yet: read them in the background, not on the UI thread.
        return wanted != self._loaded and any(not _is_parsed(file) for file, _url in wanted)

    def fetch(self) -> None:
        for file, url in self._wanted:
            if not _is_fresh(file):
                _download(url, file)
            try:
                _cached(file, _parse_rings)   # parse here, off the UI thread
            except (OSError, ValueError) as error:
                raise SourceError(f"IBGE map unreadable: {error}") from error

    def parse(self):
        wanted = self._wanted
        rings: list = []
        epochs: list[float] = []
        for file, _url in wanted:
            file_rings, epoch = _read_rings(file)
            if epoch is None:
                continue
            rings.extend(file_rings)
            epochs.append(epoch)
        self._loaded = wanted
        if not wanted:
            # Nothing drawn at this zoom: the legend tells the age of the national file.
            epochs = [epoch for epoch in [_read_epoch(self.stamp_file())] if epoch is not None]
        return rings, (min(epochs) if epochs else None)

    def clear(self) -> None:
        super().clear()
        self._lines_key = None
        self._lines = []
        self._loaded = ()

    def visible(self, view: View) -> bool:
        """Work out the files this view needs; False when nothing is drawn at its zoom.
        A change is read at once when its files are parsed already (or nothing will be
        fetched this session); otherwise the old outlines stay until the background
        refresh has the new ones."""
        wanted = self.pieces_for(view)
        if wanted != self._wanted:
            self._wanted = wanted
            if wanted != self._loaded and (
                all(_is_parsed(file) for file, _url in wanted) or not config.FETCH_NEW_DATA_FROM_API
            ):
                self.reload()
                self._lines_key = None
        return bool(wanted)

    def build(self, view: View) -> LayerRender:
        # visible() runs first: it sets the wanted files even while the cache is empty,
        # otherwise a layer that never had data would never be downloaded.
        shown = self.visible(view)
        if not self.items or not shown:
            return LayerRender(None, self.line_color)
        key = (view_key(view), id(self.items))
        if key != self._lines_key:
            self._lines = draw_outlines(self.items, view)
            self._lines_key = key
        return LayerRender(self._lines, self.line_color)

class StateOutlinesLayer(_OutlineLayer):
    name = "br_ufs"
    toggle_key = "u"
    source_name = _("IBGE states")
    info_title = _("STATE")
    level = "UF"
    cache_name = "ufs.geojson"
    uf_level = ""

class MunicipalityOutlinesLayer(_OutlineLayer):
    name = "br_municipios"
    toggle_key = "i"
    source_name = _("IBGE municipalities")
    info_title = _("MUNICIPALITY")
    level = "municipio"
    cache_name = "municipios.geojson"
    uf_level = "&intrarregiao=municipio"
    min_zoom = MUNICIPALITY_MIN_ZOOM

    def start_pieces(self) -> tuple:
        return ()  # nothing until a view is zoomed in far enough

def _names_from(items) -> dict[str, tuple[str, str]]:
    """IBGE code -> (name, UF) from the localities list. Entries without a code,
    a name or a UF are left out; the list must keep at least one."""
    if not isinstance(items, list):
        raise ValueError("the municipality list is not a list")
    names: dict[str, tuple[str, str]] = {}
    for item in items:
        try:
            code = str(int(item["municipio-id"]))
            names[code] = (str(item["municipio-nome"]), str(item["UF-sigla"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not names:
        raise ValueError("no municipality with a code, a name and a UF")
    return names

def _parse_names(data: bytes) -> dict[str, tuple[str, str]]:
    return _names_from(json.loads(data.decode("utf-8")))

def municipality_names() -> dict[str, tuple[str, str]]:
    """IBGE code -> (municipality name, UF). Reads the cache only; the download
    happens in ensure_municipality_names(). A damaged cache file is removed, so the
    next ensure_municipality_names() downloads it again."""
    file = store.path("ibge", NAMES_FILE)
    try:
        return _cached(file, _parse_names)
    except OSError:
        return {}
    except (ValueError, KeyError, TypeError):
        file.unlink(missing_ok=True)
        _parsed.pop(str(file), None)
        return {}

def ensure_municipality_names() -> None:
    """Keep the municipality list for 30 days. Only a valid list is written to the
    cache. Raises SourceError on failure."""
    file = store.path("ibge", NAMES_FILE)
    if _is_fresh(file):
        return
    _status, body, _headers = get(MUNICIPALITIES_URL, DOWNLOAD_TIMEOUT, min_gap=1.0)
    try:
        names = _names_from(json.loads(body.decode("utf-8")))
    except (UnicodeDecodeError, ValueError) as error:
        raise SourceError(f"IBGE municipality list unreadable: {error}") from error
    compact = [
        {"municipio-id": int(code), "municipio-nome": name, "UF-sigla": uf}
        for code, (name, uf) in names.items()
    ]
    store.write_bytes(file, json.dumps(compact, ensure_ascii=False).encode("utf-8"))
