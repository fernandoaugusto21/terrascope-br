# Sources backlog

Live checks of the candidate sources that are not layers yet. All checks were made on 2026-11-14, between 15:56 and
16:05 (UTC-3), with plain HTTP GET or HEAD requests and no keys. "Implemented" means a layer in this package.

## Summary

| Source | Availability on the check | Format | Size | Key | Status |
|---|---|---|---|---|---|
| INMET warnings, `/avisos/ativos` | HTTP 200 | JSON, areas as GeoJSON text | 344,719 bytes | no | implemented as `br_inmet_alerts` |
| INMET forecast, `/previsao/<IBGE>` | HTTP 200 | JSON, icons as base64 | 252,179 bytes (one municipality) | no | not implemented |
| GOES-19 (INPE STAC `GOES19-L2-CMI-1`) | HTTP 200 | NetCDF4 (HDF5), one file per band | 24,523,811 bytes per band file | no | not implemented |
| MERGE hourly (INPE STAC `prec_merge_hourly-1`) | HTTP 200 | GRIB2 with a GrADS `.ctl` | 72,634 bytes per hour | no | not implemented |
| MERGE daily (INPE STAC `prec_merge_daily-1`) | HTTP 200 | GRIB2 with a GrADS `.ctl` | 486,545 bytes per day | no | not implemented |
| REDEMET radar | not testable without a key | not known | not known | yes | not implemented |

The previous test (see the task notes) found the INMET warnings endpoint down or with an SSL error. It answered
normally on 2026-11-14, with a valid certificate.

## INMET warnings (implemented)

- `GET https://apiprevmet3.inmet.gov.br/avisos/ativos`: HTTP 200, `application/json; charset=utf-8`, 344,719 bytes.
  The answer is an object with two lists, `hoje` (today) and `futuro` (the next days). The same warning id can be in
  both lists.
- Fields used: `id`, `codigo`, `descricao` (event), `severidade`, `aviso_cor` (hex colour), `inicio` and `fim` as
  `YYYY-MM-DD HH:MM` (no time zone given), `estados`, `geocodes` (IBGE codes, comma separated), `poligono` (a GeoJSON
  Polygon, written as a JSON string, lon/lat order), `encerrado`.
- On the check: 3 warnings in `hoje` and 4 in `futuro`, 7 rows for 6 distinct ids, all with an area and the same
  colour (`#FFFE00`, "Perigo Potencial").
- Implemented in `src/terrascope_br/sources/inmet.py`, with a sample in `tests/data/inmet_avisos_sample.json`.

## INMET forecast (not implemented)

- `GET https://apiprevmet3.inmet.gov.br/previsao/3550308` (São Paulo): HTTP 200, 252,179 bytes. The answer is keyed by
  the IBGE code, then by date (`DD/MM/YYYY`), then by period (`manha`, and the others), with `temp_max`, `temp_min`,
  `resumo`, wind fields, and `icone` as a base64 PNG data URI. Most of the size is the icons.
- Not a map layer: it is one forecast per municipality. It needs a municipality picker and a text panel in the UI,
  which the layer system does not have. The trailing slash in the URL gives the same answer.
- Terms of use: not checked.

## GOES-19 (satellite)

- STAC (`https://data.inpe.br/bdc/stac/v1/collections`) has `GOES19-L2-CMI-1` (GOES-19 Cloud & Moisture Imagery). The
  item for 2026-11-14 18:20 UTC has 16 NetCDF assets (one per band; band 2 also has a 1 km variant) and a PNG
  thumbnail.
- Band 13 file, `https://data.inpe.br/bdc/data/GOES19-L2-CMI/ch13/2026/11/S10161313_202611141820.nc`: HTTP 200,
  Content-Length 24,523,811 bytes. The first bytes are the HDF5 signature (`\x89HDF`), so the format is NetCDF4.
