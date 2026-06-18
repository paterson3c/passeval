"""Tests del módulo de normalización."""
from __future__ import annotations

import unicodedata

from passeval.normalize import REPLACEMENT_CHAR, decode_line, normalize


def test_nfc_idempotente_sobre_ascii():
    assert normalize("password") == "password"


def test_preserva_espacios_internos_y_extremos():
    s = "  hola  mundo  "
    assert normalize(s) == s
    assert len(normalize(s)) == len(s)


def test_preserva_mayusculas():
    s = "AbCdEf123"
    assert normalize(s) == s


def test_compone_acentos_descompuestos():
    descompuesto = "café"  # 'café' como NFD: 'cafe' + combining acute
    compuesto = "café"          # 'café' como NFC (1 codepoint para é)
    assert normalize(descompuesto) == compuesto
    assert len(normalize(descompuesto)) < len(descompuesto)


def test_acento_compuesto_se_mantiene():
    s = "café"
    assert normalize(s) == s
    assert unicodedata.is_normalized("NFC", normalize(s))


def test_emoji_se_mantiene():
    s = "fire🔥pwd"
    assert normalize(s) == s


def test_normalize_idempotente():
    s = "Verano2024ñ"
    assert normalize(normalize(s)) == normalize(s)


def test_decode_utf8_valido_sin_separador():
    raw, had_invalid = decode_line(b"hola")
    assert raw == "hola"
    assert had_invalid is False


def test_decode_quita_lf():
    raw, had_invalid = decode_line(b"hola\n")
    assert raw == "hola"
    assert had_invalid is False


def test_decode_quita_crlf():
    raw, had_invalid = decode_line(b"hola\r\n")
    assert raw == "hola"
    assert had_invalid is False


def test_decode_preserva_espacios_internos_finales_no_es_trim():
    # un espacio antes del \n debe conservarse: el separador es \n, no el espacio
    raw, _ = decode_line(b"hola \n")
    assert raw == "hola "


def test_decode_bytes_invalidos_marcan_flag():
    # 0xff no es inicio válido de secuencia UTF-8
    raw, had_invalid = decode_line(b"\xffabc")
    assert had_invalid is True
    assert REPLACEMENT_CHAR in raw
    # 'abc' debe estar presente al final
    assert raw.endswith("abc")


def test_decode_acentos_nfd_se_componen_a_nfc():
    # 'café' NFD: 'cafe' + U+0301 (combining acute) = b'cafe\xcc\x81'
    raw, _ = decode_line(b"cafe\xcc\x81\n")
    assert raw == "café"
    assert unicodedata.is_normalized("NFC", raw)


def test_decode_linea_vacia():
    raw, had_invalid = decode_line(b"")
    assert raw == ""
    assert had_invalid is False


def test_decode_solo_separador():
    raw, had_invalid = decode_line(b"\n")
    assert raw == ""
    assert had_invalid is False
