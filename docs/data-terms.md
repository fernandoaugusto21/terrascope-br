# Data terms of use

This note records what each data source read by terrascope-br publishes about reuse: whether free software may consume
the data, the attribution required, request limits, commercial use and caching. Everything was checked on **2026-11-14**
(access date of every statement below). It is a reference note, not legal advice.

Where a source publishes no terms, the tables say so; nothing is assumed. Every statement names the page it came from.

- **fetched**: the page was read on the access date.
- **search snippet**: the statement came from a search result that was not opened.
- **not reachable**: the page could not be read (HTTP error, certificate error or DNS error).

Certainty:

- **high**: quoted from the source's own page.
- **medium**: from the source's own page, but as a summary, or from a neighbouring page.
- **low**: from a secondary source or a search snippet.
- **none**: nothing found.

## Endpoints read by the plugin

| Source | Endpoint(s) | Module |
|---|---|---|
| IBGE malhas (API v3) | `https://servicodados.ibge.gov.br/api/v3/malhas/paises/BR?formato=application/vnd.geo%2Bjson&intrarregiao={level}&qualidade=minima` | `sources/ibge.py` |
| IBGE localidades (API v1) | `https://servicodados.ibge.gov.br/api/v1/localidades/municipios?view=nivelado` | `sources/ibge.py` |
| INPE Queimadas | index `https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/10min/` and the `focos_10min_YYYYMMDD_HHMM.csv` files in it | `sources/inpe.py` |
| IAG-USP seismology catalogue (FDSN event service) | `https://moho.iag.usp.br/fdsnws/event/1/query` (parameters built by `query_url()`) | `sources/iag.py` |
| USGS earthquake feed (fallback) | `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_month.geojson` | `sources/iag.py` |
| SP Águas measurements (SIBH) | `https://apps.spaguas.sp.gov.br/sibh/api/v2/measurements/now?station_type_id=2&hours=1` | `sources/spaguas.py` |
| IAG-USP Chuva Online radar | `https://www.starnet.iag.usp.br/img_starnet/Radar_USP/pelletron_36km/loop/36km_loop_{index}.png` (10 frames) | `sources/chuva_online.py` |

## 1. IBGE (malhas and localidades APIs)