- CPTEC FTP (`https://ftp.cptec.inpe.br/goes/goes19/`) has more products: `retangular/ch11` to `ch16` (NetCDF files,
  e.g. `2026/11/14/S10165545_202611141850.nc`), `rgb/` (`rgb_natural`, `rgb_night`, and others) and `goes19_web/`. The
  `rgb/` folder layout was not confirmed: a guessed day path returned 404.
- To implement it, a NetCDF4/HDF5 reader is needed (`netCDF4` or `h5py`, both compiled wheels), plus the
  geostationary projection (x/y in radians, from the `goes_imager_projection` attributes) to place the pixels on the
  lon/lat grid. Both are heavier than the rest of the package.
- A georeferenced PNG product (`rgb/` or `goes19_web/`) would avoid the reader, if its layout and projection are
  confirmed. Not verified.

## MERGE precipitation (INPE)

- STAC: `prec_merge_hourly-1` and `prec_merge_daily-1`.
- Hourly: `https://data.inpe.br/bdc/data/prec_merge_hourly/GPM/HOURLY/2026/11/14/MERGE_CPTEC_2026111413.grib2`:
  HTTP 200, 72,634 bytes, with a 374-byte `.ctl`. Daily: `MERGE_CPTEC_20261113.grib2`, 486,545 bytes.
  The mirror on `https://ftp.cptec.inpe.br/modelos/tempo/MERGE/GPM/DAILY/` has year folders.
- Grid, from the `.ctl`: 493 x 701 points at 0.1 degree, lon -82.15 to -33.15, lat -56.95 to 13.05. Variables: `PREC`
  (surface precipitation, mm/h) and `NEST` (number of stations per grid point). The hourly `.ctl` has `tdef 13Z`.
- Discrepancy to check before implementing: the STAC item for file `2026111413` gives the datetime
  `2026-11-14T12:01Z`, while the `.ctl` says 13Z. The file name and the `.ctl` were trusted here.
- Format: GRIB edition 2 (the file starts with `GRIB`, edition byte `2`). Decoding needs eccodes (a C library, through
  `eccodes` or `cfgrib` with `xarray`) or a pure-Python GRIB2 reader. The packing used by `PREC` was not checked.
- No key. The files are small, so the cost is the decoder dependency, not the download.

## REDEMET radar (not implemented)

- `https://api-redemet.decea.mil.br/` and `https://api-redemet.decea.mil.br/radar/`, without a key: HTTP 303, with an
  empty body and `Location: https://ajuda.decea.mil.br/base-de-conhecimento/api-redemet-o-que-e/` (the API help page).
- The radar endpoint could not be tested without a key, and the response format is not known. The key must be
  registered by the user and kept in the user's config, never in the repository.
- Implementing it needs: a `redemet_key` setting read from the config, the endpoint and parameters from the
  registered API documentation, a sample response for the tests, and a legend that says when no key is set.

## Found on the way: weather radar CAPPI (not in the list)

- STAC `br-weather-radar-cappi-1` ("Brazilian Weather Radar Network - CAPPI") has CEMADEN radar products, e.g.
  `cemaden/saofrancisco/cappi/cappi3km_nc/2026/11/cappi3km_202611141825.nc`: HTTP 200, 1,009,052 bytes, NetCDF. It needs
  the same NetCDF reader as GOES-19. Not checked further.

## Next steps

1. Decide whether the package may depend on a NetCDF/HDF5 reader and a GRIB2 decoder. The core and this package have
   neither today, and both are compiled dependencies. The answer decides GOES-19, MERGE and the CAPPI radar together.
2. Check the MERGE time discrepancy (12:01Z in STAC against 13Z in the `.ctl`) on a second file.
3. Confirm the GOES-19 `rgb/` and `goes19_web/` layouts, in case a georeferenced image avoids the reader.
4. REDEMET: wait for a user key, then test the radar endpoint with it.
5. INMET forecast: a text panel for a chosen municipality, if it is wanted; it is not a map layer.
