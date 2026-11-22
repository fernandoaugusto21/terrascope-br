"""The `python -m terrascope_br` check, and the core settings being read when used."""

from __future__ import annotations

import importlib.metadata
import importlib.util

from terrascope_br import __main__ as check_command

EXPECTED_LAYERS = check_command.EXPECTED_LAYERS


class _Dist:
    def __init__(self, name: str) -> None:
        self.metadata = {"Name": name}


class _EntryPoint:
    def __init__(self, name: str, dist: str = "terrascope-br") -> None:
        self.name = name
        self.dist = _Dist(dist)


def _tables(monkeypatch, tabs=("brasil",), layers=EXPECTED_LAYERS, core="1.1.0", plugins=True,
            providers=("br",), core_providers=True):
    table = {
        check_command.TAB_GROUP: [_EntryPoint(name) for name in tabs],
        check_command.LAYER_GROUP: [_EntryPoint(name) for name in layers],
        check_command.PROVIDER_GROUP: [_EntryPoint(name) for name in providers],
    }
    monkeypatch.setattr(
        importlib.metadata, "entry_points",
        lambda group=None, **_: list(table.get(group, [])),
    )
    monkeypatch.setattr(importlib.metadata, "version", lambda name: core)
    real_find_spec = importlib.util.find_spec

    def find_spec(name, *args, **kwargs):
        if name == "terrascope.core.plugins" and not plugins:
            return None
        if name == "terrascope.layers.weather.providers" and not core_providers:
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)


def test_check_passes_when_core_has_plugins_and_everything_is_registered(monkeypatch, capsys):
    _tables(monkeypatch)
    assert check_command.check() == []
    assert check_command.main() == 0
    assert "OK with terrascope 1.1.0" in capsys.readouterr().out


def test_core_without_plugin_support_is_reported(monkeypatch, capsys):
    _tables(monkeypatch, core="1.0.0", plugins=False)
    assert check_command.main() == 1
    assert "has no plugin support" in capsys.readouterr().out


def test_missing_layer_entry_point_is_reported(monkeypatch):
    _tables(monkeypatch, layers=[name for name in EXPECTED_LAYERS if name != "br_radar"])
    problems = check_command.check()
    assert len(problems) == 1
    assert "br_radar" in problems[0]


def test_a_layer_left_from_an_old_install_is_reported(monkeypatch):
    _tables(monkeypatch, layers=[*EXPECTED_LAYERS, "br_gauges"])
    problems = check_command.check()
    assert len(problems) == 1
    assert "br_gauges" in problems[0] and "reinstall" in problems[0]


def test_entry_points_of_other_packages_do_not_count(monkeypatch):
    table = [_EntryPoint(name, dist="someone-else") for name in ("brasil",)]
    monkeypatch.setattr(
        importlib.metadata, "entry_points",
        lambda group=None, **_: list(table) if group == check_command.TAB_GROUP else [],
    )
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "1.1.0")
    problems = check_command.check()
    assert any("brasil" in problem for problem in problems)


def test_core_not_installed_is_reported(monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    assert check_command.check() == ["terrascope is not installed in this environment"]


def test_core_without_tab_zoom_max_is_reported(monkeypatch):
    monkeypatch.setattr(check_command, "_core_tab_keys", lambda: frozenset({"name", "label", "view"}))
    problems = check_command.check()
    assert any("zoom_max" in line for line in problems)


def test_core_without_weather_providers_is_reported(monkeypatch):
    _tables(monkeypatch, core_providers=False)
    assert any("weather providers" in line for line in check_command.check())


def test_missing_weather_provider_entry_point_is_reported(monkeypatch):
    _tables(monkeypatch, providers=())
    assert any("terrascope.weather_providers / br" in line for line in check_command.check())


def test_both_core_problems_are_reported_together(monkeypatch):
    _tables(monkeypatch, core_providers=False)
    monkeypatch.setattr(check_command, "_core_tab_keys", lambda: frozenset({"name"}))
    problems = check_command.check()
    assert any("weather providers" in line for line in problems)
    assert any("zoom_max" in line for line in problems)


def test_core_without_layer_credits_is_reported(monkeypatch):
    from terrascope.core.layer import Layer

    _tables(monkeypatch)
    monkeypatch.delattr(Layer, "credits")
    problems = check_command.check()
    assert any("Layer.credits" in line for line in problems)