| Question | Answer | Certainty | Source |
|---|---|---|---|
| May free software consume it? | No restriction found. No explicit permission found on the API page. | none (for explicit permission) | [API docs](https://servicodados.ibge.gov.br/api/docs), fetched |
| Attribution? | Not stated on the API page. IBGE's library page says selected data from its publications may be used by third parties if IBGE is cited as the source. | low | [IBGE library](https://www.ibge.gov.br/biblioteca-servicos.html), search snippet (page returned HTTP 403) |
| Attribution form | Secondary sources give "IBGE" with the year (ABNT style "Fonte: IBGE (ANO)"). | low | [Canaltech](https://canaltech.com.br/educacao/como-citar-dados-do-ibge-nas-normas-abnt/), search snippet |
| Licence of the municipal mesh | The IBGE methodological note on the Malha Municipal Digital says the product and its derivatives are licensed compatibly with CC BY 4.0. Not verified that the API endpoints return this product. | low | [Malha Municipal Digital notes](https://biblioteca.ibge.gov.br/visualizacao/livros/liv102169.pdf), search snippet |
| Request limits | None published on the API page. | none | [API docs](https://servicodados.ibge.gov.br/api/docs), fetched |
| Commercial use | Secondary sources say reuse, including commercial, is allowed with attribution. Not verified on an IBGE page. | low | search snippets only |
| Caching | Not stated. | none | - |

Notes:

- The API page is a service index. It has no terms, licence, attribution, limit or caching text. It links to
  `/api/docs/malhas?versao=3` and `/api/docs/localidades`, which were not read.
- The IBGE terms page `https://www.ibge.gov.br/acesso-informacao/institucional/termos-de-uso.html` (address tried, not
  confirmed by a link) returned HTTP 403 on 2026-11-14. Not read.
- IBGE's Plano de Dados Abertos 2024-2025 (`https://www.ibge.gov.br/np_download/novoportal/documentos_institucionais/Plano_de_Dados_Abertos_IBGE_2024_2025.pdf?v=2`)
  was downloaded but its text could not be extracted. A search summary says IBGE treats open data as data released
  under an open licence. Not verified in the plan itself.
- Federal decree 8.777/2016, art. 2, III, is quoted by secondary sources as requiring open data to be published under
  an open licence that allows free use, consumption or crossing, and that "only requires attribution of authorship or
  source". The official text (planalto.gov.br, in.gov.br) could not be read on 2026-11-14 (blocked, connection error).
  The decree covers federal executive bodies, which includes IBGE; that reading is an inference from the decree's scope.

## 2. INPE Queimadas (fire hotspots)

| Question | Answer | Certainty | Source |
|---|---|---|---|
| May free software consume it? | Yes, as far as stated. The FAQ says the data are free and openly accessible, and that the hotspots can be used in any GIS "sem nenhum custo" (at no cost). No licence restricts the use. | medium | [FAQ](https://data.inpe.br/queimadas/faq/), fetched |
| Attribution? | Citation is expected, not stated as a legal condition. The FAQ gives the in-text form "(INPE, 2019)" and an ABNT NBR 6023 reference with the access date. | medium (form); none (legal requirement) | [FAQ](https://data.inpe.br/queimadas/faq/), fetched |
| Request limits | None stated. | none | [FAQ](https://data.inpe.br/queimadas/faq/), [dados abertos](https://data.inpe.br/queimadas/dados-abertos/), [BDQueimadas](https://terrabrasilis.dpi.inpe.br/queimadas/bdqueimadas/), all fetched |
| Commercial use | Not stated. The FAQ says the data are free "para qualquer usuário" (for any user); that is about cost, not licence. | none | FAQ, fetched |
| Caching | Not stated. | none | - |

Notes:

- FAQ quotes (fetched 2026-11-14): "Todos os dados e produtos ficam disponíveis para acesso livre sem nenhum custo após sua
  geração" (item 5); "Sim, e sem nenhum custo" (item 18, on use in any GIS).
- The dados abertos page and the BDQueimadas page contain no terms of use, licence or citation text. BDQueimadas only
  mentions the e-mail field of the export form.
- The README of this repository (before this change) says the INPE terms page had moved and the new address returned 404.
  No INPE page with terms of use was found in this check. The search pointed to a "Termos de uso" section on
  basedosdados.org, a third-party catalogue; it was not used as a source.
- The federal decree note in section 1 applies here by the same inference (INPE is a federal research institute).

## 3. IAG-USP seismology catalogue (main earthquake source)

| Question | Answer | Certainty | Source |
|---|---|---|---|
| May free software consume it? | No restriction and no permission found. | none | [About](https://www.moho.iag.usp.br/about/), fetched |
| Attribution? | Not found. The app credits "IAG-USP" anyway. | none | - |
| Request limits | Not found. | none | - |
| Commercial use | Not found. | none | - |
| Caching | Not found. The centre recommends its Brazilian Seismic Bulletin for a citable list of earthquakes, and says the live catalogue keeps changing and older events can be revised. | low | [Bulletin page](https://www.moho.iag.usp.br/eq/bulletin) and [catalogue](https://moho.iag.usp.br/rq/event), search snippets only |

Notes:

- The About page (fetched 2026-11-14) has no data policy, terms, licence or citation text. Its only mention of data is the
  mission to "Manter e gerenciar um banco de dados sismológicos" (maintain and manage a seismological database).
- The FDSN data centre listing (`https://www.fdsn.org/datacenters/detail/USP/`) was seen in a search result only.
- The app sends one query per 30 minutes with a 60 s timeout (`sources/iag.py`).

## 4. USGS earthquake feed (fallback when IAG-USP fails)

| Question | Answer | Certainty | Source |
|---|---|---|---|
| May free software consume it? | Yes, as far as stated: "USGS-authored or produced data and information are considered to be in the U.S. Public Domain." | high (public-domain statement); medium (consequence for software) | [Copyrights and credits](https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits), fetched |
| Attribution? | Requested, not required: "When using information from USGS information products, publications, or websites, we ask that proper credit be given." Sample credit: "Credit: U.S. Geological Survey". | high | same page, fetched |
| Request limits | The developers page has no rate-limit, caching, gzip or polling text. A 2022 USGS mailing-list post, seen in a search snippet only, says requests above a limit get HTTP 429 and that feed responses are cached for 60 s. | high (no limits on the developers page); low (429 and 60 s) | [Developers](https://earthquake.usgs.gov/earthquakes/feed/v1.0/developers.php), fetched; [realtime-feeds post](https://geohazards.usgs.gov/pipermail/realtime-feeds/2022-January/000028.html), search snippet |
| Commercial use | Not stated for the data. The copyright page gives a commercial-use rule only for multimedia with USGS personnel ("permission may be required"). | medium | same page, fetched |
| Caching | Not stated. | none | - |

Notes:

- The Feed Lifecycle Policy (`https://earthquake.usgs.gov/earthquakes/feed/policy.php`) returned HTTP 404 on 2026-11-14.
- The site's policy index (`https://www.usgs.gov/policies-and-notices`, fetched) names no data-reuse terms.
- The public-domain statement covers USGS-authored information. It does not cover non-USGS images, which the page says
  are copyrighted. The feed is USGS-authored.
- The app fetches the feed only when IAG-USP fails, at most once per 30-minute refresh.

## 5. SP Águas rain gauges (SIBH measurements API)

| Question | Answer | Certainty | Source |
|---|---|---|---|
| May free software consume it? | Not found for the API. | none | - |
| Attribution? | Not found for the API. Some SP Águas datasets on the state open-data portal are labelled CC BY 4.0 (monthly rainfall, SPI and drought files, not the measurements API). The dataset pages were not opened. | low (for portal labels) | [Dados Abertos SP listing](https://dadosabertos.sp.gov.br/dataset/?organization=agencia-de-aguas-do-estado-de-sao-paulo-sp-aguas&tags=dados+pluviom%C3%A9tricos), search snippet (the page itself was not reachable) |
| Request limits | Not found. | none | - |
| Commercial use | For the CC BY 4.0 portal datasets, commercial use is allowed with attribution (the licence's own terms). For the API: not found. | low | CC BY 4.0 label, search snippet |
| Caching | Not found for the API. | none | - |

Notes:

- The SP Águas FAQ (`https://www.spaguas.sp.gov.br/site/perguntas-frequentes/`) returned a certificate error on 2026-11-14
  (not reachable). The SIBH site (`https://sibh.daee.sp.gov.br/`) was seen in a search snippet only.
- The 60 requests per minute limit that a search result showed belongs to another municipal water utility (SAAE de
  Águas de Lindóia). It does not apply here and was not used.
- The app makes one request per 10-minute refresh, for the last hour.

## 6. IAG-USP Chuva Online radar (36 km frames)

| Question | Answer | Certainty | Source |
|---|---|---|---|
| May free software consume it? | Not found. The pages read have no reuse terms. | none | [Sobre](https://www.chuvaonline.iag.usp.br/sobre_chuva.php) and [home](https://www.chuvaonline.iag.usp.br/), fetched |
| Attribution? | Not found. The "Sobre" page carries the notice "© 1997-2026 STARNET Sferics Timing and Ranging Network". A source label "HL STORM-T/IAG" appears next to the timestamp on the home page. Neither is a reuse condition. | high (notice text) | same pages, fetched |
| Request limits | None published. The site says the radars "operam em tempo real com atualizações a cada seis minutos" (run in real time, updated every six minutes). | high (interval); none (limits) | Sobre, fetched |
| Commercial use | Not found. | none | - |
| Caching | Not found. The app caches decoded frames locally and sends `If-Modified-Since` for each frame, so an unchanged frame costs one 304 answer. | none (source); - (app) | `sources/chuva_online.py` |

Notes:

- The copyright notice is not a licence. Because no reuse terms were found, reuse of the frames needs confirmation from
  IAG-USP. The plugin reads the frames and draws their cells; it does not republish the images.

## Cross-source points

- Attribution is an open-data condition only where a licence says so. Of the sources above, the IBGE and INPE pages give
  the citation form; the USGS page asks for credit; the SP Águas portal labels CC BY 4.0 on some datasets (which requires
  attribution); the IAG-USP and Chuva Online pages say nothing.
- Requests from the plugin: the Terrascope User-Agent is sent with each request, gzip is accepted, the body is capped at
  40 MB, and at least one second separates two requests to the same host (`src/terrascope_br/net.py`).
- The plugin's own code is MIT (see `LICENSE`). It does not change the terms of the data.

## Attribution shown in the app

The BRASIL tab shows its credits in one line at the bottom-left of the map, and the core always adds
"© OpenStreetMap contributors" below it (`src/terrascope/core/ui/map.py`, `draw_attribution`).

- Before: `Data: IBGE, INPE, IAG-USP, SP Águas` (pt_BR: `Dados: IBGE, INPE, IAG-USP, SP Águas`).
- After: `Data: IBGE, INPE, IAG-USP, SP Águas, USGS` (pt_BR: `Dados: IBGE, INPE, IAG-USP, SP Águas, USGS`).

USGS was missing although the earthquake layer uses it as fallback. The core cuts the line to the map width, so on a
narrow terminal the end of the line can be cut.

## Open items

1. IBGE: read the terms or licence on an IBGE page (the terms address returned HTTP 403); confirm the API licence
   through IBGE's access-to-information channel.
2. INPE: find the "Termos de uso" page of Programa Queimadas, if one exists.
3. IAG-USP: ask for a reuse statement covering the FDSN query and the Chuva Online frames.
4. SP Águas: read the FAQ and the SIBH terms (the FAQ returned a certificate error).
5. USGS: read the Feed Lifecycle Policy (HTTP 404 here) and the 2022 rate-limit announcement in full.
6. Decree 8.777/2016, art. 2, III: check the wording on planalto.gov.br.

## Access log (2026-11-14)

| URL | Result |
|---|---|
| https://data.inpe.br/queimadas/faq/ | read |
| https://data.inpe.br/queimadas/dados-abertos/ | read |
| https://terrabrasilis.dpi.inpe.br/queimadas/bdqueimadas/ | read |
| https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits | read |
| https://www.usgs.gov/policies-and-notices | read |
| https://earthquake.usgs.gov/earthquakes/feed/v1.0/developers.php | read |
| https://earthquake.usgs.gov/earthquakes/feed/policy.php | HTTP 404 |
| https://servicodados.ibge.gov.br/api/docs | read |
| https://www.ibge.gov.br/acesso-informacao/institucional/termos-de-uso.html | HTTP 403 |
| https://www.ibge.gov.br/biblioteca-servicos.html | HTTP 403 (search snippet used) |
| IBGE Plano de Dados Abertos 2024-2025 (PDF) | downloaded, text not extracted |
| https://www.moho.iag.usp.br/about/ | read |
| https://www.chuvaonline.iag.usp.br/sobre_chuva.php | read |
| https://www.chuvaonline.iag.usp.br/ | read |
| https://www.spaguas.sp.gov.br/site/perguntas-frequentes/ | certificate error |
| https://dadosabertos.sp.gov.br/ (SP Águas listing) | not reachable (search snippet used) |
| https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2016/decreto/d8777.htm | blocked |
| https://www.in.gov.br/ (Decree 8.777/2016) | connection error |
