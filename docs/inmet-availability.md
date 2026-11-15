# INMET endpoint availability

Probe run on 2026-11-14, UTC. The script is `scripts/probe_inmet.py` (standard library only).

## Conclusion

- An INMET layer is viable with the endpoints below. In three rounds, every request to a valid route, sent with the
  package's own User-Agent (`terrascope/1.0`), completed DNS, TLS 1.2 with a verified certificate, and returned HTTP
  200 or 204, with no timeout and no server error.
- Use: `apiprevmet3` `/previsao/<geocode>` (forecast), `/avisos/ativos` (active warnings), `/municipios` (geocode
  list); `apitempo` `/estacoes/T` (station catalogue); `apivime` `/BRA` and `apisat` `/BR` (image lists, see the
  size note below).
- Do not depend on `apitempo` `/estacao/dados/<date>` yet. It returns 404 `E_ROUTE_NOT_FOUND` in all rounds, and the
  period form `/estacao/<start>/<end>/<code>` returns an empty 204. No station observations were retrieved, so a
  station-observation layer is not confirmed.
- Do not send a `curl` or other default HTTP-client User-Agent. With `curl/8.23.0` the server accepted the TLS
  handshake and then closed the connection without a response, in all 33 verified attempts. Browser and package
  User-Agents got normal responses. The cause was not identified on the server side.
- The outage reported on 2026-11-10 (DNS and connection timeouts, then an SSL error) did not reproduce. No TLS
  verification failure occurred in any of the 99 verified attempts of the main run. 9 of 207 DNS lookups took about
  12 s, so DNS delay is a plausible part of the earlier timeouts, but this is not proven.
- Scope limits: one network, one day, one test host. The terms of use of each INMET service were not checked here.

## Method

- Main run: 3 rounds, started 2026-11-14 18:58:03, 19:01:52 and 19:05:55 UTC, about 3 minutes apart. Each round
  probed 11 targets with the `terrascope` User-Agent and with `browser` and `curl` User-Agents for comparison. Each
  target was also probed without certificate verification (`--no-verify-diag`) as a diagnostic only. The package
  never disables verification; all status results above come from verified connections.
- Follow-up run: 3 rounds, 19:08:40, 19:11:44 and 19:14:59 UTC, for the satellite list and the station-data routes,
  after raising the script's body-size cap from 4 MiB to 16 MiB. The satellite list is 6.75 MB, and the first run
  truncated it.
- Per probe the script records: DNS (addresses and time), TCP connect, TLS (version, cipher, certificate subject,
  issuer, expiry, and the verified chain length), then HTTP (status, time to headers, total time, content type,
  size, a short sample and the detected format).
- Certificate chain (checked with `openssl s_client`, same for every host): leaf `*.inmet.gov.br` issued by Let's
  Encrypt `YR1`, then `ISRG Root YR`, then `ISRG Root X1`. The leaf expires on 2026-12-08, so a renewal is due in
  about three weeks.

## Results per endpoint

Times are for the `terrascope/1.0` User-Agent, verified TLS. HTTP is given for rounds 1, 2 and 3. Latency is the
median total time for the request.

