"""Tests del detector de repeticiones."""
from __future__ import annotations

from passeval.strength.patterns.repetition import detect


def _types_tokens(matches):
    return [(m.metadata.get("kind"), m.token, m.start, m.end) for m in matches]


def test_aaaa_detectado_como_single():
    matches = detect("aaaa")
    singles = [m for m in matches if m.metadata["kind"] == "single"]
    assert len(singles) == 1
    m = singles[0]
    assert m.token == "aaaa"
    assert m.start == 0 and m.end == 4
    assert m.metadata["count"] == 4
    assert m.metadata["base"] == "a"


def test_repeticion_minima_es_3():
    # "aa" no debe ser repetición single (longitud 2).
    matches = [m for m in detect("aab") if m.metadata["kind"] == "single"]
    assert matches == []


def test_dos_grupos_de_repeticion_no_se_fusionan():
    matches = [m for m in detect("aaabbb") if m.metadata["kind"] == "single"]
    bases = sorted(m.metadata["base"] for m in matches)
    assert bases == ["a", "b"]
    assert all(m.metadata["count"] == 3 for m in matches)


def test_periodo_2_abab():
    matches = [m for m in detect("abab") if m.metadata["kind"] == "period"]
    assert any(
        m.metadata["base"].lower() == "ab" and m.metadata["count"] == 2 for m in matches
    )


def test_periodo_3_xyzxyz():
    matches = [m for m in detect("xyzxyz") if m.metadata["kind"] == "period"]
    assert any(
        m.metadata["base"].lower() == "xyz" and m.metadata["count"] == 2 for m in matches
    )


def test_falso_positivo_controlado_secuencia_no_es_repeticion():
    # "abcd" es secuencia, no repetición — no debe emitir kind=single ni period.
    matches = detect("abcd")
    assert matches == []


def test_case_insensitive_pero_token_preserva_caso():
    matches = [m for m in detect("AaAa") if m.metadata["kind"] == "period"]
    assert matches
    # token preserva el casing original del input
    assert matches[0].token == "AaAa"


def test_cadena_vacia_devuelve_lista_vacia():
    assert detect("") == []
