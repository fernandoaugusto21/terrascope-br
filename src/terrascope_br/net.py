"""HTTP for the data sources: the app's User-Agent, gzip, a size cap and
per-host pacing. Failures raise SourceError; the layer decides what to show."""

from __future__ import annotations

import gzip
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from terrascope.core.config import USER_AGENT

MAX_BYTES = 40 * 1024 * 1024  # no source here is bigger than this

class SourceError(Exception):
    """A source could not be read; the message is shown in the legend."""

_pace_lock = threading.Lock()
_last_request: dict[str, float] = {}

def _pace(url: str, min_gap: float) -> None:
    """Keep at least `min_gap` seconds between requests to the same host."""
    host = urlsplit(url).netloc
    with _pace_lock:
        now = time.monotonic()
        wait = _last_request.get(host, 0.0) + min_gap - now
        _last_request[host] = max(now, now + wait)
    if wait > 0:
        time.sleep(wait)

def decode_body(body: bytes, headers) -> bytes:
    """Undo gzip when the server sent it (it does even when not asked)."""
    encoding = (headers.get("Content-Encoding") or "").lower() if headers else ""
    if encoding == "gzip" or body[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(body)
        except (OSError, EOFError) as error:
            raise SourceError(f"bad gzip body: {error}") from error
    return body

def get(url: str, timeout: float, *, min_gap: float = 1.0,
        if_modified_since: str | None = None) -> tuple[int, bytes, dict]:
    """GET `url`. Returns (status, body, headers); status 304 means the copy
    the caller has is still current (body is b""). Raises SourceError."""
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Encoding": "gzip"}
    if if_modified_since:
        headers["If-Modified-Since"] = if_modified_since
    _pace(url, min_gap)
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_BYTES + 1)
            response_headers = dict(response.headers.items())
            status = response.status
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return 304, b"", dict(error.headers.items()) if error.headers else {}
        raise SourceError(f"HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise SourceError(f"{type(reason).__name__}: {reason}") from error
    if len(body) > MAX_BYTES:
        raise SourceError("response too large")
    return status, decode_body(body, response_headers), response_headers