| Endpoint | DNS (r1 / r2 / r3) | TLS | HTTP (r1 / r2 / r3) | Median latency | Format and sample | Result |
|---|---|---|---|---|---|---|
| `apiprevmet3` `/previsao/3550308` (São Paulo) | 12.0 s / 39 ms / 5 ms | OK | 200 / 200 / 200 | 488 ms | JSON object, 252 KB; keyed by geocode, then date (`14/11/2026`), then period (`manha`) | OK |
| `apiprevmet3` `/avisos/ativos` | 1 ms / 1 ms / 1 ms | OK | 200 / 200 / 200 | 450 ms | JSON object (`hoje`, ...), 345 KB | OK |
| `apiprevmet3` `/municipios` | 1 / 1 / 1 ms | OK | 200 / 200 / 200 | 544 ms | JSON array of 5,570 items (`geocode`, `nome`, `sigla`, coordinates), 673 KB | OK |
| `apitempo` `/` | 1 ms / 9 ms / 12.1 s | OK | 404 / 404 / 404 | 172 ms | `E_ROUTE_NOT_FOUND` (plain text) | Route missing; not a service outage |
| `apitempo` `/estacoes/T` | 1 / 1 / 2 ms | OK | 200 / 200 / 200 | 341 ms | JSON array of 668 stations (`CD_ESTACAO`, `DC_NOME`, `SG_ESTADO`, ...), 261 KB; no token needed | OK |
| `apitempo` `/estacao/dados/2026-11-13` | 7 ms / 12.0 s / 1 ms | OK | 404 / 404 / 404 | 178 ms | `E_ROUTE_NOT_FOUND` for this path | Route not confirmed |
| `apitempo` `/estacao/2026-11-13/2026-11-13/A472` (rerun) | 0.9 / 1.2 / 0.9 ms | OK | 204 / 204 / 204 | 167 ms | Empty body | Answers, but no data returned |
| `apisat` `/` | 1 ms / 12.0 s / 12.0 s | OK | 404 / 404 / 404 | 163 ms | `E_ROUTE_NOT_FOUND` | Root has no content |
| `apisat` `/BR` (rerun, 3 rounds) | 0.6 / 8.3 / 0.9 ms | OK | 200 / 200 / 200 | 2,345 ms | JSON array of 10 frames with `base64` PNG, 6.75 MB; metadata: satellite, parameter, `data`, `hora` | OK, heavy |
| `apivime` `/BRA` | 1 / 12.0 s / 12.0 s | OK | 200 / 200 / 200 | 811 ms | JSON array of 6 items (`nome`, `modelo`, ...), 789 KB | OK |
| `satelite.inmet.gov.br/` | 1 / 7 ms / 12.0 s | OK | 200 / 200 / 200 | 173 ms | HTML, 3 KB | OK (page, not data) |
| `portal.inmet.gov.br/` | 1 / 21 / 47 ms | OK | 200 / 200 / 200 | 1,179 ms | HTML, 77 KB | OK (page, not data) |

In the main run, all 99 verified attempts completed DNS and TLS. Of the 48 requests to valid routes sent with
`terrascope/1.0` or `browser`, 47 returned 200. The exception was one `browser` request to `apivime` in round 1,
which the server closed without a response. Its later attempts returned 200.

### User-Agent comparison (same hosts, same rounds)

| User-Agent | Verified HTTP results across 33 requests per UA | Notes |
|---|---|---|
| `terrascope/1.0` (package) | 24 of 24 valid-route requests: 200 or 204; 9 expected 404 (`/`, dated station path) | |
| `browser` (Chrome string) | 23 of 24 valid-route requests: 200; one `apivime` round-1 close | Also 404 on the same 9 expected routes |
| `curl/8.23.0` | 0 of 33: TLS completes, then the server closes the connection without a response | Every host, every round |

The `curl` pattern held for every host, including those answering the other two User-Agents. It is consistent with a
filter at the edge that rejects this client string. It was not tested whether the server also rejects other tool
User-Agents (for example Python's default), and this run did not test that.

### Transient and timing events

- DNS: 9 of 207 lookups took about 12 s; every other lookup took under 1 s. The slow lookups were spread across hosts
  and rounds. The resolver used by the test host was not identified.
- `apivime` `/BRA`, browser User-Agent, round 1: connection closed without a response. Retried in rounds 2 and 3, both
  200. The `terrascope/1.0` request in round 1 succeeded.
- `portal` `/`, unverified diagnostic request, round 2: HTTP 503. The verified request in the same round returned 200.

## Notes for the layer

- Routes requested by the task with no working data route: `apitempo` `/estacao/dados/<date>` (404). Its correct form is
  not confirmed. The portal's own JavaScript does not call that path. It does reference `/estacao/front`, which
  returned 404 on GET; it may be a POST endpoint, which was not tested.
- Token: none of the routes that returned data asked for a token (no 401 or 403 in any round). Whether the
  station-observation API needs a token is not known.
- Size: the satellite list is 6.75 MB of JSON, mostly base64 images, so it should be cached and fetched no more often
  than the source updates. It is well under the 40 MB cap in the package's `net.py`, but the cap is still needed.
- The package already backs off and keeps the last good data on failure. The transient events above fit that
  behaviour and do not need a new retry scheme.

## Reproducing

    python scripts/probe_inmet.py                                  # default targets, one round, terrascope UA
    python scripts/probe_inmet.py --rounds 3 --interval 180 --ua terrascope browser curl --no-verify-diag \
        --json-out probe.json
    python scripts/probe_inmet.py --only apisat-br tempo-dados tempo-periodo --rounds 3 --interval 180

The script uses only the standard library and Python 3.10 or later. The chain-of-certificates check in this document
used `openssl s_client`.
