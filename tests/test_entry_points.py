"""The package registers its layers and tab through the core entry points."""

import importlib.metadata

from terrascope_br.sources import cge, chuva_online, iag, ibge, inpe, spaguas
from terrascope_br.tabs import brasil_tabs

LAYER_NAMES = {
    "br_global_rain", "br_global_temp", "br_ufs", "br_municipios", "br_fires", "br_quakes", "br_gauges", "br_radar",
    "br_inmet_alerts", "br_inundation_cge",
}


def _ours(group):
    return [
        entry_point for entry_point in importlib.metadata.entry_points(group=group)
        if entry_point.dist is not None and entry_point.dist.metadata["Name"] == "terrascope-br"
    ]


def test_layer_entry_points_are_registered():
    assert {entry_point.name for entry_point in _ours("terrascope.layers")} == LAYER_NAMES


def test_tab_entry_point_is_registered():
    assert [entry_point.name for entry_point in _ours("terrascope.tabs")] == ["brasil"]


def test_tab_only_lists_layers_of_this_package_or_the_built_ins():
    tab = brasil_tabs()[0]
    assert set(tab["layers"]) - {"basemap"} == LAYER_NAMES


def test_tab_attribution_credits_every_source():
    attribution = brasil_tabs()[0]["attribution"]
    for layer in (ibge.StateOutlinesLayer, ibge.MunicipalityOutlinesLayer, inpe.FireLayer,
                  iag.QuakeLayer, spaguas.RainGaugeLayer, chuva_online.RadarLayer, cge.CgeAlertsLayer):
        assert layer.credit in attribution
    assert "USGS" in attribution  # the earthquake fallback feed


def test_every_layer_key_is_accepted_by_the_core():
    """The core skips a plugin layer whose key the app reserves or another layer uses."""
    from terrascope.core import plugins

    reserved = plugins.reserved_toggle_keys()
    taken: dict[str, str] = {}
    for entry_point in _ours("terrascope.layers"):
        layer = entry_point.load()
        problem = plugins.toggle_key_problem(layer.toggle_key, taken, reserved)
        assert problem is None, f"{entry_point.name}: {problem}"
        if layer.toggle_key:
            taken[layer.toggle_key.lower()] = entry_point.name
