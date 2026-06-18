"""Tests del detector de términos españoles."""
from __future__ import annotations

from passeval.strength.patterns.spanish import CULTURAL_TERMS, detect


def test_termino_cultural_madrid_se_detecta():
    matches = detect("madrid2024", dictionaries={})
    cultural = [m for m in matches if m.metadata["source"] == "cultural"]
    assert any(m.token.lower() == "madrid" for m in cultural)


def test_termino_cultural_realmadrid_se_detecta():
    matches = detect("RealMadrid", dictionaries={})
    cultural = [m for m in matches if m.metadata["source"] == "cultural"]
    assert any(m.token.lower() == "realmadrid" for m in cultural)


def test_termino_cultural_te_quiero_concatenado():
    matches = detect("tequiero!", dictionaries={})
    cultural = [m for m in matches if m.metadata["source"] == "cultural"]
    assert any(m.token.lower() == "tequiero" for m in cultural)


def test_diccionario_es_inyectado():
    dicts = {"nombres_es": {"juan": 0, "maria": 1}}
    matches = detect("juan1990", dictionaries=dicts)
    nombres = [m for m in matches if m.metadata["source"] == "nombres_es"]
    assert any(m.token.lower() == "juan" and m.metadata["rank"] == 0 for m in nombres)


def test_diccionario_no_es_se_ignora_cuando_se_pasa_filtrado():
    # Cuando dictionaries se pasa explícitamente, se usa tal cual.
    # Si solo le damos uno con sufijo no _es, igualmente lo busca.
    # Lo importante: el filtrado a "_es" solo aplica al cargar por defecto.
    dicts = {"otra": {"hola": 0}}
    matches = detect("holamundo", dictionaries=dicts)
    assert any(m.metadata["source"] == "otra" for m in matches)


def test_falso_positivo_controlado_palabra_random():
    # "xyzwabc" no es término cultural ni está en el dict inyectado.
    matches = detect("xyzwabc", dictionaries={})
    assert matches == []


def test_minimo_3_caracteres():
    # "ho" no debe coincidir aunque sea prefijo de "hola" cultural.
    matches = detect("ho", dictionaries={})
    assert matches == []


def test_cultural_terms_no_vacio():
    # Sanity: la lista cultural debe tener referencias.
    assert len(CULTURAL_TERMS) >= 20
