"""Tests del detector de fechas."""
from __future__ import annotations

from passeval.strength.patterns.dates import detect


def _formats(matches):
    return sorted(m.metadata["format"] for m in matches)


def test_anio_suelto_1985():
    matches = detect("hola1985")
    yyyy = [m for m in matches if m.metadata["format"] == "YYYY"]
    assert len(yyyy) == 1
    assert yyyy[0].token == "1985"
    assert yyyy[0].metadata["year"] == 1985


def test_iso_yyyy_mm_dd_valida():
    matches = detect("2024-03-15")
    iso = [m for m in matches if m.metadata["format"] == "YYYY-MM-DD"]
    assert len(iso) == 1
    m = iso[0]
    assert m.metadata["year"] == 2024
    assert m.metadata["month"] == 3
    assert m.metadata["day"] == 15


def test_iso_invalida_no_se_emite():
    # 2024-13-40: mes 13 inválido.
    matches = [m for m in detect("2024-13-40") if m.metadata["format"] == "YYYY-MM-DD"]
    assert matches == []


def test_ddmmyyyy_valida():
    matches = [m for m in detect("31121999") if m.metadata["format"] == "DDMMYYYY"]
    assert len(matches) == 1
    assert matches[0].metadata == {
        "year": 1999, "month": 12, "day": 31, "format": "DDMMYYYY"
    }


def test_mmddyyyy_valida_distinta_de_ddmmyyyy():
    # 12311999 -> MMDDYYYY: mes=12, día=31 ✓; DDMMYYYY: día=12, mes=31 ✗
    matches = detect("12311999")
    formats = [m.metadata["format"] for m in matches if m.metadata["format"] in ("DDMMYYYY", "MMDDYYYY")]
    assert "MMDDYYYY" in formats
    assert "DDMMYYYY" not in formats


def test_ambiguedad_dd_mm_intercambiables():
    # 12031985 valida DDMMYYYY (12 marzo) y MMDDYYYY (mar 12) -> ambos.
    matches = detect("12031985")
    formats = sorted({m.metadata["format"] for m in matches if m.metadata["format"] in ("DDMMYYYY", "MMDDYYYY")})
    assert formats == ["DDMMYYYY", "MMDDYYYY"]


def test_ddmmyy_expansion_de_anio():
    # 311299 -> 31/12/1999 (yy=99 >= 50 -> 1999).
    matches = [m for m in detect("311299") if m.metadata["format"] == "DDMMYY"]
    assert len(matches) == 1
    assert matches[0].metadata["year"] == 1999

    # 010125 -> 01/01/2025 (yy=25 < 50 -> 2025).
    matches2 = [m for m in detect("010125") if m.metadata["format"] == "DDMMYY"]
    assert len(matches2) == 1
    assert matches2[0].metadata["year"] == 2025


def test_falso_positivo_controlado_no_es_fecha():
    # "999999" no es DDMMYY válida (mes=99).
    matches = [m for m in detect("999999") if m.metadata["format"] == "DDMMYY"]
    assert matches == []


def test_anio_fuera_de_rango_no_se_emite():
    # 1899 está fuera de [1900, 2099].
    matches = [m for m in detect("hola1899") if m.metadata["format"] == "YYYY"]
    assert matches == []


def test_no_revienta_con_digitos_unicode_no_ascii():
    """Regresión: '²²' satisface str.isdigit() pero int() lo rechaza.

    Antes del fix, una contraseña con superíndices o devanagari abortaba
    el ETL completo (`ValueError: invalid literal for int() with base 10`).
    El detector ahora filtra a dígitos ASCII estrictos.
    """
    # No debe lanzar excepción; solo no detectar fechas en esa ventana.
    out = detect("aaa²²bb1989cc")
    formatos = sorted(m.metadata["format"] for m in out)
    assert formatos == ["YYYY"]  # detecta el año pero ignora '²²' como dígitos
    # Devanagari (digit Unicode `१२३४५६` = 123456): no debe parsear como fecha.
    assert detect("१२३४५६") == []
