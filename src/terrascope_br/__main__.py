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
PROVIDER_GROUP = "terrascope.weather_providers"
EXPECTED_PROVIDERS = ("br",)
EXPECTED_TABS = ("brasil",)
EXPECTED_LAYERS = ("br_global_rain", "br_global_temp", "br_ufs", "br_municipios", "br_fires", "br_quakes",
                   "br_gauges", "br_radar", "br_inmet_alerts", "br_inundation_cge")


def _names_registered_by_this_package(group: str) -> set[str]:
    names = set()
    for entry_point in importlib.metadata.entry_points(group=group):
        dist = getattr(entry_point, "dist", None)
        dist_name = str(dist.metadata["Name"] if dist is not None else "")
        if dist_name.replace("_", "-").lower() == PACKAGE:
            names.add(entry_point.name)
    return names


def _core_tab_keys() -> frozenset:
    """The tab keys the installed core accepts."""
    from terrascope.core import plugins

    return frozenset(getattr(plugins, "TAB_KEYS", ()))


def check() -> list[str]:
    """The problems found, one line each. An empty list means the tab, the layers and
    the weather providers load."""
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
    elif "zoom_max" not in _core_tab_keys():
        problems.append(
            f"terrascope {core} does not know a tab's zoom_max, so it would skip the BRASIL tab: "
            "update Terrascope from its main branch, as the README says"
        )
    if importlib.util.find_spec("terrascope.layers.weather.providers") is None:
        # The tab loads without it; the city weather then stays on Open-Meteo.
        problems.append(
            f"terrascope {core} has no weather providers (terrascope.layers.weather.providers): the "
            "tab loads, but the CGE / INMET weather is not used; update Terrascope from its main branch"
        )
    else:
        from terrascope.core.layer import Layer

        if not callable(getattr(Layer, "credits", None)):
            # The tab loads; GeoSampa goes in the tab's credits line instead.
            problems.append(
                f"terrascope {core} does not draw the layers' own credits (Layer.credits): the "
                "selected city's weather source is not named on the map; update Terrascope from its main branch"
            )
    for group, expected in ((TAB_GROUP, EXPECTED_TABS), (LAYER_GROUP, EXPECTED_LAYERS),
                            (PROVIDER_GROUP, EXPECTED_PROVIDERS)):
        registered = _names_registered_by_this_package(group)
        for name in expected:
            if name not in registered:
                problems.append(f"entry point {group} / {name} is not registered")
    return problems


def check_inmet_token() -> int:
    """`python -m terrascope_br inmet-token`: say where the INMET station key comes
    from and whether INMET accepts it (one request to the station API)."""
    import os
    from datetime import datetime, timedelta, timezone

    from terrascope_br.sources import weather_providers as wp

    token = wp.inmet_token()
    if not token:
        print(f"terrascope-br: no INMET station key: set {wp.TOKEN_ENV} or write it in "
              f"{wp.token_file()}; the current conditions come from the keyless nearest-station answer")
        return 1
    where = wp.TOKEN_ENV if os.environ.get(wp.TOKEN_ENV, "").strip() else str(wp.token_file())
    day = datetime.now(timezone.utc).date()
    url = wp.STATION_URL.format(start=(day - timedelta(days=1)).isoformat(), end=day.isoformat(),
                                station="A701", token=token)
    try:
        status, body, _headers = wp.get(url, wp.TIMEOUT, min_gap=1.0)
    except wp.SourceError as error:
        refused = any(code in str(error) for code in ("HTTP 401", "HTTP 403"))
        print(f"terrascope-br: INMET {'refused' if refused else 'did not answer'} the key from {where}: {error}")
        return 1
    text = wp._text(body).strip()
    if text.upper().startswith(wp.INVALID_KEY_TEXT):
        print(f"terrascope-br: INMET refused the key from {where}: {text}")
        return 1
    print(f"terrascope-br: INMET accepted the key from {where} (HTTP {status}, "
          f"{'no data for the period' if not text else 'station A701 answered'}).")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["inmet-token"]:
        return check_inmet_token()
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
