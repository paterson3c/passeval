"""Tests del módulo charset."""
from __future__ import annotations

import math

import pytest

from passeval.strength.charset import (
    DIGIT_MASK,
    LOWER_MASK,
    SYMBOL_MASK,
    UNICODE_MASK,
    UPPER_MASK,
    charset_entropy,
    charset_mask,
    charset_size_from_mask,
)


def test_lower_only_mask_y_size():
    assert charset_mask("abcxyz") == LOWER_MASK
    assert charset_size_from_mask(LOWER_MASK) == 26


def test_upper_only_mask_y_size():
    assert charset_mask("ABCXYZ") == UPPER_MASK
    assert charset_size_from_mask(UPPER_MASK) == 26


def test_digit_only_mask_y_size():
    assert charset_mask("0123456789") == DIGIT_MASK
    assert charset_size_from_mask(DIGIT_MASK) == 10


def test_symbol_only_mask_y_size():
    # ASCII printable no alfanumérico: 33 caracteres incluyendo espacio
    assert charset_mask("!@#$%^&*()") == SYMBOL_MASK
    assert charset_size_from_mask(SYMBOL_MASK) == 33


def test_unicode_no_ascii_mask_y_size():
    assert charset_mask("ñáéíóú") == UNICODE_MASK
    assert charset_size_from_mask(UNICODE_MASK) == 100


def test_emoji_es_unicode():
    assert charset_mask("🔥") == UNICODE_MASK


def test_combinaciones():
    assert charset_mask("aA1") == LOWER_MASK | UPPER_MASK | DIGIT_MASK
    assert charset_mask("aA1!") == LOWER_MASK | UPPER_MASK | DIGIT_MASK | SYMBOL_MASK
    assert (
        charset_mask("aA1!ñ")
        == LOWER_MASK | UPPER_MASK | DIGIT_MASK | SYMBOL_MASK | UNICODE_MASK
    )


def test_size_combinado():
    # lower + digit = 26 + 10 = 36
    assert charset_size_from_mask(LOWER_MASK | DIGIT_MASK) == 36
    # lower + upper + digit = 62
    assert charset_size_from_mask(LOWER_MASK | UPPER_MASK | DIGIT_MASK) == 62
    # full ASCII printable + unicode = 26+26+10+33+100 = 195
    full = LOWER_MASK | UPPER_MASK | DIGIT_MASK | SYMBOL_MASK | UNICODE_MASK
    assert charset_size_from_mask(full) == 195


def test_charset_entropy_cadena_vacia():
    assert charset_entropy("") == (0, 0.0)


def test_charset_entropy_lower_simple():
    # "abc" -> N=26, L=3, bits = 3*log2(26)
    n, bits = charset_entropy("abc")
    assert n == 26
    assert bits == pytest.approx(3 * math.log2(26))


def test_charset_entropy_password_clasico():
    # 'P@ssw0rd' tiene lower+upper+digit+symbol = 95
    # L=8, bits = 8*log2(95) ≈ 52.55
    n, bits = charset_entropy("P@ssw0rd")
    assert n == 26 + 26 + 10 + 33
    assert bits == pytest.approx(8 * math.log2(95), abs=1e-6)


def test_caracter_de_control_no_contribuye():
    # \t (0x09) es control, no debería marcar nada por sí solo
    assert charset_mask("\t") == 0
    # mezclado con lower, solo debe marcar lower
    assert charset_mask("a\t") == LOWER_MASK


def test_espacio_es_symbol():
    assert charset_mask(" ") == SYMBOL_MASK
