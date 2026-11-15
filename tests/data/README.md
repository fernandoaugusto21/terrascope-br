Samples of public responses kept to exercise the parsers offline. Trimmed where
noted (SP Águas: first 30 readings, with up to 12 that have rain; INPE index: last 40 links).
Captured on 2026-11-10 from the public endpoints listed in the package README.

`inmet_avisos_sample.json`: INMET warnings (`https://apiprevmet3.inmet.gov.br/avisos/ativos`), captured on
2026-11-14 at about 15:55 (UTC-3). Trimmed to the first two warnings of "hoje" and the first of "futuro"; the `icone` field (a base64
image) is removed. The "futuro" entry repeats the second "hoje" entry (same id), as the API does.
