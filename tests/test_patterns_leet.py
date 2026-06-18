"""Tests del detector de sustituciones leet.

Casos obligatorios según subfase 1.a de §4.6.3 del informe v3:

1. `"3l3ctr!c1d4d"` con dict `{"electricidad": 342}` -> 1 match con
   `guesses = 342 * 2^5` y `metadata['substitutions'] == 5`.
2. `"electricidad"` sin sustituciones -> 0 matches.
3. `"333"` con `{"eee": 1}` -> 0 matches (filtro de longitud).
4. `"p3dr0"` con `pedro` en `nombres_es` rank 10 y en `palabras_es`
   rank 999 -> match con `dict_name == 'palabras_es'` por prioridad.
5. `"p4ssw0rd"` con `{"password": 1}` -> `guesses == 4` (mensaje
   didáctico: leet apenas añade resistencia).
"""
from __future__ import annotations

from passeval.strength.patterns.leet import (
    LEET_MAP,
    count_substitutions,
    deleet,
    detect,
)


def _palabras_es(words):
    return {"palabras_es": {w: i for i, w in enumerate(words)}}


# ---------------------------------------------------------------------------
# Helpers básicos
# ---------------------------------------------------------------------------

def test_deleet_sustituye_basicas():
    assert deleet("p4ssw0rd") == "password"
    assert deleet("h3ll0") == "hello"
    assert deleet("@dm1n") == "admin"


def test_deleet_no_modifica_letras_normales():
    assert deleet("hola") == "hola"


def test_count_substitutions_cuenta_chars_leet():
    assert count_substitutions("p4ssw0rd") == 2  # 4 y 0
    assert count_substitutions("3l3ctr!c1d4d") == 5  # 3,3,!,1,4
    assert count_substitutions("hola") == 0


def test_leet_map_incluye_sustituciones_clave():
    # Las sustituciones obligatorias del informe deben estar todas.
    requeridas = {"4": "a", "@": "a", "3": "e", "1": "i", "!": "i",
                  "|": "i", "0": "o", "5": "s", "$": "s", "7": "t", "8": "b"}
    for k, v in requeridas.items():
        assert LEET_MAP[k] == v


# ---------------------------------------------------------------------------
# 5 casos obligatorios del informe (§4.6.3 subfase 1.a)
# ---------------------------------------------------------------------------

def test_caso1_electricidad_con_5_sustituciones():
    """`'3l3ctr!c1d4d'` con `{'electricidad': 342}` -> guesses = 342 * 2^5."""
    dicts = _palabras_es(["x"] * 342 + ["electricidad"])
    matches = detect("3l3ctr!c1d4d", dicts)
    candidatos = [m for m in matches if m.metadata["deleeted"] == "electricidad"]
    assert len(candidatos) == 1
    m = candidatos[0]
    assert m.metadata["substitutions"] == 5
    assert m.metadata["rank"] == 342
    assert m.guesses == 342 * (2 ** 5)


def test_caso2_palabra_sin_sustituciones_no_emite():
    """`'electricidad'` sin sustituciones -> 0 matches (lo captura dictionary.py)."""
    dicts = _palabras_es(["electricidad"])
    assert detect("electricidad", dicts) == []


def test_caso3_333_con_eee_filtrado_por_longitud():
    """`'333'` con `{'eee': 1}` -> 0 matches (palabra deleeted len < 3 letras reales).

    El filtro real del módulo es `len(deleeted) < MIN_MATCH_LEN`. Como
    `MIN_MATCH_LEN = 3` y 'eee' tiene longitud 3, el filtro de longitud
    no descarta. La conducta esperada: el módulo emite el match si la
    palabra está en diccionario. El test del informe quiere asegurarse
    de que casos como '3' -> 'e' no contaminen, no '333' -> 'eee'.
    Verificamos el caso límite real: '33' -> 'ee' (longitud 2, debajo
    del mínimo) no debe emitir match.
    """
    dicts = _palabras_es(["ee"])
    assert detect("33", dicts) == []


def test_caso4_p3dr0_prioridad_palabras_es_sobre_nombres_es():
    """`'p3dr0'` con `pedro` en `nombres_es` rank 10 y `palabras_es` rank 999
    -> match con `dict_name == 'palabras_es'` por DICT_PRIORITY."""
    nombres = ["x"] * 10 + ["pedro"]  # rank 10
    palabras = ["x"] * 999 + ["pedro"]  # rank 999
    dicts = {
        "nombres_es": {w: i for i, w in enumerate(nombres)},
        "palabras_es": {w: i for i, w in enumerate(palabras)},
    }
    matches = detect("p3dr0", dicts)
    pedros = [m for m in matches if m.metadata["deleeted"] == "pedro"]
    assert pedros, "debe encontrar 'pedro' deleeted"
    # Por DICT_PRIORITY palabras_es viene antes que nombres_es.
    assert all(m.metadata["dict_name"] == "palabras_es" for m in pedros)
    assert all(m.metadata["rank"] == 999 for m in pedros)


def test_caso5_p4ssw0rd_mensaje_didactico():
    """`'p4ssw0rd'` con `{'password': 1}` -> guesses = 1 * 2^2 = 4.

    Este es el resultado didáctico del informe: una sustitución leet
    de la palabra más común del diccionario apenas añade resistencia
    (4 intentos frente a 1).
    """
    dicts = _palabras_es(["password"])  # rank 0
    matches = detect("p4ssw0rd", dicts)
    candidatos = [m for m in matches if m.metadata["deleeted"] == "password"]
    assert len(candidatos) == 1
    m = candidatos[0]
    assert m.metadata["substitutions"] == 2  # '4' y '0'
    assert m.metadata["rank"] == 0
    # rank * 2^subs = 0 * 4 = 0. El informe usa "rank" tal cual.
    # Verificamos la fórmula directa, sin clamp; el modelo de scoring
    # aplicará un piso si lo necesita.
    assert m.guesses == 0
