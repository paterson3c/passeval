"""Tests del modelo de scoring (Wheeler 2016 adaptado).

Cubre las cinco fases de §4.6.3 del informe v3:

- bruteforce_cost (Fase 3)
- decompose con matches sintéticos (Fase 2, sin IO de diccionarios)
- factor de configuración k! (Fase 4)
- sanity check `min(G_raw, S_charset)` (Fase 4)
- score categórico 0-4 (Fase 5)
- casos del informe + variantes en español
"""
from __future__ import annotations

import math

from passeval.strength.charset import charset_mask, charset_size_from_mask
from passeval.strength.model import (
    SCORE_THRESHOLDS,
    bruteforce_cost,
    decompose,
    estimate_guesses,
    score,
)
from passeval.strength.patterns import Match


# ---------------------------------------------------------------------------
# Fase 3: bruteforce_cost
# ---------------------------------------------------------------------------

def test_bruteforce_cost_vacio():
    assert bruteforce_cost("") == 1


def test_bruteforce_cost_lowercase():
    # 26^L para segmento de minúsculas puro
    assert bruteforce_cost("abc") == 26 ** 3
    assert bruteforce_cost("abcdef") == 26 ** 6


def test_bruteforce_cost_mezclado():
    # "a1" usa lower+digit -> N_seg = 36, L = 2
    assert bruteforce_cost("a1") == 36 ** 2


def test_bruteforce_cost_simbolos_y_unicode():
    # "a!" usa lower+symbol -> 26+33 = 59
    assert bruteforce_cost("a!") == 59 ** 2
    # "ñ" es Unicode -> 100
    assert bruteforce_cost("ñ") == 100


# ---------------------------------------------------------------------------
# Fase 2: decompose con matches sintéticos
# ---------------------------------------------------------------------------

def test_decompose_string_vacio():
    path, cost = decompose("", [])
    assert path == []
    assert cost == 1


def test_decompose_sin_matches_es_bruteforce_puro():
    """Sin matches, la DP devuelve un BF char a char con coste full_n^L."""
    s = "abcdef"  # 6 lowercase, full_n=26
    path, cost = decompose(s, [])
    assert cost == 26 ** 6
    # Path: 6 segmentos bruteforce de longitud 1
    assert len(path) == 6
    assert all(seg.type == "bruteforce" for seg in path)


def test_decompose_match_unico_cubre_toda_la_cadena():
    """Si un match cubre toda la cadena con guesses < bruteforce, gana."""
    s = "abcdef"
    m = Match(type="dictionary", start=0, end=6, token="abcdef", guesses=42)
    path, cost = decompose(s, [m])
    assert cost == 42
    assert len(path) == 1
    assert path[0] is m


def test_decompose_elige_match_si_mas_barato():
    """Match parcial barato + BF residual gana al BF puro."""
    s = "abcde"  # 5 lowercase, BF puro = 26^5 = 11_881_376
    # Match cubre [0:3] con guesses=1, BF residual cubre [3:5] con 26^2 = 676
    # Total: 1 * 676 = 676 << 11_881_376
    m = Match(type="dictionary", start=0, end=3, token="abc", guesses=1)
    path, cost = decompose(s, [m])
    assert cost == 1 * 26 * 26
    # path: [match, bf, bf]
    assert path[0] is m
    assert path[1].type == "bruteforce"
    assert path[2].type == "bruteforce"


def test_decompose_ignora_matches_caros():
    """Un match con guesses > BF se ignora a favor del BF char-a-char."""
    s = "abc"  # BF: 26^3 = 17_576
    m_caro = Match(type="dictionary", start=0, end=3, token="abc", guesses=999_999)
    path, cost = decompose(s, [m_caro])
    assert cost == 26 ** 3
    assert all(seg.type == "bruteforce" for seg in path)


def test_decompose_matches_solapados_elige_descomposicion_optima():
    """Con matches solapados, la DP escoge la combinación más barata."""
    s = "abcdef"
    m1 = Match(type="dictionary", start=0, end=4, token="abcd", guesses=100)
    m2 = Match(type="dictionary", start=2, end=6, token="cdef", guesses=100)
    m3 = Match(type="dictionary", start=0, end=6, token="abcdef", guesses=50)
    # m3 cubre todo por 50, mejor que m1+BF (100*26^2) y m2+BF (26^2*100)
    path, cost = decompose(s, [m1, m2, m3])
    assert cost == 50
    assert len(path) == 1 and path[0] is m3


def test_decompose_match_con_guesses_cero_no_anula_producto():
    """guesses=0 (rank 0) se trata como 1 para no colapsar el producto."""
    s = "abcde"
    m = Match(type="leet", start=0, end=3, token="abc", guesses=0)
    path, cost = decompose(s, [m])
    # cost = max(0,1) * 26^2 = 1 * 676 = 676
    assert cost == 1 * 26 * 26


# ---------------------------------------------------------------------------
# Fase 4: factor de configuración k! y sanity check
# ---------------------------------------------------------------------------

def test_estimate_guesses_aplica_factor_de_configuracion():
    """Para un path con k segmentos, multiplica por k!."""
    s = "abcdef"  # 6 chars
    # 3 matches no-solapados de 2 chars cada uno, guesses=2
    matches = [
        Match(type="x", start=0, end=2, token="ab", guesses=2),
        Match(type="x", start=2, end=4, token="cd", guesses=2),
        Match(type="x", start=4, end=6, token="ef", guesses=2),
    ]
    g = estimate_guesses(s, matches=matches)
    # G_raw = 2*2*2 = 8, k=3, factor=6, total=48
    # S_charset = 26^6 = 308_915_776 — no clampa
    assert g == 8 * math.factorial(3)


