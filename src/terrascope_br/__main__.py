"""`python -m terrascope_br`: check that this environment can load the BRASIL tab.

Terrascope 1.0.0 on PyPI has no plugin support. This package installs on it without
an error, and its tab never appears, so nothing at runtime can warn about it: the
package is not loaded at all. This command reports what is missing instead.

Exit status: 0 when the core has plugin support and the tab and its layers are
registered; 1 otherwise, with one line per problem.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import sys

PACKAGE = "terrascope-br"
TAB_GROUP = "terrascope.tabs"
LAYER_GROUP = "terrascope.layers"
EXPECTED_TABS = ("brasil",)
EXPECTED_LAYERS = ("br_ufs", "br_municipios", "br_fires", "br_quakes", "br_gauges", "br_radar",
                    "br_inmet_alerts")


def _names_registered_by_this_package(group: str) -> set[str]:
    names = set()
    for entry_point in importlib.metadata.entry_points(group=group):
        dist = getattr(entry_point, "dist", None)
        dist_name = str(dist.metadata["Name"] if dist is not None else "")
        if dist_name.replace("_", "-").lower() == PACKAGE:
            names.add(entry_point.name)
    return names


def check() -> list[str]:
    """The problems found, one line each. An empty list means the tab loads."""
    try:
        core = importlib.metadata.version("terrascope")
    except importlib.metadata.PackageNotFoundError:
        return ["terrascope is not installed in this environment"]
    problems = []
    if importlib.util.find_spec("terrascope.core.plugins") is None:
        problems.append(
            f"terrascope {core} has no plugin support (terrascope.core.plugins): "
            "install a Terrascope build with plugins, as the README says"
        )
    for group, expected in ((TAB_GROUP, EXPECTED_TABS), (LAYER_GROUP, EXPECTED_LAYERS)):
        registered = _names_registered_by_this_package(group)
        for name in expected:
            if name not in registered:
                problems.append(f"entry point {group} / {name} is not registered")
    return problems


def main() -> int:
    problems = check()
    if problems:
        for line in problems:
            print(f"terrascope-br: {line}")
        return 1
    core = importlib.metadata.version("terrascope")
    print(f"terrascope-br: OK with terrascope {core}: tab {', '.join(EXPECTED_TABS)} "
          f"and {len(EXPECTED_LAYERS)} layers are registered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
