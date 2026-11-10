# terrascope-br

Brazilian data layers for [Terrascope](https://github.com/a-shygun/Terrascope), packaged as a plugin. It adds a
**BRASIL** tab with public Brazilian sources. Terrascope itself does not need to change for it to load.

Requires a Terrascope build with layer and tab plugin support (entry points `terrascope.layers` and
`terrascope.tabs`). That support is not in a released version yet.

## Install

    pip install -e .                 # from a checkout, in the same environment as terrascope
    terrascope --list-tabs           # BRASIL appears with origin plugin:terrascope-br
    terrascope -t brasil             # open the tab

`pip uninstall terrascope-br` removes the tab and the layers again.

## Layers

| Layer (`--disable-layer`) | Key | Source | Refresh | Notes |
|---|---|---|---|---|
| `br_ufs` | U | IBGE, state outlines | once per 30 days | |
| `br_municipios` | I | IBGE, municipality outlines | once per 30 days | downloaded only from zoom 8 on |
| `br_fires` | F | INPE Queimadas | 10 min | hotspots of the last 3 h |
| `br_quakes` | B | IAG-USP seismology catalogue | 30 min | last 30 days, Brazil and neighbours |
| `br_gauges` | G | SP Águas, rain gauges | 10 min | rain in the last hour, city from the IBGE code |
| `br_radar` | T | IAG-USP Chuva Online, 36 km radar | 10 min | hidden when the newest frame is over 30 min old |

The municipality names used by the gauges come from the IBGE localities list (kept 30 days).

## Behaviour

- Data is cached under `<cache-dir>/br`. `terrascope --offline` shows the cached data without any request.
- A failed download keeps the last good data, shows the reason in the layer's legend, and backs off before
  retrying. It never raises on screen.
- Requests use the Terrascope User-Agent, gzip, a 40 MB size cap and at least 1 s between requests to the same host.
- The INPE CSV time column is read as UTC. The file does not say which time zone it uses; the local time
  shown is UTC-3.

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

Credits are shown in the BRASIL tab attribution and in each layer's legend.

## License

MIT. Each data source keeps its own terms; see the credits shown in the app.
