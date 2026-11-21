"""Weather providers for Brazil: CGE for the municipality of São Paulo, INMET (CPTEC as
fallback) for the forecast elsewhere, and the IBGE lookup of the municipality."""

import json
from datetime import datetime
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
    assert [name for name, _ in today["periods"]] == list(wp.PERIODS)
    assert today["summary"] == "Pancadas isoladas" and today["code"] == 80   # the worst period


def test_cge_station_gives_the_current_conditions():
    today = wp.parse_cge_forecast(_read("cge_previsao_estendida.html"))[0]
    now = wp.parse_cge_station(_read("cge_estacao_503.html"), today, datetime(2026, 11, 21, 8, 30))
    assert now["temperature"] == 30.0 and now["windspeed"] == 10.8
    assert now["winddirection"] == 225.0                       # "SW"
    assert now["summary"] == "Nublado" and now["weathercode"] == 3   # the morning period
    assert now["time"] == "2026-11-21T08:30"


def test_cge_pages_without_data_are_errors():
    with pytest.raises(ValueError):
        wp.parse_cge_forecast("<html>manutenção</html>")
    with pytest.raises(ValueError):
        wp.parse_cge_station("<html></html>", None, datetime(2026, 11, 21))


def test_inmet_days_take_the_extremes_and_the_worst_period():
    days = wp.parse_inmet(json.loads(_read("inmet_previsao_3518800.json")), "3518800")
    assert len(days) == 5 and days[0]["date"] == "2026-11-21"
    assert days[0]["code"] == 95 and "trovoadas" in days[0]["summary"]
    assert all(day["tmin"] <= day["tmax"] for day in days)


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
    assert asked[-1].endswith("/previsao/3518800")

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
