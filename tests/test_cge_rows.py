"""CGE region details in the side panel (about 15 columns of value at the default width): one row per
flood point with the counts in the first line, one row per forecast day, and no number apart from its
unit. The rows are checked as the layer builds them."""

import time
from datetime import datetime, timedelta

import pytest

from terrascope.core import config
from terrascope_br.sources import cge

NBSP = "\u00a0"   # the no-break space between a number and its unit


@pytest.fixture
def layer(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    made = cge.CgeAlertsLayer()
    made.enabled = True
    made.floods_time = time.time()
    return made


def _point(active=True, passable=True, time_text="20:21 a", street="R JOSE DE ALENCAR (BRAS)"):
    return {"active": active, "passable": passable, "time": time_text, "street": street}


def _flood_rows(layer, count):
    points = [_point(time_text=f"{8 + index:02d}:00–", street=f"R NUMERO {index}") for index in range(count)]
    return layer._flood_rows({"floods": points})


# ---- flood rows -------------------------------------------------------------------------

def test_no_flood_point_today_is_one_row(layer):
    assert layer._flood_rows({"floods": []}) == [(cge._("FLOODS"), cge._("none today"))]


def test_one_active_point_has_its_counts_and_one_line(layer):
    rows = layer._flood_rows({"floods": [_point(passable=False)]})
    assert rows[0] == (cge._("FLOODS"), f"1{NBSP}{cge._('active')} · 1{NBSP}{cge._('today')}")
    assert rows[1] == ("", "20:21 R. Jose de Alencar (Bras) · " + cge._("impassable"))
    assert len(rows) == 2


def test_five_points_list_three_and_say_how_many_more(layer):
    rows = _flood_rows(layer, 5)
    assert rows[0][1] == f"5{NBSP}{cge._('active')} · 5{NBSP}{cge._('today')}"
    # Active first, then the latest of the day: the three latest are listed, then "+2 more".
    assert rows[1:] == [("", "12:00 R. Numero 4 · " + cge._("passable")),
                        ("", "11:00 R. Numero 3 · " + cge._("passable")),
                        ("", "10:00 R. Numero 2 · " + cge._("passable")),
                        ("", f"+2{NBSP}{cge._('more')}")]


def test_inactive_points_are_marked_over_and_do_not_count_as_active(layer):
    rows = layer._flood_rows({"floods": [_point(active=False, time_text="08:00–08:30", street="AV MORVAN")]})
    assert rows[0] == (cge._("FLOODS"), f"0{NBSP}{cge._('active')} · 1{NBSP}{cge._('today')}")
    assert rows[1] == ("", f"08:00 Av. Morvan · {cge._('passable')} {cge._('(over)')}")


def test_the_start_time_is_the_clock_time_whatever_the_range():
    assert cge.clock("20:21 a") == "20:21"
    assert cge.clock("17:18–19:40") == "17:18"
    assert cge.clock("") == ""


# ---- title case of the CGE's streets and districts -----------------------------------------

@pytest.mark.parametrize("raw, shown", [
    ("R JOSE DE ALENCAR (BRAS)", "R. Jose de Alencar (Bras)"),
    ("RUA DA CONSOLACAO", "R. da Consolacao"),
    ("R DA CONSOLACAO", "R. da Consolacao"),
    ("AV MORVAN DIAS DE FIGUEIREDO", "Av. Morvan Dias de Figueiredo"),
    ("AV DOS BANDEIRANTES", "Av. dos Bandeirantes"),
    ("CAMPO LIMPO E SAO PAULO", "Campo Limpo e Sao Paulo"),
    ("SANTO-AMARO", "Santo-Amaro"),
    ("  VILA   MARIANA  ", "Vila Mariana"),
    ("R. JOSE DE ALENCAR", "R. Jose de Alencar"),
])
def test_street_and_district_names_read_in_title_case(raw, shown):
    assert cge.street_name(raw) == shown


def test_title_case_keeps_the_particles_lower_case_except_the_first_word():
    assert cge.title_case("DA SILVA DO NORTE") == "Da Silva do Norte"
    assert cge.title_case("DA SILVA", first_is_capital=False) == "da Silva"


# ---- forecast rows ----------------------------------------------------------------------

def _forecast(now, today_summary="Pancadas isoladas", tomorrow_summary="Pancadas de chuva"):
    return [
        {"date": now.date().isoformat(), "summary": today_summary, "tmin": 22, "tmax": 32},
        {"date": (now.date() + timedelta(days=1)).isoformat(), "summary": tomorrow_summary,
         "tmin": 22.4, "tmax": 29},
    ]


def test_the_forecast_is_one_row_per_day_with_an_en_dash_and_a_footnote():
    now = datetime(2026, 11, 23, 10, 0)
    rows = cge.forecast_rows(_forecast(now), now)
    assert rows == [
        (cge._("TODAY"), f"Pancadas isoladas · 22–32{NBSP}°C"),
        (cge._("TOMORROW"), f"Pancadas de chuva · 22–29{NBSP}°C"),
        ("", cge._("(whole city)")),
    ]


def test_a_day_without_its_range_or_summary_is_still_one_row():
    now = datetime(2026, 11, 23, 10, 0)
    assert cge.forecast_day_text({"summary": None, "tmin": None, "tmax": 30}) == "—"
    assert cge.forecast_day_text({"summary": "Sol", "tmin": 20, "tmax": None}) == "Sol"
    assert cge.forecast_rows([], now) == []                     # no day in the forecast: no rows


def test_tomorrow_alone_when_the_forecast_starts_tomorrow():
    now = datetime(2026, 11, 23, 22, 0)
    rows = cge.forecast_rows([_forecast(now)[1]], now)
    assert [name for name, _value in rows] == [cge._("TOMORROW"), ""]


# ---- no number apart from its unit ----------------------------------------------------------

def test_a_number_and_its_unit_are_joined_by_a_no_break_space():
    assert cge._number_text(16.0, "mm").endswith(f"{NBSP}mm")
    assert cge._number_text(21.9, "°C").endswith(f"{NBSP}°C")
    assert " " not in cge._number_text(21.9, "°C")          # no ordinary space inside the value
    assert cge._number_text(None, "mm") == ""


def test_the_station_row_keeps_each_number_with_its_unit(monkeypatch):
    from terrascope_br.sources import weather_providers as wp

    reading = {"temperature": 21.9, "windspeed": 3.0}
    monkeypatch.setattr(wp, "_page", lambda url, keep: "")
    monkeypatch.setattr(cge, "parse_station_list", lambda page: {"SE": "503"})
    monkeypatch.setattr(wp, "parse_cge_station", lambda page, forecast, now: reading)
    monkeypatch.setattr(wp, "_station_value", lambda page, label: "16,0")
    monkeypatch.setattr(wp, "_number", lambda text: 16.0)
    monkeypatch.setattr(wp, "parse_cge_forecast", lambda page: [])
    station, _forecast_rows = cge.region_details("SE", datetime(2026, 11, 23, 10, 0))
    assert station.count(f"{NBSP}°C") == 1 and station.count(f"{NBSP}mm") == 1
    assert " mm" not in station and " °C" not in station


def test_no_forecast_is_read_as_unavailable(monkeypatch):
    from terrascope_br.sources import weather_providers as wp

    def broken(url, keep):
        raise wp.WeatherFetchError("down")

    monkeypatch.setattr(wp, "_page", broken)
    station, rows = cge.region_details("CV", datetime(2026, 11, 23, 10, 0))
    assert rows == [(cge._("FORECAST"), cge._("forecast unavailable"))]


def test_the_region_forecast_rows_come_from_the_cache_in_the_info_box(layer, monkeypatch):
    layer.selected = {"code": "SE", "region": "Se", "zone": "", "state": "observacao", "floods": []}
    monkeypatch.setattr(layer, "_region_details", lambda code: {
        "station": "S", "forecast": [(cge._("TODAY"), "F"), ("", cge._("(whole city)"))]})
    rows = layer.info_rows()
    assert (cge._("TODAY"), "F") in rows and ("", cge._("(whole city)")) in rows
