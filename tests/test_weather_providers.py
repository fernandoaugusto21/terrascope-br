"""Weather providers for Brazil: CGE for the municipality of São Paulo, INMET (CPTEC as
fallback) for the forecast elsewhere, and the IBGE lookup of the municipality."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from terrascope.core import config
from terrascope.layers.weather.city_weather import WeatherFetchError
from terrascope.layers.weather.providers import OpenMeteoProvider, WeatherReading, fetch_reading
from terrascope_br import store
from terrascope_br.net import SourceError
from terrascope_br.sources import ibge
from terrascope_br.sources import weather_providers as wp

DATA = Path(__file__).parent / "data"
SP_CENTRE = (-23.55, -46.63)
GUARULHOS = (-23.45, -46.50)


def _read(name, encoding="utf-8"):
    return (DATA / name).read_text(encoding=encoding)


# ---- parsing (real pages and answers of 21/11/2026) -----------------------------

def test_cge_forecast_has_three_days_with_their_periods():
    days = wp.parse_cge_forecast(_read("cge_previsao_estendida.html"))
    assert [day["date"] for day in days] == ["2026-11-21", "2026-11-22", "2026-11-23"]
    today = days[0]
    assert (today["tmin"], today["tmax"]) == (21.0, 31.0)
    assert [part["part"] for part in today["periods"]] == [0, 1, 2, 3]
    assert [part["name"] for part in today["periods"]] == ["Overnight", "Morning", "Afternoon", "Night"]
    assert all(part["code"] == wp.wmo_code(part["summary"]) for part in today["periods"])
    assert today["summary"] == "Pancadas isoladas" and today["code"] == 80   # the worst period


def test_cge_parts_with_new_titles_still_count_for_the_day():
    page = _read("cge_previsao_estendida.html").replace("<h1>Madrug.</h1>", "<h1>Madrugada</h1>")
    page = page.replace("<h1>Tarde</h1>", "<h1>Entardecer</h1>")
    today = wp.parse_cge_forecast(page)[0]
    assert 0 in [part["part"] for part in today["periods"]]          # "Madrugada" is still the overnight
    assert 2 not in [part["part"] for part in today["periods"]]      # an unknown title is not drawn...
    assert today["code"] == 80                                       # ...but its conditions still count


def test_cge_station_gives_the_current_conditions():
    today = wp.parse_cge_forecast(_read("cge_previsao_estendida.html"))[0]
    now = wp.parse_cge_station(_read("cge_estacao_503.html"), [today], datetime(2026, 11, 21, 8, 30))
    assert now["temperature"] == 30.0 and now["windspeed"] == 10.8
    assert now["winddirection"] == 225.0                       # "SW"
    assert now["summary"] == "Nublado" and now["weathercode"] == 3   # the morning period
    assert now["time"] == "2026-11-21T08:30"


def test_cge_pages_without_data_are_errors():
    with pytest.raises(ValueError):
        wp.parse_cge_forecast("<html>manutenção</html>")
    with pytest.raises(ValueError):
        wp.parse_cge_station("<html></html>", [], datetime(2026, 11, 21))


def test_inmet_days_take_the_extremes_and_the_worst_period():
    days = wp.parse_inmet(json.loads(_read("inmet_previsao_3518800.json")), "3518800")
    assert len(days) == 5 and days[0]["date"] == "2026-11-21"
    assert days[0]["code"] == 95 and "trovoadas" in days[0]["summary"]
    assert all(day["tmin"] <= day["tmax"] for day in days)


def test_inmet_days_have_their_parts_and_the_sun_times():
    days = wp.parse_inmet(json.loads(_read("inmet_previsao_3518800.json")), "3518800")
    first = days[0]
    assert [part["part"] for part in first["periods"]] == [1, 2, 3]   # morning, afternoon, night
    assert first["periods"][0]["summary"] and first["periods"][0]["code"] is not None
    assert first["sunrise"] and first["sunrise"].startswith("2026-11-21T") and len(first["sunrise"]) == 16
    assert days[-1]["periods"] == []   # the last days come as one block, without parts
    assert wp._clock("2026-11-21", "5h07") == "2026-11-21T05:07" and wp._clock("2026-11-21", "") is None


def test_cptec_days_and_weather_words():
    days = wp.parse_cptec(_read("cptec_previsao_4480.xml", "latin-1"))
    assert len(days) == 7 and days[0]["summary"] == "Chuva" and days[0]["code"] == 61
    assert days[0]["uv"] == 0.0


@pytest.mark.parametrize(("text", "code"), [
    ("Céu claro", 0), ("Poucas nuvens", 1), ("Sol entre nuvens", 2), ("Parcialmente nublado", 2),
    ("Muitas nuvens", 3), ("Encoberto", 3), ("Nevoeiro", 45), ("Chuvisco", 51), ("Chuva", 61),
    ("Pancadas isoladas", 80), ("Nublado com pancadas de chuva e trovoadas isoladas", 95),
    ("Tempestade com granizo", 96), ("texto sem tempo", None),
])
def test_words_to_wmo_codes(text, code):
    assert wp.wmo_code(text) == code


def test_compass_points():
    assert wp.compass_degrees("SSE") == 157.5 and wp.compass_degrees("NO") == 315.0
    assert wp.compass_degrees("calmo") is None


# ---- where is a place ------------------------------------------------------------

def _square(code, west, south, east, north):
    ring = [[west, south], [east, south], [east, north], [west, north], [west, south]]
    return {"type": "Feature", "properties": {"codarea": code},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    ibge._parsed.clear()
    wp._found.clear()
    wp._pages.clear()
    wp._refused.clear()
    monkeypatch.delenv(wp.TOKEN_ENV, raising=False)
    monkeypatch.setattr(config, "USER_CONFIG_DIR", tmp_path / "config", raising=False)
    # The station readings of the fixtures are from 21/11/2026 19:00 UTC.
    monkeypatch.setattr(wp, "_now", lambda: datetime(2026, 11, 21, 19, 40, tzinfo=timezone.utc))
    # SP state; inside it the capital (a box round the centre) and Guarulhos to the north-east.
    store.write_bytes(store.path("ibge", "ufs.geojson"), json.dumps(
        {"features": [_square("35", -53.0, -25.5, -44.0, -19.5)]}).encode())
    store.write_bytes(store.path("ibge", "mun-35-minima.geojson"), json.dumps({"features": [
        _square(wp.SAO_PAULO, -46.83, -24.0, -46.36, -23.48),
        _square("3518800", -46.6, -23.48, -46.3, -23.3),
    ]}).encode())
    return tmp_path


def test_municipality_lookup_by_state_then_municipality(cache):
    assert wp.municipality_at(*SP_CENTRE) == wp.SAO_PAULO
    assert wp.municipality_at(*GUARULHOS) == "3518800"
    assert wp.municipality_at(-22.9, -43.2) is None          # outside the cached states
    assert not store.path("ibge", "municipios.geojson").exists()   # no national file


def test_a_missing_state_file_is_downloaded_for_that_state_only(cache, monkeypatch):
    store.path("ibge", "mun-35-minima.geojson").unlink()
    urls = []

    def fake_get(url, timeout, **kwargs):
        urls.append(url)
        return 200, json.dumps({"features": [_square(wp.SAO_PAULO, -46.83, -24.0, -46.36, -23.48)]}).encode(), {}

    monkeypatch.setattr(ibge, "get", fake_get)
    assert wp.municipality_at(*SP_CENTRE) == wp.SAO_PAULO
    assert len(urls) == 1 and "/estados/35?" in urls[0] and "qualidade=minima" in urls[0]


# ---- the providers ---------------------------------------------------------------

def _serve(monkeypatch, answers):
    """Make wp.get answer from `answers` ({url part: bytes or Exception})."""
    asked = []

    def fake_get(url, timeout, **kwargs):
        asked.append(url)
        for part, answer in answers.items():
            if part in url:
                if isinstance(answer, Exception):
                    raise answer
                return 200, answer, {}
        raise SourceError("HTTP 404")

    monkeypatch.setattr(wp, "get", fake_get)
    return asked


CGE_PAGES = {
    "previsao_estendida": (DATA / "cge_previsao_estendida.html").read_bytes(),
    "estacao.jsp": (DATA / "cge_estacao_503.html").read_bytes(),
}


def test_cge_answers_for_the_capital_only(cache, monkeypatch):
    _serve(monkeypatch, CGE_PAGES)
    cge = wp.CgeProvider()
    assert cge.covers(*SP_CENTRE) and not cge.covers(*GUARULHOS)
    reading = cge.fetch(*SP_CENTRE)
    assert reading.current["temperature"] == 30.0 and len(reading.forecast) == 3


def test_cge_station_down_still_gives_the_forecast(cache, monkeypatch):
    _serve(monkeypatch, {"previsao_estendida": CGE_PAGES["previsao_estendida"], "estacao.jsp": SourceError("HTTP 500")})
    reading = wp.CgeProvider().fetch(*SP_CENTRE)
    assert reading.current is None and reading.forecast


def test_inmet_for_other_municipalities_and_cptec_when_inmet_fails(cache, monkeypatch):
    inmet = (DATA / "inmet_previsao_3518800.json").read_bytes()
    asked = _serve(monkeypatch, {"apiprevmet3": inmet})
    federal = wp.FederalForecastProvider()
    reading = federal.fetch(*GUARULHOS)
    assert reading.forecast_source == "INMET" and reading.current is None and len(reading.forecast) == 5
    assert any(url.endswith("/previsao/3518800") for url in asked)

    store.write_bytes(store.path("ibge", ibge.NAMES_FILE),
                      json.dumps([{"municipio-id": 3518800, "municipio-nome": "Guarulhos", "UF-sigla": "SP"}]).encode())
    _serve(monkeypatch, {
        "apiprevmet3": SourceError("HTTP 503"),
        "listaCidades": b"<cidades><cidade><nome>Guarulhos</nome><uf>SP</uf><id>2075</id></cidade></cidades>",
        "/7dias/2075/": (DATA / "cptec_previsao_4480.xml").read_bytes(),
    })
    reading = federal.fetch(*GUARULHOS)
    assert reading.forecast_source == "CPTEC/INPE" and len(reading.forecast) == 7
    assert json.loads(store.read_bytes(store.path("cptec", wp.CPTEC_IDS_FILE))) == {"3518800": "2075"}


def test_both_federal_sources_down_is_an_error(cache, monkeypatch):
    _serve(monkeypatch, {"apiprevmet3": SourceError("HTTP 503"), "listaCidades": SourceError("HTTP 500")})
    store.write_bytes(store.path("ibge", ibge.NAMES_FILE),
                      json.dumps([{"municipio-id": 3518800, "municipio-nome": "Guarulhos", "UF-sigla": "SP"}]).encode())
    with pytest.raises(WeatherFetchError, match="INMET.*CPTEC"):
        wp.FederalForecastProvider().fetch(*GUARULHOS)


class _World(OpenMeteoProvider):
    def fetch(self, latitude, longitude):
        return WeatherReading(current={"temperature": 1.0, "weathercode": 0}, forecast=[{"date": "x"}],
                              current_source="Open-Meteo", forecast_source="Open-Meteo")


def test_the_chain_by_region(cache, monkeypatch):
    _serve(monkeypatch, {**CGE_PAGES, "apiprevmet3": (DATA / "inmet_previsao_3518800.json").read_bytes()})
    chain = [*wp.providers(), _World()]
    capital = fetch_reading(*SP_CENTRE, chain)
    assert (capital.current_source, capital.forecast_source) == ("CGE", "CGE")
    guarulhos = fetch_reading(*GUARULHOS, chain)
    assert (guarulhos.current_source, guarulhos.forecast_source) == ("Open-Meteo", "INMET")
    abroad = fetch_reading(-34.6, -58.4, chain)            # Buenos Aires: outside Brazil
    assert (abroad.current_source, abroad.forecast_source) == ("Open-Meteo", "Open-Meteo")


# ---- review fixes ------------------------------------------------------------------

def test_a_coastal_place_just_outside_the_coarse_outline_takes_the_nearest(cache):
    # 0.05 degrees east of the capital's box (on the "sea"): still São Paulo.
    assert wp.municipality_at(-23.7, -46.31) == wp.SAO_PAULO
    assert wp.municipality_at(-23.7, -46.0) is None          # 0.36 degrees away: not taken


def test_far_from_brazil_no_file_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    wp._found.clear()
    monkeypatch.setattr(ibge, "get", lambda *a, **k: pytest.fail("no download for Paris"))
    assert wp.municipality_at(48.85, 2.35) is None


def test_a_damaged_state_file_is_removed(cache):
    store.write_bytes(store.path("ibge", "mun-35-minima.geojson"), b'{"features": [{"properties": {"codarea": "1"}, '
                      b'"geometry": {"type": "Polygon", "coordinates": [[["a", "b"], ["c", "d"]]]}}]}')
    with pytest.raises(WeatherFetchError):
        wp.municipality_at(*SP_CENTRE)
    assert not store.path("ibge", "mun-35-minima.geojson").exists()


def test_the_current_words_need_a_forecast_for_today():
    days = wp.parse_cge_forecast(_read("cge_previsao_estendida.html"))
    page = _read("cge_estacao_503.html")
    late = wp.parse_cge_station(page, days, datetime(2026, 11, 24, 1, 0))   # the page still starts on the 21st
    assert late["summary"] is None and late["weathercode"] is None
    night = wp.parse_cge_station(page, days, datetime(2026, 11, 21, 23, 0))
    assert night["summary"] == "Pancadas isoladas"                       # "Noite"


def test_the_cge_clock_is_sao_paulo_time(cache, monkeypatch):
    _serve(monkeypatch, CGE_PAGES)
    seen = []
    real = wp.parse_cge_station
    monkeypatch.setattr(wp, "parse_cge_station", lambda page, forecast, now: seen.append(now) or real(page, forecast, now))
    wp.CgeProvider().fetch(*SP_CENTRE)
    assert seen[0].tzinfo is not None and seen[0].utcoffset() == wp.SAO_PAULO_TIME.utcoffset(seen[0])


def test_an_ambiguous_station_label_gives_nothing():
    page = _read("cge_estacao_503.html")
    assert wp._station_value(page, "Atual") is None                       # three "Atual" rows
    assert wp._station_value(page, "Velocidade") == "10.8 km/h"


def test_a_municipality_cptec_does_not_list_is_remembered(cache, monkeypatch):
    store.write_bytes(store.path("ibge", ibge.NAMES_FILE),
                      json.dumps([{"municipio-id": 3518800, "municipio-nome": "Guarulhos", "UF-sigla": "SP"}]).encode())
    asked = _serve(monkeypatch, {"apiprevmet3": SourceError("HTTP 503"), "listaCidades": b"<cidades></cidades>"})
    for _ in range(2):
        with pytest.raises(WeatherFetchError):
            wp.FederalForecastProvider().fetch(*GUARULHOS)
    assert sum("listaCidades" in url for url in asked) == 1


def test_an_unwritable_id_file_does_not_lose_the_cptec_forecast(cache, monkeypatch):
    store.write_bytes(store.path("ibge", ibge.NAMES_FILE),
                      json.dumps([{"municipio-id": 3518800, "municipio-nome": "Guarulhos", "UF-sigla": "SP"}]).encode())
    _serve(monkeypatch, {
        "apiprevmet3": SourceError("HTTP 503"),
        "listaCidades": b"<cidades><cidade><nome>Guarulhos</nome><uf>SP</uf><id>2075</id></cidade></cidades>",
        "/7dias/2075/": (DATA / "cptec_previsao_4480.xml").read_bytes(),
    })
    real_write = store.write_bytes
    monkeypatch.setattr(store, "write_bytes", lambda file, data: (_ for _ in ()).throw(PermissionError("locked"))
                        if file.name == wp.CPTEC_IDS_FILE else real_write(file, data))
    assert wp.FederalForecastProvider().fetch(*GUARULHOS).forecast_source == "CPTEC/INPE"


# ---- INMET stations: current conditions, with and without a key -------------------

FORECAST = (DATA / "inmet_previsao_3518800.json").read_bytes()
NEAREST = (DATA / "inmet_proxima_3518800.json").read_bytes()
STATIONS = (DATA / "inmet_estacoes_T.json").read_bytes()
A701_HOURS = json.dumps([
    {"CD_ESTACAO": "A701", "DT_MEDICAO": "2026-11-21", "HR_MEDICAO": "1800", "TEM_INS": "28.4",
     "VEN_VEL": "2.0", "VEN_DIR": "300"},
    {"CD_ESTACAO": "A701", "DT_MEDICAO": "2026-11-21", "HR_MEDICAO": "1900", "TEM_INS": "29.6",
     "VEN_VEL": "2.5", "VEN_DIR": "310"},
    {"CD_ESTACAO": "A701", "DT_MEDICAO": "2026-11-21", "HR_MEDICAO": "2000", "TEM_INS": None},
]).encode()


def test_without_a_key_the_nearest_station_answer_gives_the_current_conditions(cache, monkeypatch):
    asked = _serve(monkeypatch, {"/previsao/": FORECAST, "/estacao/proxima/3518800": NEAREST})
    reading = wp.FederalProvider().fetch(*GUARULHOS)
    assert reading.current_source == "INMET A701" and reading.forecast_source == "INMET"
    now = reading.current
    assert now["temperature"] == 29.1 and now["windspeed"] == pytest.approx(11.2) and now["winddirection"] == 297
    assert now["time"] == "2026-11-21T16:00"                    # 19:00 UTC in São Paulo time
    assert now["summary"] and now["weathercode"] is not None    # the afternoon part of today's forecast
    assert not any("apitempo" in url for url in asked)


def test_with_a_key_the_station_api_answers_for_the_nearest_station(cache, monkeypatch):
    monkeypatch.setenv(wp.TOKEN_ENV, "MY-KEY")
    asked = _serve(monkeypatch, {"/previsao/": FORECAST, "/estacoes/T": STATIONS,
                                 "/token/estacao/2026-11-20/2026-11-21/A701/MY-KEY": A701_HOURS})
    reading = wp.FederalProvider().fetch(*GUARULHOS)
    assert reading.current_source == "INMET A701"
    assert reading.current["temperature"] == 29.6 and reading.current["time"] == "2026-11-21T16:00"  # the newest with a value
    assert not any("/estacao/proxima/" in url for url in asked)


@pytest.mark.parametrize("refusal", [b"CHAVE INV\xc3\x81LIDA!", SourceError("HTTP 401"), SourceError("HTTP 403")])
def test_a_refused_key_falls_back_to_the_keyless_answer_and_is_not_sent_again(cache, monkeypatch, refusal):
    monkeypatch.setenv(wp.TOKEN_ENV, "OLD-KEY")
    asked = _serve(monkeypatch, {"/previsao/": FORECAST, "/estacoes/T": STATIONS,
                                 "/token/estacao/": refusal, "/estacao/proxima/3518800": NEAREST})
    reading = wp.FederalProvider().fetch(*GUARULHOS)
    assert reading.current_source == "INMET A701" and reading.current["temperature"] == 29.1   # keyless
    wp._pages.clear()
    wp.FederalProvider().fetch(*GUARULHOS)
    assert sum("/token/estacao/" in url for url in asked) == 1


class _OpenMeteo(OpenMeteoProvider):
    def fetch(self, latitude, longitude):
        return WeatherReading(current={"temperature": 25.0, "time": "2026-11-21T16:00"},
                              forecast=[{"date": "2026-11-21"}], current_source="Open-Meteo",
                              forecast_source="Open-Meteo")


@pytest.mark.parametrize("key", ["", "BAD-KEY"])
def test_no_station_reading_leaves_the_current_conditions_to_open_meteo_without_error(cache, monkeypatch, key):
    """No key, or a refused key, and the keyless answer is down too: the chain
    takes the current conditions from Open-Meteo, keeps the INMET forecast, and
    nothing on screen says the station failed except the provider's errors."""
    if key:
        monkeypatch.setenv(wp.TOKEN_ENV, key)
    _serve(monkeypatch, {"/previsao/": FORECAST, "/estacoes/T": STATIONS,
                         "/token/estacao/": b"CHAVE INV\xc3\x81LIDA!", "/estacao/proxima/": SourceError("HTTP 503")})
    reading = fetch_reading(*GUARULHOS, chain=[wp.FederalProvider(), _OpenMeteo()])
    assert reading.current_source == "Open-Meteo" and reading.current["temperature"] == 25.0
    assert reading.forecast_source == "INMET"


