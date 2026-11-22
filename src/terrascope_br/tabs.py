"""The BRASIL tab. Terrascope reads it through the `terrascope.tabs` entry point."""

from __future__ import annotations

from terrascope_br._i18n import _


def _core_names_layer_credits() -> bool:
    """True when the core draws the layers' own credits (Layer.credits)."""
    try:
        from terrascope.core.layer import Layer
    except ImportError:
        return False
    return callable(getattr(Layer, "credits", None))

def brasil_tabs() -> list[dict]:
    attribution = _("Data: IBGE, INPE, IAG-USP, SP Águas, USGS, INMET, CGE, RainViewer, Open-Meteo")
    if not _core_names_layer_credits():
        # An older core does not draw the layers' credits: GeoSampa (CC BY-SA 4.0 asks to
        # be named) goes in the tab's line instead.
        attribution += " · GeoSampa (CC BY-SA 4.0)"
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
                "br_inmet_alerts",
                "br_inundation_cge",
            ],
            "detailed": True,
            # The city temperature labels take the place of the basemap's city names (as on the WEATHER tab).
            "place_names": False,
            # Sources shown only now and then are named by their layers while they show
            # (Layer.credits): GeoSampa with the CGE regions, the weather source of the
            # selected city (CPTEC/INPE when it gave the forecast).
            "attribution": attribution,
            "view": {"lat": -14, "lon": -52, "zoom": 3},
            # The most a tab may allow, 16 times closer than the world map (64): at 1024 one
            # braille dot is about 65 m, room for the streets of a city. The IBGE outlines stop
            # gaining detail at 256 ("maxima", a vertex every 250 m; see ibge.py).
            "zoom_max": 1024,
        }
    ]
