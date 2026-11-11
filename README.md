# terrascope-br

Brazilian data layers for [Terrascope](https://github.com/a-shygun/Terrascope), packaged as a plugin. It adds a
**BRASIL** tab with public Brazilian sources. Terrascope itself does not need to change for it to load.

Requires a Terrascope build with layer and tab plugin support (entry points `terrascope.layers` and
`terrascope.tabs`), with the translation support and the home view of tabs. Those are not in a released version yet.

## Install

Terrascope 1.0.0 on PyPI has no plugin support, so install a Terrascope build with plugins first, from the fork at a
fixed commit:

    pip install "terrascope @ git+https://github.com/fernandoaugusto21/Terrascope@d10ce09880983148941ffccae28daaff437d3ae6"
    pip install -e .                 # from a checkout of this repository, in the same environment
    python -m terrascope_br          # checks that the core has plugins, and that the tab and its layers are registered
    terrascope --list-tabs           # BRASIL appears with origin plugin:terrascope-br
    terrascope -t brasil             # open the tab (it starts on Brazil)

`python -m terrascope_br` exits with 1 and says what is missing when a line of the check fails. The commit above is the
one this package was tested against: update it when the plugin support of Terrascope moves to another commit.

`pip uninstall terrascope-br` removes the tab and the layers again.

## Layers

| Layer (`--disable-layer`) | Key | Source | Refresh | Notes |
|---|---|---|---|---|
| `br_ufs` | U | IBGE, state outlines | once per 30 days | |
| `br_municipios` | I | IBGE, municipality outlines | once per 30 days | downloaded only from zoom 8 on |
| `br_fires` | F | INPE Queimadas | 10 min | hotspots of the last 3 h |
| `br_quakes` | B | IAG-USP catalogue, or USGS if IAG-USP fails | 30 min | last 30 days, Brazil and neighbours |
| `br_gauges` | G | SP Águas, rain gauges | 10 min | rain in the last hour, city from the IBGE code |
| `br_radar` | T | IAG-USP Chuva Online, 36 km radar | 10 min | hidden when the newest frame is over 30 min old |

The municipality names used by the gauges come from the IBGE localities list (kept 30 days).

### Earthquakes: two sources

The IAG-USP catalogue is the main source. It has small events that the USGS feed does not show. When the IAG-USP
query fails (network error, HTTP error, empty or unreadable answer), the layer uses the USGS `all_month` feed for the
same box and window. The legend title and the info box of each quake say which source is in use
(`IAG-USP` or `USGS (fallback)`). If both fail, the layer keeps its last good data, or stays empty with the reason in
the legend when there is none.

## Legend

Each layer uses at most two legend lines: its title, then either its colour key or one message when the data needs
attention (no data yet, last update failed, or no update since a time). Credits are not repeated in the legend; they
are in the BRASIL tab's attribution line.

## Home view

The BRASIL tab sets `view: {lat: -14, lon: -52, zoom: 3}`, so the map opens over Brazil. The view is applied each time
the tab is opened; see the plugin section of the Terrascope README.

## Behaviour

- Data is cached under `<cache-dir>/br`. `terrascope --offline` shows the cached data without any request.
- A failed download keeps the last good data, shows the reason in the legend, and backs off before retrying. It
  never raises on screen.
- Requests use the Terrascope User-Agent, gzip, a 40 MB size cap and at least 1 s between requests to the same host.
- The INPE CSV time column is read as UTC. The file does not say which time zone it uses; the local time shown is
  UTC-3.

## Language

All texts are written in English in the source and marked with `_()`. The Portuguese catalogue is
`src/terrascope_br/locale/pt_BR/LC_MESSAGES/terrascope_br.po` (domain `terrascope_br`). The language is the one the
Terrascope core uses (`--lang`, `app.language`). The build compiles the catalogues with Babel, as the core does.

To update the catalogue after changing texts:

    pybabel extract -F babel.cfg -o src/terrascope_br/locale/terrascope_br.pot .
    pybabel update -i src/terrascope_br/locale/terrascope_br.pot -d src/terrascope_br/locale -D terrascope_br -l pt_BR
    # fill in the new msgstr entries in the .po, then reinstall (the build hook compiles the .mo)

## Tests

    pip install -e ".[dev]"
    pytest tests

The tests read trimmed samples from `tests/data` (see the README there) and do not use the network.

## Sources and terms

Each source keeps its own terms. What was checked on 2026-11-10:

- IBGE: the terms page could not be read (HTTP 403); the API index page has no terms. Not verified.
- INPE Queimadas: the terms page moved and the new address returned 404. Not verified.
- IAG-USP (seismology catalogue and Chuva Online): no terms or reuse conditions published on the pages checked.
- SP Águas (SIBH): no terms or reuse conditions published on the page checked.
- USGS (earthquake feed, fallback): not checked in this round.

Credits are shown in the BRASIL tab attribution.

## License

MIT. Each data source keeps its own terms; see the credits shown in the app.
