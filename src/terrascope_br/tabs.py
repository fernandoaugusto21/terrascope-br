"""The BRASIL tab. Terrascope reads it through the `terrascope.tabs` entry point."""

from __future__ import annotations

from terrascope_br._i18n import _

def brasil_tabs() -> list[dict]:
    return [
        {
            "name": "brasil",
            "label": "BRASIL",
            "color": "#e15759",
            "blurb": _("Brazil: state and city outlines, fires, quakes, rain gauges and radar"),
            "layers": [
                "basemap",
                "br_ufs",
                "br_municipios",
                "br_fires",
                "br_quakes",
                "br_gauges",
                "br_radar",
            ],
            "detailed": True,
            "attribution": _("Data: IBGE, INPE, IAG-USP, SP Águas"),
            "view": {"lat": -14, "lon": -52, "zoom": 3},
        }
    ]
