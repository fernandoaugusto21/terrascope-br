"""IBGE: state outlines, municipality outlines (from a zoom on) and the
municipality name list. Each file is downloaded once and kept for 30 days."""

from __future__ import annotations

import json
import time

from terrascope.core.layer import LayerRender
from terrascope.core.view import View

from terrascope_br import store
from terrascope_br._i18n import _
from terrascope_br.base import SourceLayer
from terrascope_br.geo import draw_outlines, rings_from_geojson, view_key
from terrascope_br.net import SourceError, get

MALHA_URL = ("https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR"
             "?formato=application/vnd.geo%2Bjson&intrarregiao={level}&qualidade=minima")
MUNICIPALITIES_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios?view=nivelado"
DOWNLOAD_TIMEOUT = 60.0
VALIDITY_SECONDS = 30 * 24 * 3600      # 30 days
MUNICIPALITY_MIN_ZOOM = 8.0            # municipalities are drawn from this zoom on
OUTLINE_COLOR = "#7fa3c0"
NAMES_FILE = "municipios_nivelado.json"

# Parsed cache files, by path: (modification time, parsed value). A file is parsed
# again only when it changes, so reload() on the UI thread does not reread megabytes.
_parsed: dict[str, tuple[float, object]] = {}

def _is_fresh(file) -> bool:
    age = store.age(file)
    return age is not None and age < VALIDITY_SECONDS

def _cached(file, parse):
    """parse(bytes) for `file`, reusing the last result while the file is unchanged.
    Raises OSError when the file is missing."""
    stamp = file.stat().st_mtime
    key = str(file)
    hit = _parsed.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    data = store.read_bytes(file)
    if data is None:
        raise OSError(f"cannot read {file}")
    value = parse(data)
    _parsed[key] = (stamp, value)
    return value

def _download_map(level: str, file) -> None:
    _status, body, _headers = get(MALHA_URL.format(level=level), DOWNLOAD_TIMEOUT, min_gap=1.0)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise SourceError(f"IBGE returned an unreadable map: {error}") from error
    if not isinstance(payload, dict) or not payload.get("features"):
        raise SourceError("IBGE map has no features")
    store.write_bytes(file, body)

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

def state_rings() -> list:
    """The state outlines of the cache, read without any download: [] until the
    map has been fetched once. Other layers use it for their own shapes."""
    return _read_rings(store.path("ibge", "ufs.geojson"))[0]

class _OutlineLayer(SourceLayer):
    level = "UF"
    cache_name = "ufs.geojson"
    refresh_seconds = VALIDITY_SECONDS
    stale_after_seconds = 365 * 24 * 3600
    credit = "IBGE"
    line_color = OUTLINE_COLOR

    def __init__(self) -> None:
        super().__init__()
        self._lines_key = None
        self._lines: list[str] = []

    def stamp_file(self):
        return store.path("ibge", self.cache_name)

    def fetch(self) -> None:
        _download_map(self.level, self.stamp_file())

    def parse(self):
        return _read_rings(self.stamp_file())

    def clear(self) -> None:
        super().clear()
        self._lines_key = None
        self._lines = []

    def visible(self, view: View) -> bool:
        return True

    def build(self, view: View) -> LayerRender:
        # visible() runs first: it sets the zoom gate even while the cache is empty,
        # otherwise a municipality layer that never had data would never be downloaded.
        shown = self.visible(view)
        if not self.items or not shown:
            return LayerRender(None, self.line_color)
        key = view_key(view)
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

class MunicipalityOutlinesLayer(_OutlineLayer):
    name = "br_municipios"
    toggle_key = "i"
    source_name = _("IBGE municipalities")
    info_title = _("MUNICIPALITY")
    level = "municipio"
    cache_name = "municipios.geojson"

    def __init__(self) -> None:
        super().__init__()
        self._zoomed_in = False

    def is_due(self) -> bool:
        # Downloaded only once the view is zoomed in far enough to use it.
        return self._zoomed_in and super().is_due()

    def visible(self, view: View) -> bool:
        self._zoomed_in = view.zoom >= MUNICIPALITY_MIN_ZOOM
        return self._zoomed_in

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
