"""Core settings are read when they are used, not when this package is imported."""

from __future__ import annotations

from terrascope.core import config
from terrascope_br import base


def test_offline_flag_set_after_import_stops_fetches(monkeypatch):
    """base.py used to copy FETCH_NEW_DATA_FROM_API at import; a later --offline
    must still stop the fetches, so the value is read when it is used."""

    class Source(base.SourceLayer):
        source_name = "test"

        def fetch(self):
            raise AssertionError("no request may be made with --offline")

    layer = Source()
    layer.enabled = True
    monkeypatch.setattr(config, "FETCH_NEW_DATA_FROM_API", False)
    assert layer.refresh_if_due(force=True) is False
