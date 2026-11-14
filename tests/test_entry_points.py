"""The package registers its layers and tab through the core entry points."""

import importlib.metadata

from terrascope_br.tabs import brasil_tabs

LAYER_NAMES = {"br_ufs", "br_municipios", "br_fires", "br_quakes", "br_gauges", "br_radar",
                "br_inmet_alerts"}


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
