"""Tests de la entropía de Shannon."""
from __future__ import annotations

import math

import pytest

from passeval.strength.shannon import shannon_entropy


def test_cadena_vacia_es_cero():
    assert shannon_entropy("") == 0.0


def test_un_solo_caracter_es_cero():
    assert shannon_entropy("a") == 0.0


def test_uniforme_dos_simbolos():
    # "ab" tiene 2 símbolos uniformemente distribuidos -> log2(2) = 1
    assert shannon_entropy("ab") == pytest.approx(1.0)


def test_uniforme_n_simbolos():
    # "abcd" -> log2(4) = 2
    assert shannon_entropy("abcd") == pytest.approx(2.0)
    # "12345678" -> log2(8) = 3
    assert shannon_entropy("12345678") == pytest.approx(3.0)


def test_aaaabbbb_es_un_bit():
    # 4 'a' y 4 'b' -> H = 1 bit/char
    assert shannon_entropy("aaaabbbb") == pytest.approx(1.0)


def test_password_valor_documentado():
    # 'p'=2, 'a'=1, 's'=2, 'w'=1, 'o'=1, 'r'=1, 'd'=1 sobre 8 chars
    # H = -(2 * (2/8)*log2(2/8) + 5 * (1/8)*log2(1/8))
    #   = -(2 * 0.25*-2 + 5 * 0.125*-3)
    #   = -(-1 + -1.875) = 2.875
    assert shannon_entropy("password") == pytest.approx(2.75, abs=0.01)


def test_distribucion_sesgada_menor_entropia_que_uniforme():
    # 'aaaab' tiene 4 a's y 1 b, debe dar menos que log2(2) = 1
    assert shannon_entropy("aaaab") < 1.0
    assert shannon_entropy("aaaab") > 0.0


def test_repeticion_total_es_cero_si_unico_simbolo():
    assert shannon_entropy("aaaaaa") == 0.0


def test_no_negativa_nunca():
    for s in ["abc", "P@ssw0rd", "café", "🔥🔥🔥", "Verano2024"]:
        assert shannon_entropy(s) >= 0.0


def test_acotada_por_log2_n():
    # Cota teórica: H <= log2(número de símbolos distintos)
    s = "abcdefgh"  # 8 distintos
    assert shannon_entropy(s) <= math.log2(8) + 1e-9
