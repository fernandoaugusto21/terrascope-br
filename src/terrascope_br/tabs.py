"""The BRASIL tab. Terrascope reads it through the `terrascope.tabs` entry point."""

from __future__ import annotations

from terrascope_br._i18n import _

def brasil_tabs() -> list[dict]:
    return [
        {
            "name": "brasil",
            "label": "BRASIL",
            "color": "#e15759",
            "blurb": _("Brazil: state and city outlines, fires, quakes, rain gauges, radar, and global rain and temperature cropped to Brazil"),
            "layers": [
                "basemap",
                "br_global_rain",
                "br_global_temp",
                "br_ufs",
                "br_municipios",
                "br_fires",
                "br_quakes",
                "br_gauges",
                "br_radar",
            ],
            "detailed": True,
            # The city temperature labels take the place of the basemap's city names (as on the WEATHER tab).
            "place_names": False,
            "attribution": _("Data: IBGE, INPE, IAG-USP, SP Águas, RainViewer, Open-Meteo"),
            "view": {"lat": -14, "lon": -52, "zoom": 3},
        }
    ]