def test_a_far_or_old_station_reading_is_not_current(cache, monkeypatch):
    far = json.loads(NEAREST)
    far["estacao"]["LATITUDE"], far["estacao"]["LONGITUDE"] = "-22.0", "-47.9"   # about 170 km away
    _serve(monkeypatch, {"/previsao/": FORECAST, "/estacao/proxima/3518800": json.dumps(far).encode()})
    assert wp.FederalProvider().fetch(*GUARULHOS).current is None
    record = json.loads(NEAREST)["dados"]
    assert wp.parse_station_reading(record, datetime(2026, 11, 21, 23, 1, tzinfo=timezone.utc)) is None   # 4 h old
    assert wp.parse_station_reading({**record, "TEM_INS": None}, datetime(2026, 11, 21, 19, 30, tzinfo=timezone.utc)) is None


def test_the_key_can_live_in_the_config_folder(cache):
    assert wp.inmet_token() == ""
    folder = config.USER_CONFIG_DIR / "br"
    folder.mkdir(parents=True)
    (folder / "inmet_token.txt").write_text("# my INMET key\n  FILE-KEY  \n", encoding="utf-8")
    assert wp.inmet_token() == "FILE-KEY"


def test_the_key_check_command_says_where_the_key_is_and_what_inmet_says(cache, monkeypatch, capsys):
    from terrascope_br import __main__ as command

    assert command.main(["inmet-token"]) == 1 and "no INMET station key" in capsys.readouterr().out
    monkeypatch.setenv(wp.TOKEN_ENV, "BAD")
    _serve(monkeypatch, {"/token/estacao/": b"CHAVE INV\xc3\x81LIDA!"})
    assert command.main(["inmet-token"]) == 1 and "refused" in capsys.readouterr().out
    _serve(monkeypatch, {"/token/estacao/": A701_HOURS})
    assert command.main(["inmet-token"]) == 0 and "accepted" in capsys.readouterr().out
    _serve(monkeypatch, {"/token/estacao/": SourceError("HTTP 403")})
    assert command.main(["inmet-token"]) == 1 and "refused" in capsys.readouterr().out


