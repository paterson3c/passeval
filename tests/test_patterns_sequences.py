"""Tests del detector de secuencias."""
from __future__ import annotations

from passeval.strength.patterns.sequences import detect


def test_abc_creciente_alfabetica():
    matches = detect("abc")
    assert len(matches) == 1
    m = matches[0]
    assert m.token == "abc"
    assert m.metadata == {"length": 3, "direction": "asc", "kind": "alphabetic"}


def test_cba_decreciente_alfabetica():
    matches = detect("cba")
    assert len(matches) == 1
    assert matches[0].metadata["direction"] == "desc"
    assert matches[0].metadata["kind"] == "alphabetic"


def test_123_creciente_numerica():
    matches = detect("123")
    assert len(matches) == 1
    assert matches[0].metadata == {"length": 3, "direction": "asc", "kind": "numeric"}


def test_9876_decreciente_numerica():
    matches = detect("9876")
    assert len(matches) == 1
    assert matches[0].token == "9876"
    assert matches[0].metadata["direction"] == "desc"


def test_dos_secuencias_separadas():
    matches = detect("abc...123")
    assert len(matches) == 2
    tokens = sorted(m.token for m in matches)
    assert tokens == ["123", "abc"]


def test_falso_positivo_controlado_clase_mixta():
    # "a1b" no es secuencia (cambia de clase entre letra y dígito).
    assert detect("a1b") == []


def test_falso_positivo_controlado_salto_dos():
    # "ace" salta de 1, no debe detectarse.
    assert detect("ace") == []


def test_longitud_minima_2_no_se_emite():
    # "ab" tiene length=2, por debajo del umbral 3.
    assert detect("ab") == []


def test_case_insensitive():
    matches = detect("ABC")
    assert len(matches) == 1
    assert matches[0].token == "ABC"
    assert matches[0].metadata["kind"] == "alphabetic"
