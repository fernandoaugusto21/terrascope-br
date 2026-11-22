"""The legend says why a fetch failed in a few words, per source, not with the
exception text (which ran across the map: "ConnectionAbortedError: [WinError 10053]
Uma conexão estabelecida foi anulada pelo software no computador host")."""

from terrascope_br._i18n import _
from terrascope_br.base import ERROR_LIMIT, SourceLayer, short_error


def test_a_dropped_connection_is_named_as_such():
    text = "ConnectionAbortedError: [WinError 10053] Uma conexão estabelecida foi anulada pelo software no computador host"
    assert short_error(text) == _("connection dropped")
    assert short_error("RemoteDisconnected: Remote end closed connection without response") == _("connection dropped")
    assert short_error("[WinError 10061] No connection could be made") == _("connection refused")


def test_each_source_of_a_combined_message_keeps_its_name():
    text = "IAG-USP: gaierror: [Errno 11001] getaddrinfo failed; USGS: gaierror: [Errno 11001] getaddrinfo failed"
    short = short_error(text)
    assert short.startswith("IAG-USP: ") and "; USGS: " in short
    assert "Errno" not in short and "gaierror" not in short


def test_http_codes_and_unknown_errors_stay_short():
    assert short_error("HTTP Error 502: Bad Gateway") == "HTTP 502"
    long_text = "ValueError: " + "x" * 200
    assert len(short_error(long_text)) <= ERROR_LIMIT


def test_the_same_reason_is_not_repeated():
    assert short_error("timed out; timed out") == short_error("timed out")


def test_the_legend_uses_the_short_form_and_keeps_the_full_error():
    layer = SourceLayer()
    layer.error = "ConnectionAbortedError: [WinError 10053] Uma conexão estabelecida foi anulada"
    assert layer.freshness_text() == _("unavailable: %s") % _("connection dropped")
    assert "10053" in layer.error