def test_a_failing_or_empty_keyed_answer_falls_back_to_the_keyless_one(cache, monkeypatch):
    monkeypatch.setenv(wp.TOKEN_ENV, "GOOD-KEY")
    for keyed in (SourceError("HTTP 503"), b"", json.dumps([]).encode()):
        wp._pages.clear()
        _serve(monkeypatch, {"/previsao/": FORECAST, "/estacoes/T": STATIONS,
                             "/token/estacao/": keyed, "/estacao/proxima/3518800": NEAREST})
        reading = wp.FederalProvider().fetch(*GUARULHOS)
        assert reading.current_source == "INMET A701" and reading.current["temperature"] == 29.1
    assert not wp._refused   # a failure is not a refusal


def test_an_http_refusal_is_retried_later_a_text_refusal_not_in_this_run(cache, monkeypatch):
    monkeypatch.setenv(wp.TOKEN_ENV, "KEY")
    _serve(monkeypatch, {"/previsao/": FORECAST, "/estacoes/T": STATIONS,
                         "/token/estacao/": SourceError("HTTP 403"), "/estacao/proxima/3518800": NEAREST})
    wp.FederalProvider().fetch(*GUARULHOS)
    assert wp._refused["KEY"] != float("inf")
    wp._refused.clear()
    wp._pages.clear()
    _serve(monkeypatch, {"/previsao/": FORECAST, "/estacoes/T": STATIONS,
                         "/token/estacao/": b"CHAVE INV\xc3\x81LIDA!", "/estacao/proxima/3518800": NEAREST})
    wp.FederalProvider().fetch(*GUARULHOS)
    assert wp._refused["KEY"] == float("inf")


