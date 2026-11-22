# terrascope-br

Brazilian data layers for [Terrascope](https://github.com/a-shygun/Terrascope), packaged as a plugin. It adds a
**BRASIL** tab with public Brazilian sources. Terrascope itself does not need to change for it to load.

Requires a Terrascope build with layer and tab plugin support (entry points `terrascope.layers` and
`terrascope.tabs`), with the translation support and the home view of tabs. Those are on the `main` branch of the
fork, and not in a released version yet.

## Install

Terrascope 1.0.0 on PyPI has no plugin support, so install a Terrascope build with plugins first, from the fork's `main`
branch:

    pip install "terrascope @ git+https://github.com/fernandoaugusto21/Terrascope@main"
    pip install -e .                 # from a checkout of this repository, in the same environment
    python -m terrascope_br          # checks that the core has plugins, and that the tab and its layers are registered
    terrascope --list-tabs           # BRASIL appears with origin plugin:terrascope-br
    terrascope -t brasil             # open the tab (it starts on Brazil)

`python -m terrascope_br` exits with 1 and says what is missing when a line of the check fails.

When a release with plugin support is out, this line changes to that release.

`pip uninstall terrascope-br` removes the tab and the layers again.

### Windows: standalone build (Scoop)

Each merge into `main` builds a standalone `terrascope.exe` with this plugin and the core's `main` branch, and
publishes it as a GitHub release (`windows-<core commit>-br<plugin commit>`) with its Scoop manifest
(`.github/workflows/build-windows.yml`). The Scoop app is `terrascope-br` (it installs `terrascope.exe`), so it does not
clash with the core's own `terrascope` app; do not install both. To install or update the newest build:

    scoop install https://github.com/fernandoaugusto21/terrascope-br/releases/latest/download/terrascope-br.json
    scoop uninstall terrascope-br; scoop install <same address>    # to move to a newer build

Only a build of the core's `main` is marked latest; a build of another core branch, tag or commit is a pre-release. The
ten newest builds are kept.

The build also runs when the core's `main` changes: right away when the core's workflow `notify-plugin.yml` has a
token to send the signal (secret `PLUGIN_DISPATCH_TOKEN` in the core repository: a fine-grained token with
"Contents: read and write" on this repository), and otherwise once a day, when the core's `main` has moved since the
last build (GitHub turns scheduled workflows off after 60 days without activity in the repository; Actions shows a
button to turn it on again). Pull requests build and check the executable without publishing it. A build can also be started by hand
(Actions, "Windows build", with a branch, tag or commit of the core).

## Layers

| Layer (`--disable-layer`) | Key | Source | Refresh | Notes |
|---|---|---|---|---|
| `br_ufs` | U | IBGE, state outlines | once per 30 days | national file up to zoom 32, then one file per state in view (see below) |
| `br_municipios` | I | IBGE, municipality outlines | once per 30 days | from zoom 8 on; national file up to zoom 32, then one file per state in view |
| `br_fires` | F | INPE Queimadas | 10 min | hotspots of the last 3 h |
| `br_quakes` | B | IAG-USP catalogue, or USGS if IAG-USP fails | 30 min | last 30 days, Brazil and neighbours |
| `br_radar` | T | IAG-USP Chuva Online, 36 km radar | 10 min | hidden when the newest frame is over 30 min old |
| `br_global_rain` | J | RainViewer radar (the core's frame and palette), inside the Brazil outline | 5 min check, a new frame about every 10 min | dBZ; drawn under `br_radar`; nothing is drawn until the IBGE state map is in the cache |
| `br_global_temp` | M | city temperatures (the core's city layer, with the providers below), cities of Brazil only | 15 min per city | relative colours, as on the WEATHER tab; labels replace the basemap city names in this tab; from zoom 32 on, one label per municipality (see below) |
| `br_inmet_alerts` | V | INMET, active weather warnings | 30 min | warnings of today and the next days, as areas; hidden when the data is over 1 h old |
| `br_inundation_cge` | (legend switch) | CGE, flood-risk state of each of the 32 subprefeituras of the city of São Paulo; outlines from GeoSampa | 5 min (outlines: 30 days) | from zoom 16 on; watch / attention / overflow imminent / alert, read from the colours of the CGE alert map (see below); states hidden when over 1 h old |

The SP Águas rain gauge layer (`br_gauges`) is inactive since 2026-11-22: it is not registered and makes no request.
Its code stays in `sources/spaguas.py` for later use (`ENABLED`).

The municipality names (weather labels and providers) come from the IBGE localities list, kept 30 days.

### Radar history

The Chuva Online site serves only its last ten frames, as ten files that are rewritten as new frames come (no archive).
The layer keeps every frame it sees in the last hour and its slider steps through them, as the core's radar slider
does (`[` and `]`, or the mouse on the slider row; the newest frame is the `LIVE` position). The loop uses the core's
radar animation settings: `--radar-animate` turns it on, and `frame_seconds` / `frame_hold_last_seconds` under
`layers.weather.radar` in the config file set the timing.
The history starts with the ten frames of the first fetch (about 30 minutes) and grows with each fetch.

### Global rain and temperature

These two layers reuse the core's code (`terrascope.layers.weather`): the RainViewer fetch and palette, and the
city-temperature layer with its label placement. Changes made to the core's weather code reach this tab.

- **Rain** is drawn only inside the outline of Brazil, from the IBGE state map that `br_ufs` keeps in its cache. The
  layer does not download that map itself: until `br_ufs` has fetched it, the legend says it is waiting. The layer
  keeps its own copy of the newest RainViewer frame, so while both the WEATHER tab and this layer show rain, the
  newest frames are downloaded twice. Sharing the core's frame store would avoid it; that is a core change and is not
  made here.
- **Rain colours** are the core's RainViewer levels, in dBZ. They are not the IAG-USP colours: the BR radar says
  weak, moderate or strong, without a unit, and is drawn over the global rain wherever both cover the same cell.
- **Temperature** labels only the cities of Brazil from the Natural Earth places list. The colours are relative to
  the coldest and hottest city read so far, as on the WEATHER tab, not to a fixed scale.
- Each layer has its own switch: the key (J, M) or the `on | off` choice in its legend line. The tab switches the
  whole layer on.
- The BRASIL tab hides the basemap's capital and city names (`place_names: false`), because the temperature labels
  take their place, as the WEATHER tab does.

### Earthquakes: two sources

The IAG-USP catalogue is the main source. It has small events that the USGS feed does not show. When the IAG-USP
query fails (network error, HTTP error, empty or unreadable answer), the layer uses the USGS `all_month` feed for the
same box and window. The legend title and the info box of each quake say which source is in use
(`IAG-USP` or `USGS (fallback)`). If both fail, the layer keeps its last good data, or stays empty with the reason in
the legend when there is none.

### Weather warnings

The INMET layer draws the area of each active warning, filled with the warning's own colour (`aviso_cor`), and a
`!` marker at the middle of the area's box. The legend has one entry per colour in the data, labelled with its
severity. A warning that appears in both lists of the API (today and the next days) is drawn once. Warnings with no
area are left out. The API gives the start and end times without a time zone; they are shown as they come.

### CGE flood risk by region

The CGE publishes the state of each subprefeitura of the city of São Paulo only as a picture: the page
`mapas.jsp?arq=alertas` shows the image `/cgesp/mapa_sp_geoserver.png` (made again every few minutes) and an HTML
image map with one circle per region, at its place on the image, with its name. The layer reads the colours inside
each circle: grey is watch, yellow attention, orange overflow imminent, red alert (the legend of that image). The
names of the image map are matched to the GeoSampa acronyms by a fixed table; a region the table does not know is left
out. The time of the data is the image's `Last-Modified`. A new look of that page or image shows up as "update
failed" in the legend, never as a wrong state. The outlines are GeoSampa's `geoportal:subprefeitura` (WFS), thinned
to about 110 m and kept 30 days.

By region, the layer also shows:

- **The name** of each subprefeitura (the CGE's own spelling), from zoom 128 on; a name that would cover another is
  left out. Clicking a name opens the region in the info box.
- **Flood points of the day** (`alagamentos.jsp`, read every 5 minutes with the states): the CGE lists them by region,
  with no position, so a region with active points gets `≈<number>` after its name (a mark alone below zoom 128), red
  when one of them is impassable, yellow when they are passable. The info box lists the points of the day, active first
  (time, street, passable or not). Flood points over 1 h old are not shown. If only the flood list fails, the states
  keep updating.
- **In the info box of a region:** its zone; the reading of the CGE station of that region (temperature, rain since
  the station's daily reset, wind), when it has one (27 of the 32 regions; `estacoes-meteorologicas.jsp` maps station
  names to regions by a fixed table); and the CGE forecast for today and tomorrow. **The CGE publishes one forecast for
  the whole city, not one per region**, and the box says so. Station and forecast are fetched when the region is
  selected and kept 10 and 30 minutes.

## Legend

Each layer uses at most two legend lines: its title, then either its colour key or one message when the data needs
attention (no data yet, last update failed, or no update since a time). Credits are not repeated in the legend; they
are in the BRASIL tab's attribution line.

## City weather in Brazil

The plugin registers two weather providers (core entry point `terrascope.weather_providers`), used for every city
the core labels with its weather, on any tab:

| Where | Current conditions | Forecast |
|---|---|---|
| municipality of São Paulo | CGE (city hall's weather centre), station at the Sé | CGE extended forecast: today and two days, with night, morning, afternoon and evening |
| any other municipality of Brazil | the nearest INMET automatic station, within 50 km and at most 3 h old (see below); else Open-Meteo | INMET by IBGE code (5 days); CPTEC/INPE (7 days) when INMET fails |
| outside Brazil | Open-Meteo | Open-Meteo |

The info box shows SOURCE (for example `Open-Meteo · forecast INMET`), and the forecast title ends with the provider
of the forecast. The CGE publishes HTML pages only; they are read by their CSS classes, so a change of layout there
shows up as "UNAVAILABLE" and the next provider (INMET for the forecast, Open-Meteo for the conditions) answers. The
CGE and INMET forecasts have no hourly values: the forecast box shows their parts of the day instead of the hourly
chart (CGE: overnight, morning, afternoon, night; INMET: morning, afternoon, night, for the first days), each with its
icon. INMET also gives the sunrise and sunset. The rain and wind rows show no data for them.

The municipality of a city is found in the IBGE outlines: the state in the national state file, then the
municipality in that state's own `minima` file (`mun-<state>-minima.geojson`, downloaded the first time, kept for 30
days). The CPTEC id of a municipality is looked up by name once and kept in `cptec/cptec_ids.json`.

### INMET stations and the station key (token)

The current conditions of a municipality outside the capital come from the nearest INMET automatic station: its
temperature, wind and the time of the reading (hourly, in UTC at INMET, shown in São Paulo time). Stations have no
sky reading, so the conditions words come from the running part of today's INMET forecast. The info box names the
station, for example `INMET A701 · forecast INMET`.

- **Without a key** (the default), the reading comes from INMET's forecast service, which answers with the nearest
  station of an IBGE municipality and its latest reading (`apiprevmet3.inmet.gov.br/estacao/proxima/<code>`).
- **With a key**, it comes from INMET's station API (`apitempo.inmet.gov.br/token/estacao/...`), from the automatic
  station nearest to the place itself (INMET's station list, kept a day).
- A station more than 50 km away or a reading more than 3 h old is not used: the core's next provider (Open-Meteo)
  answers for the current conditions, with no error on screen.
- A key INMET refuses (HTTP 401 or 403, or the text `CHAVE INVÁLIDA!`, which INMET sends with HTTP 200) is not sent
  again in that run (after an HTTP 401 or 403, which a firewall may send too, it is tried again after 30 minutes); the
  keyless answer stands in. The keyless answer also stands in when the keyed one fails or has no recent reading close
  enough. A city refreshes its weather every 15 minutes: without a key that is one request per municipality; with a
  key the towns that share a station share its answer for 10 minutes.
- The time shown is the place's own (by state: Acre, Amazonas, Rondônia, Roraima, Mato Grosso and Mato Grosso do Sul
  are behind São Paulo), and the conditions words come from the part of today's forecast running there; INMET has no
  overnight part, so from midnight to 6 h the words of today's morning stand in.

How to get a key. INMET publishes no sign-up page; the API manual's address
(`portal.inmet.gov.br/manual/manual-de-uso-da-api-estações`) answered HTTP 404 on 2026-11-21. The channel used by
other projects is an e-mail to INMET's registration desk:

1. Write to `cadastro.act@inmet.gov.br` (the address comes from third-party projects, not from an INMET page; ask
   INMET to confirm the channel in its answer). Say who you are (name, and institution if any), that you want a key
   (token) for the station data API `apitempo.inmet.gov.br`, what for (for example: a free, non-commercial terminal app
   showing the latest reading of the nearest automatic station), and how often it asks (one request per station every
   10 minutes or so). An institutional address is recommended by other projects.
2. Wait for the answer. INMET publishes no deadline and no request limit; third parties report that a request may
   cover at most 6 months of data.
3. Put the key in one of these places (the environment variable wins):
   - PowerShell, for the current window: `$env:TERRASCOPE_BR_INMET_TOKEN = "your key"`; for every new window:
     `[Environment]::SetEnvironmentVariable("TERRASCOPE_BR_INMET_TOKEN", "your key", "User")`;
   - or the file `br\inmet_token.txt` in the Terrascope config folder (`terrascope --show-paths` shows the folder):
     the first line that is not blank or a `#` comment is the key.
4. Check it: `python -m terrascope_br inmet-token` says where the key comes from and whether INMET accepts it.

### One label per municipality

Below zoom 32 the temperature layer labels the Natural Earth cities, as the core does (5 of them in the São Paulo
metropolitan area). From zoom 32 on (where the outlines switch to one file per state), it labels the municipalities
of the states in view instead: one point inside each municipality's outline, from the same `minima` file the providers
read, so a click asks the providers for that very municipality. The names come from the IBGE localities list.

Where labels would overlap, the core's rule keeps the first: municipalities with a Natural Earth city come first, by
that city's population, then the others by area. Only the labelled municipalities (at most the core's
`layers.weather.cities.max_labels`) are fetched, at most `max_concurrent_requests` at a time, and each source host is
asked at most once a second. The state files are read on the layer's refresh thread; until they are ready, the
Natural Earth cities stay. This needs a Terrascope core with `WeatherLayer.city_places`; an older core keeps the
Natural Earth cities at every zoom.

## Home view and zoom

The BRASIL tab sets `view: {lat: -14, lon: -52, zoom: 3}`, so the map opens over Brazil. The view is applied each time
the tab is opened; see the plugin section of the Terrascope README.

The tab also sets `zoom_max: 1024`, the core's default limit for a tab and 16 times closer than the world map (64): at 1024 one
braille dot is about 65 m. The IBGE outlines follow the zoom so that the detail matches the dots, up to 256:

| Zoom | State and municipality outlines | 1 dot (108 x 58 cells) |
|---|---|---|
| up to 32 | national files, `minima` quality (municipalities from zoom 8) | 2.1 km and more |
| 32 to 128 | one file per state in view, `intermediaria` (a vertex every 1.2 km in Greater São Paulo) | 2.1 km to 530 m |
| 128 to 1024 | one file per state in view, `maxima` (a vertex every 250 m, the finest IBGE file) | 530 m to 65 m |

A zoomed-in view never asks for a national file: the national municipality mesh (3.6 MB) is downloaded and drawn
only for the wide views. The per-state files are downloaded the first time a view shows that state at that level
(São Paulo: 1.0 MB `intermediaria`, 4.9 MB `maxima`; Minas Gerais `maxima`: 8.8 MB), read in the background, and kept
for 30 days. While a new level loads, the previous outlines stay on the screen. The boxes of the states come from the
national state file (about 100 kB), which the tab reads at its home view; a view over the sea or a
neighbouring country draws no IBGE outline. Offline, the cached files of a new level are read at once, which can
pause the screen for up to a second for the largest states.

From zoom 16 on, the core draws the coast from Natural Earth 1:10m (`map.zoom.detail_min`), so it matches the IBGE
outlines at the coast.

## Behaviour

- Data is cached under `<cache-dir>/br`. `terrascope --offline` shows the cached data without any request.
- A failed download keeps the last good data, shows the reason in the legend, and backs off before retrying. It
  never raises on screen.
- Requests use the Terrascope User-Agent, gzip, a 40 MB size cap and at least 1 s between requests to the same host.
  The global layers use the core's requests, so their limits are the core's.
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

The tests read trimmed samples from `tests/data` (see the README there) and do not use the network. They run on every pull request and on `main` (Ubuntu and Windows, `.github/workflows/ci.yml`), against the core's `main`.

## Sources and terms

Each source keeps its own terms. [`docs/data-terms.md`](docs/data-terms.md) lists, for each endpoint the plugin
reads, what the source publishes about reuse, attribution, request limits, commercial use and caching, with the URL
and access date of every statement. Where a source publishes no terms, the document says so. The credits are shown in
the BRASIL tab attribution; sources shown only now and then are named on their own map lines while they show (the
selected city's weather source, which names CPTEC/INPE whenever its forecast is on screen as its terms ask, and
"GeoSampa (CC BY-SA 4.0), ..." while the CGE regions are drawn). The weather sources (CGE, INMET, CPTEC/INPE) and GeoSampa
were checked on 2026-11-21: CPTEC/INPE does not allow commercial use or reproduction "em meios de divulgação" without
its authorisation, and the CGE publishes no terms; both are open items there.
RainViewer (global rain) and Open-Meteo (city temperatures), read by the global layers, are not covered by that
document yet; the core shows their credits on the WEATHER tab, and the BRASIL tab attribution lists them too.

## License

MIT. Each data source keeps its own terms; see [`docs/data-terms.md`](docs/data-terms.md) and the credits shown in the app.
