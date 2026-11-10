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

def _is_fresh(file) -> bool:
    age = store.age(file)
    return age is not None and age < VALIDITY_SECONDS

def _download_map(level: str, file) -> None:
    _status, body, _headers = get(MALHA_URL.format(level=level), DOWNLOAD_TIMEOUT, min_gap=1.0)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise SourceError(f"IBGE returned an unreadable map: {error}") from error
    if not isinstance(payload, dict) or not payload.get("features"):
        raise SourceError("IBGE map has no features")
    store.write_bytes(file, body)

def _read_rings(file):
    data = store.read_bytes(file)
    if data is None:
        raise ValueError("missing cache file")
    rings = rings_from_geojson(json.loads(data.decode("utf-8")))
    return rings, time.time() - (store.age(file) or 0.0)

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
        if not self.items or not self.visible(view):
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

def municipality_names() -> dict[str, tuple[str, str]]:
    """IBGE code -> (municipality name, UF). Reads the cache only; the
    download happens in ensure_municipality_names()."""
    data = store.read_bytes(store.path("ibge", NAMES_FILE))
    if data is None:
        return {}
    names = {}
    for item in json.loads(data.decode("utf-8")):
        names[str(item["municipio-id"])] = (str(item["municipio-nome"]), str(item["UF-sigla"]))
    return names

def ensure_municipality_names() -> None:
    """Keep the municipality list for 30 days. Raises SourceError on failure."""
    file = store.path("ibge", NAMES_FILE)
    if _is_fresh(file):
        return
    _status, body, _headers = get(MUNICIPALITIES_URL, DOWNLOAD_TIMEOUT, min_gap=1.0)
    try:
        items = json.loads(body.decode("utf-8"))
        if not isinstance(items, list) or not items:
            raise ValueError("empty list")
    except (UnicodeDecodeError, ValueError) as error:
        raise SourceError(f"IBGE municipality list unreadable: {error}") from error
    store.write_bytes(file, body)