def test_an_empty_or_odd_station_list_is_not_kept(cache, monkeypatch):
    for odd in (b"[]", b'{"message": "erro"}'):
        _serve(monkeypatch, {"/estacoes/T": odd})
        with pytest.raises(WeatherFetchError):
            wp._stations()
        assert not store.path("inmet", wp.STATIONS_FILE).exists()
    store.write_bytes(store.path("inmet", wp.STATIONS_FILE), b"{damaged")
    _serve(monkeypatch, {"/estacoes/T": STATIONS})
    assert {station["code"] for station in wp._stations()} >= {"A701"}   # the damaged copy is replaced


@pytest.mark.parametrize("data", [
    "\ufeffBOM-KEY\n".encode("utf-8"), "BOM-KEY\n".encode("utf-16"), b"   # a comment\nBOM-KEY\n",
])
def test_key_files_from_windows_editors(cache, data):
    folder = config.USER_CONFIG_DIR / "br"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "inmet_token.txt").write_bytes(data)
    assert wp.inmet_token() == "BOM-KEY"


def test_the_time_and_part_of_the_day_are_the_places_own():
    record = json.loads(NEAREST)["dados"]   # 19:00 UTC
    assert wp.parse_station_reading(record, datetime(2026, 11, 21, 19, 30, tzinfo=timezone.utc),
                                    wp.local_zone("1200401"))["time"] == "2026-11-21T14:00"   # Rio Branco, UTC-5
    assert wp.local_zone("3518800").key == "America/Sao_Paulo" and wp.local_zone("2605459").key == "America/Noronha"
    days = wp.parse_inmet(json.loads(FORECAST), "3518800")
    small_hours = datetime(2026, 11, 21, 5, 0, tzinfo=timezone.utc)   # 02:00 in São Paulo
    morning = next(part for part in days[0]["periods"] if part["part"] == 1)
    assert wp._summary_now(days, small_hours) == morning["summary"]          # INMET has no overnight part
    afternoon = datetime(2026, 11, 21, 17, 0, tzinfo=timezone.utc)    # 14:00
    assert wp._summary_now(days, afternoon) == days[0]["periods"][1]["summary"]


def test_a_source_with_an_overnight_part_keeps_it():
    days = [{"date": "2026-11-22", "periods": [
        {"part": 0, "summary": "Céu claro"}, {"part": 1, "summary": "Nublado"}]}]
    small_hours = datetime(2026, 11, 22, 4, 0, tzinfo=timezone.utc)   # 01:00 in São Paulo
    assert wp._summary_now(days, small_hours) == "Céu claro"
    blank = [{"date": "2026-11-22", "periods": [{"part": 0, "summary": ""}, {"part": 1, "summary": "Nublado"}]}]
    assert not wp._summary_now(blank, small_hours)                    # its own overnight part, even blank
    no_morning = [{"date": "2026-11-22", "periods": [{"part": 2, "summary": "Chuva"}]}]
    assert wp._summary_now(no_morning, small_hours) is None
