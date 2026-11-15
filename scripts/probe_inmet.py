#!/usr/bin/env python3
"""Availability probe for INMET endpoints: DNS, TCP, TLS and HTTP, one row per step.

Standard library only (Python 3.10+). This is a diagnostic tool, not part of the
package: nothing under src/ imports it. Every result that is reported as an
endpoint's status uses certificate verification. The unverified handshake
(--no-verify-diag) exists only to tell a certificate-chain problem apart from a
transport problem, and its rows are labelled "NOVERIFY" in the output.

Examples:
    python scripts/probe_inmet.py
    python scripts/probe_inmet.py --rounds 3 --interval 180 --json-out probe.json
    python scripts/probe_inmet.py --url https://apisat.inmet.gov.br/some/path --ua browser
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import http.client
import json
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

# "terrascope" is what the package sends (terrascope core default for data.user_agent).
# The others are for comparison only: they show whether the server reacts to the client string.
USER_AGENTS = {
    "terrascope": "terrascope/1.0",
    "python": f"Python-urllib/{sys.version_info.major}.{sys.version_info.minor}",
    "curl": "curl/8.23.0",
    "browser": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
}

MAX_BODY_BYTES = 16 * 1024 * 1024  # the satellite list is about 6.7 MB; larger bodies are marked truncated
SAMPLE_CHARS = 120


def default_targets() -> list[tuple[str, str]]:
    """(label, url) pairs. The station-data path uses yesterday (UTC), which is complete."""
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    return [
        ("prev-sp", "https://apiprevmet3.inmet.gov.br/previsao/3550308"),
        ("prev-avisos", "https://apiprevmet3.inmet.gov.br/avisos/ativos"),
        ("prev-municipios", "https://apiprevmet3.inmet.gov.br/municipios"),
        ("tempo-root", "https://apitempo.inmet.gov.br/"),
        ("tempo-estacoes", "https://apitempo.inmet.gov.br/estacoes/T"),
        ("tempo-dados", f"https://apitempo.inmet.gov.br/estacao/dados/{yesterday}"),
        # Station A472 (Acajutiba) comes from /estacoes/T; the path form is /estacao/<start>/<end>/<code>.
        ("tempo-periodo", f"https://apitempo.inmet.gov.br/estacao/{yesterday}/{yesterday}/A472"),
        ("apisat-root", "https://apisat.inmet.gov.br/"),
        # Satellite and radar image lists, found in the portal's app.js (the page reads them as base64 images).
        ("apisat-br", "https://apisat.inmet.gov.br/BR"),
        ("apivime-bra", "https://apivime.inmet.gov.br/BRA"),
        ("satelite-page", "https://satelite.inmet.gov.br/"),
        ("portal", "https://portal.inmet.gov.br/"),
    ]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def describe(error: BaseException) -> str:
    text = " ".join(str(error).split())
    if len(text) > 180:
        text = text[:177] + "..."
    name = type(error).__name__
    return f"{name}: {text}" if text else name


def run_with_timeout(function, timeout: float):
    """Run `function` in a daemon thread. getaddrinfo has no timeout of its own."""
    outcome: dict = {}

    def target() -> None:
        try:
            outcome["value"] = function()
        except BaseException as error:  # reported to the caller below
            outcome["error"] = error

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"no answer within {timeout:g} s")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def make_context(verify: bool) -> ssl.SSLContext:
    if verify:
        return ssl.create_default_context()
    # Diagnostic only (--no-verify-diag). Never used for a result reported as the endpoint status.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def check_dns(host: str, port: int, timeout: float) -> dict:
    start = time.perf_counter()
    try:
        infos = run_with_timeout(
            lambda: socket.getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout
        )
    except socket.gaierror as error:
        return {"ok": False, "ms": elapsed_ms(start), "error": describe(error)}
    except TimeoutError as error:
        return {"ok": False, "ms": elapsed_ms(start), "error": describe(error)}
    ips = sorted({info[4][0] for info in infos})
    return {"ok": True, "ms": elapsed_ms(start), "ips": ips}


def _name_dict(rdns) -> dict:
    return {key: value for rdn in rdns for key, value in rdn}


def check_tls(host: str, port: int, timeout: float, verify: bool) -> dict:
    """TCP connect, then TLS handshake. Records version, cipher and the leaf certificate."""
    context = make_context(verify)
    start = time.perf_counter()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as error:
        return {"ok": False, "stage": "tcp", "ms": elapsed_ms(start), "error": describe(error)}
    tcp_ms = elapsed_ms(start)
    handshake_start = time.perf_counter()
    try:
        tls_sock = context.wrap_socket(sock, server_hostname=host)
    except ssl.SSLCertVerificationError as error:
        sock.close()
        return {"ok": False, "stage": "tls-cert", "tcp_ms": tcp_ms,
                "ms": elapsed_ms(handshake_start), "error": describe(error)}
    except (ssl.SSLError, OSError) as error:
        sock.close()
        return {"ok": False, "stage": "tls", "tcp_ms": tcp_ms,
                "ms": elapsed_ms(handshake_start), "error": describe(error)}
    try:
        result = {
            "ok": True,
            "verified": verify,
            "tcp_ms": tcp_ms,
            "ms": elapsed_ms(handshake_start),
            "version": tls_sock.version(),
            "cipher": (tls_sock.cipher() or ("?",))[0],
        }
        der = tls_sock.getpeercert(binary_form=True)
        if der:
            result["leaf_sha256"] = hashlib.sha256(der).hexdigest()[:16]
        if verify:
            cert = tls_sock.getpeercert() or {}
            result["subject_cn"] = _name_dict(cert.get("subject", ())).get("commonName")
            result["issuer_cn"] = _name_dict(cert.get("issuer", ())).get("commonName")
            if cert.get("notAfter"):
                not_after = ssl.cert_time_to_seconds(cert["notAfter"])
                result["not_after_utc"] = datetime.fromtimestamp(
                    not_after, timezone.utc).strftime("%Y-%m-%d")
            sans = [value for kind, value in cert.get("subjectAltName", ()) if kind == "DNS"]
            result["san_count"] = len(sans)
        chain_fn = getattr(tls_sock, "get_verified_chain", None)  # Python 3.13+
        if verify and chain_fn is not None:
            chain = chain_fn() or []
            result["chain_len"] = len(chain)
            result["chain_sha256"] = [
                hashlib.sha256(item if isinstance(item, bytes) else item.public_bytes()).hexdigest()[:12]
                for item in chain
            ]
        return result
    finally:
        tls_sock.close()


def _summarize_body(body: bytes, content_type: str) -> tuple[str, str]:
    """Returns (format, short sample) without keeping more than SAMPLE_CHARS of text."""
    if body[:8] == b"\x89PNG\r\n\x1a\n":
        return "png image", body[:8].hex()
    if body[:3] == b"\xff\xd8\xff":
        return "jpeg image", body[:8].hex()
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return "binary (not UTF-8)", body[:8].hex()
    sample = " ".join(text[:SAMPLE_CHARS].split())
    stripped = text.lstrip()
    if "json" in content_type or stripped[:1] in ("{", "["):
        try:
            value = json.loads(text)
        except ValueError:
            return "json (unparseable or truncated)", sample
        if isinstance(value, dict):
            keys = list(value)[:8]
            return f"json object, {len(value)} keys", sample + f" | keys: {keys}"
        if isinstance(value, list):
            return f"json array, {len(value)} items", sample
        return f"json {type(value).__name__}", sample
    if stripped[:1] == "<":
        return "html", sample
    return "text", sample


def check_http(url: str, user_agent: str, timeout: float, verify: bool) -> dict:
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=make_context(verify)))
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent, "Accept": "*/*", "Accept-Encoding": "gzip"}
    )
    start = time.perf_counter()
    status = None
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:  # an HTTP error is still a response
        response = error
    except urllib.error.URLError as error:
        reason = error.reason if isinstance(error.reason, BaseException) else error
        return {"ok": False, "ms": elapsed_ms(start), "error": describe(reason)}
    except (OSError, http.client.HTTPException) as error:
        return {"ok": False, "ms": elapsed_ms(start), "error": describe(error)}
    status = response.status if hasattr(response, "status") else response.code
    headers_ms = elapsed_ms(start)
    content_type = (response.headers.get("Content-Type") or "").lower()
    encoding = (response.headers.get("Content-Encoding") or "").lower()
    try:
        body = response.read(MAX_BODY_BYTES + 1)
    except (OSError, http.client.HTTPException) as error:
        return {"ok": False, "status": status, "headers_ms": headers_ms, "ms": elapsed_ms(start),
                "error": "body read: " + describe(error)}
    finally:
        response.close()
    total_ms = elapsed_ms(start)
    truncated = len(body) > MAX_BODY_BYTES
    body = body[:MAX_BODY_BYTES]
    if encoding == "gzip" or body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError) as error:
            return {"ok": False, "status": status, "headers_ms": headers_ms, "ms": total_ms,
                    "error": "bad gzip: " + describe(error)}
    fmt, sample = _summarize_body(body, content_type)
    return {
        "ok": 200 <= status < 300,
        "status": status,
        "headers_ms": headers_ms,
        "ms": total_ms,
        "final_url": response.geturl() if hasattr(response, "geturl") else url,
        "content_type": content_type or None,
        "bytes": len(body),
        "truncated": truncated,
        "format": fmt,
        "sample": sample,
    }


def probe(label: str, url: str, ua_name: str, verify: bool, timeout: float, round_no: int) -> dict:
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or 443
    record = {
        "round": round_no,
        "ts_utc": utc_now(),
        "label": label,
        "url": url,
        "ua": ua_name,
        "verify": verify,
    }
    dns = check_dns(host, port, timeout)
    record["dns"] = dns
    if not dns["ok"]:
        record["tls"] = None
        record["http"] = {"ok": False, "error": "skipped: DNS failed"}
        return record
    record["tls"] = check_tls(host, port, timeout, verify)
    record["http"] = check_http(url, USER_AGENTS[ua_name], timeout, verify)
    return record


def mark(ok: bool | None) -> str:
    if ok is None:
        return "-"
    return "OK" if ok else "FAIL"


def print_row(record: dict) -> None:
    dns = record["dns"]
    tls = record["tls"] or {}
    http = record["http"]
    dns_txt = mark(dns["ok"]) + (f" {','.join(dns.get('ips', []))}" if dns["ok"] else f" ({dns['error']})")
    tls_txt = mark(tls.get("ok")) if record["tls"] else "-"
    if record["tls"] and not tls.get("ok"):
        tls_txt += f" ({tls.get('stage')}: {tls.get('error')})"
    if http.get("status") is not None:
        http_txt = f"{mark(http['ok'])} HTTP {http['status']} {http.get('format', '')}"
    else:
        http_txt = f"{mark(False)} ({http.get('error')})"
    flag = "verify" if record["verify"] else "NOVERIFY"
    print(f"r{record['round']} {record['ts_utc']} {record['label']:<15} {record['ua']:<10} {flag:<8} "
          f"DNS {dns_txt}\n    TLS {tls_txt}\n    HTTP {http_txt} {http.get('ms', '')}ms", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe INMET endpoints (stdlib only).")
    parser.add_argument("--rounds", type=int, default=1, help="how many full rounds (default 1)")
    parser.add_argument("--interval", type=float, default=0.0,
                        help="seconds to wait between rounds (default 0)")
    parser.add_argument("--timeout", type=float, default=15.0, help="per-step timeout in seconds")
    parser.add_argument("--ua", nargs="+", default=["terrascope"], choices=sorted(USER_AGENTS),
                        help="user-agent presets to send (default: terrascope)")
    parser.add_argument("--url", action="append", default=[],
                        help="extra URL to probe (repeatable); labelled custom-N")
    parser.add_argument("--only", nargs="+", default=None,
                        help="restrict the default targets to these labels")
    parser.add_argument("--no-verify-diag", action="store_true",
                        help="also run an UNVERIFIED TLS+HTTP attempt per target, for diagnosis only")
    parser.add_argument("--json-out", help="write every record as JSON to this path")
    args = parser.parse_args(argv)

    targets = default_targets()
    if args.only:
        targets = [t for t in targets if t[0] in args.only]
    targets += [(f"custom-{i}", url) for i, url in enumerate(args.url, start=1)]

    records: list[dict] = []
    for round_no in range(1, args.rounds + 1):
        print(f"== round {round_no}/{args.rounds} started {utc_now()}", flush=True)
        for label, url in targets:
            for ua_name in args.ua:
                record = probe(label, url, ua_name, True, args.timeout, round_no)
                records.append(record)
                print_row(record)
                if args.no_verify_diag:
                    record = probe(label, url, ua_name, False, args.timeout, round_no)
                    records.append(record)
                    print_row(record)
        if round_no < args.rounds and args.interval > 0:
            print(f"== waiting {args.interval:g} s", flush=True)
            time.sleep(args.interval)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(records, handle, indent=2, ensure_ascii=True)
        print(f"wrote {len(records)} records to {args.json_out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