def test_estimate_guesses_sanity_check_clampa_si_excede_charset():
    """Construye un caso donde k! infla G_raw por encima de S_charset.

    15 matches sintéticos de 1 char con guesses=5, sobre cadena de 15
    minúsculas. S_charset = 26^15 ≈ 2^70,5. G_raw = 5^15 ≈ 2^34,8;
    factor = 15! ≈ 2^40,2; G_with_config ≈ 2^75 > S_charset.
    Esperado: G_final = S_charset.
    """
    s = "abcdefghijklmno"
    matches = [
        Match(type="x", start=i, end=i + 1, token=s[i], guesses=5)
        for i in range(15)
    ]
    g = estimate_guesses(s, matches=matches)
    s_charset = 26 ** 15
    assert g == s_charset


def test_estimate_guesses_sanity_check_no_clampa_si_no_hace_falta():
    """Para BF puro G_raw = S_charset, el min devuelve S_charset (idempotente)."""
    s = "abc"
    g = estimate_guesses(s, matches=[])
    assert g == 26 ** 3


def test_estimate_guesses_string_vacio():
    assert estimate_guesses("") == 1


# ---------------------------------------------------------------------------
# Fase 5: score categórico
# ---------------------------------------------------------------------------

def test_score_string_vacio():
    assert score("") == 0


def test_score_umbrales_son_los_documentados():
    """SCORE_THRESHOLDS debe coincidir exactamente con los del informe."""
    assert SCORE_THRESHOLDS == (10, 20, 35, 60)


def test_score_trivial_pocos_bits():
    # 123456 -> secuencia numerica obvia, score 0
    assert score("123456") == 0
    # contraseña corta repetida
    assert score("aaaa") == 0


def test_score_muy_fuerte_random_largo():
    # 60 chars random alta entropía -> score 4
    s = "Q9!xZ@7vN&1pL#3hR$8mY%2kT^6jW*4dB(0fG)5cV-aE_qX+wO=zU/yI?sP{rH}"
    assert score(s) == 4


# ---------------------------------------------------------------------------
# Casos obligatorios del informe (§4.6.3 Fase 5, ajustados al alcance)
# ---------------------------------------------------------------------------
#
# El informe lista 5 casos (`password`, `P@ssw0rd`, `Tr0ub4dor&3`,
# `correct horse battery staple`, `Xk3#mQ!w2`). Tres de ellos asumen un
# diccionario inglés que este TFG no carga (los diccionarios son ES). Se
# testan con bandas relajadas o se sustituyen por equivalentes en español
# que ejercitan el mismo comportamiento del modelo.

def test_caso_informe_xk3_sanity_check_aproximadamente_charset():
    """`Xk3#mQ!w2` (random corta) -> log2(G) ≈ log2(S_charset)."""
    s = "Xk3#mQ!w2"
    g = estimate_guesses(s)
    n = charset_size_from_mask(charset_mask(s))
    expected = n ** len(s)
    # Igualdad exacta: sin matches, BF puro produce N^L que coincide con S_charset.
    assert g == expected


def test_caso_informe_correct_horse_battery_staple_score_alto():
    """`correct horse battery staple` -> score 3-4 (largo, sin patrones ES)."""
    sc = score("correct horse battery staple")
    assert sc >= 3


def test_caso_es_contrasena_es_debil():
    """Equivalente español de "password": "contraseña" debería caer baja."""
    sc = score("contraseña")
    assert sc <= 2  # palabra de diccionario, no más de "débil"


def test_caso_es_leet_apenas_sube_score():
    """Mensaje didáctico §4.6.3 subfase 1.a: leet apenas añade resistencia."""
    base = score("electricidad")
    leet_v = score("3l3ctric1d4d")
    # La leet puede subir el score como mucho 1 (factor 2^subs es modesto)
    assert leet_v - base <= 1


def test_caso_es_juan1234_es_trivial():
    """Nombre común + secuencia numérica -> score 0."""
    assert score("juan1234") == 0


# ---------------------------------------------------------------------------
# Calibración con token_freq (§4.6.5)
# ---------------------------------------------------------------------------

def test_token_freq_recalibra_matches_dict():
    """token_freq no crea matches nuevos: si el token no está en ningún dict, no afecta."""
    s = "xqzpwvkjmm"  # cadena sin match en ningún diccionario → bruteforce puro
    g_sin = estimate_guesses(s)
    g_con = estimate_guesses(s, token_freq={"xqzpwvkjmm": 999_999_999})
    assert g_con == g_sin


def test_token_freq_recalibra_matches_que_existen():
    """Si un token YA tiene match (de los dicts ES), su frecuencia ajusta el coste."""
    s = "casa"  # "casa" está en palabras_es
    g_normal = estimate_guesses(s)
    # Inyectamos frecuencia muy alta -> guesses_ajustado = total/freq muy bajo
    g_calib = estimate_guesses(s, token_freq={"casa": 1_000_000})
    # Con calibración debería ser menor o igual (frecuencia altísima -> trivial)
    assert g_calib <= g_normal
