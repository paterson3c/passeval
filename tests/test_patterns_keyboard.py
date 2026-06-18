"""Tests del detector de runs de teclado QWERTY-ES."""
from __future__ import annotations

from passeval.strength.patterns.keyboard import detect


def test_qwerty_run_horizontal_derecha():
    matches = detect("qwerty")
    assert len(matches) == 1
    m = matches[0]
    assert m.token == "qwerty"
    assert m.metadata["direction"] == "right"
    assert m.metadata["length"] == 6


def test_asdfg_fila_central():
    matches = detect("asdfg")
    assert len(matches) == 1
    assert matches[0].metadata["direction"] == "right"


def test_run_a_la_izquierda():
    matches = detect("0987")
    assert len(matches) == 1
    assert matches[0].metadata["direction"] == "left"
    assert matches[0].token == "0987"


def test_incluye_enie_en_fila_central():
    # k l ñ son adyacentes en QWERTY-ES.
    matches = detect("klñ")
    assert len(matches) == 1
    assert matches[0].token == "klñ"


def test_falso_positivo_controlado_diagonal_no_se_detecta():
    # "qaz" es diagonal, no horizontal -> no se detecta en este modelo simple.
    assert detect("qaz") == []


def test_falso_positivo_controlado_cambio_de_fila():
    # "qa" cambia de fila, "qaw" no debe ser run de longitud 3.
    assert detect("qaw") == []


def test_minimo_3_caracteres():
    assert detect("qw") == []


def test_case_insensitive():
    matches = detect("QWERTY")
    assert len(matches) == 1
    assert matches[0].token == "QWERTY"
