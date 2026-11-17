"""The rain gauges have their own on/off switch, like the global rain layer:
the gauges are dense around Sao Paulo and cover the map there."""

from __future__ import annotations

from terrascope.core.view import View

from terrascope_br.base import SourceLayer
from terrascope_br.sources import spaguas
from terrascope_br.sources.inpe import FireLayer

SAO_PAULO_VIEW = View(-47.0, -22.5, 6.0, 182, 50)
READING = {"lat": -23.55, "lon": -46.63, "mm": 0.0, "station": "Se", "owner": "SP Aguas",
           "city": "Sao Paulo/SP", "updated": None}


def _gauges() -> spaguas.RainGaugeLayer:
    layer = spaguas.RainGaugeLayer()
    layer.enabled = True
    layer.items = [READING]
    layer.data_time = 0.0
    return layer


def test_switch_by_key_and_by_legend_line():
    layer = _gauges()
    assert layer.handle_key(ord("x")) is False
    assert layer.handle_key(ord("G")) is True
    assert layer.shown is False
    assert layer.enabled is True, "the key must not turn the layer off for the tab (its line would vanish)"
    title = layer.legend_lines()[0]
    assert title.action == spaguas.RainGaugeLayer.switch_action
    assert title.choice_index == 1
    assert len(layer.legend_lines()) == 1  # no key while off
    layer.press_legend(spaguas.RainGaugeLayer.switch_action)
    assert layer.shown is True
    assert layer.legend_lines()[0].choice_index == 0


def test_switched_off_draws_no_marker_and_forgets_the_selection():
    layer = _gauges()
    assert len(layer.render(SAO_PAULO_VIEW).markers) == 1
    layer.selected = READING
    layer.handle_key(ord("g"))
    assert layer.render(SAO_PAULO_VIEW).markers == []
    assert layer.last_markers == []
    assert layer.selected is None


def test_does_not_fetch_while_switched_off(monkeypatch):
    calls = []
    layer = _gauges()
    monkeypatch.setattr(layer, "fetch", lambda: calls.append(1))
    layer.shown = False
    assert layer.refresh_if_due(force=True) is False
    assert calls == []


def test_layers_without_a_switch_keep_the_core_key():
    fires = FireLayer()
    assert SourceLayer.switch_action == ""
    assert fires.switch_action == ""
    fires.enabled = True
    if fires.toggle_key:
        assert fires.handle_key(ord(fires.toggle_key)) is True
        assert fires.enabled is False, "without a switch the key keeps turning the layer off"
    assert fires.legend_lines()[0].action in (None, "")
