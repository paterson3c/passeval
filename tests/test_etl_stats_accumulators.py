"""Tests de los acumuladores de stats del ETL (sin BBDD)."""
from __future__ import annotations

from etl._stats_accumulators import (
    BUCKET_STEP,
    LightStatsAccumulator,
    StatsAccumulator,
    entropy_bucket,
    update_light_stats,
    update_stats,
)


# ---------------------------------------------------------------------------
# entropy_bucket
# ---------------------------------------------------------------------------

def test_entropy_bucket_paso_05():
    assert entropy_bucket(0.0) == 0.0
    assert entropy_bucket(0.4) == 0.0
    assert entropy_bucket(0.5) == 0.5
    assert entropy_bucket(0.99) == 0.5
    assert entropy_bucket(1.0) == 1.0
    assert entropy_bucket(13.249) == 13.0
    assert entropy_bucket(13.5) == 13.5


def test_entropy_bucket_negativo_se_normaliza():
    assert entropy_bucket(-1.0) == 0.0


def test_bucket_step_constante_es_05():
    assert BUCKET_STEP == 0.5


# ---------------------------------------------------------------------------
# update_stats: longitud y entropía
# ---------------------------------------------------------------------------

def test_update_stats_acumula_longitud():
    acc = StatsAccumulator()
    update_stats(acc, "abc", dictionaries={})
    update_stats(acc, "abcdef", dictionaries={})
    update_stats(acc, "abc", dictionaries={})
    assert acc.length_hist[3] == 2
    assert acc.length_hist[6] == 1


def test_update_stats_acumula_entropia_shannon_y_charset():
    acc = StatsAccumulator()
    update_stats(acc, "abcdef", dictionaries={})
    # Ambos tipos deben aparecer
    types = {t for (t, _) in acc.entropy_hist.keys()}
    assert types == {"shannon", "charset"}


def test_update_stats_password_vacio_no_modifica():
    acc = StatsAccumulator()
    update_stats(acc, "", dictionaries={})
    assert not acc.length_hist
    assert not acc.entropy_hist
    assert not acc.pattern_stats
    assert not acc.token_freqs


# ---------------------------------------------------------------------------
# update_stats: tokens y patrones (con dict inyectado)
# ---------------------------------------------------------------------------

def _dicts(words):
    return {"palabras_es": {w: i for i, w in enumerate(words)}}


def test_update_stats_token_substring_para_match_de_diccionario():
    acc = StatsAccumulator()
    dicts = _dicts(["hola"])
    update_stats(acc, "hola1234", dicts)
    # Debe haber al menos una entrada de tipo 'substring' para 'hola'
    assert ("substring", "hola") in acc.token_freqs
    assert acc.token_freqs[("substring", "hola")] >= 1


def test_update_stats_token_deleetified_si_hay_caracteres_leet():
    """`'h0la'` con leet -> deleetified='hola' (longitud 4 >= 3)."""
    acc = StatsAccumulator()
    update_stats(acc, "h0la", dictionaries={})
    assert ("deleetified", "hola") in acc.token_freqs


def test_update_stats_no_emite_deleetified_si_pwd_no_tiene_leet():
    acc = StatsAccumulator()
    update_stats(acc, "hola", dictionaries={})
    assert not any(t == "deleetified" for (t, _) in acc.token_freqs.keys())


def test_update_stats_no_emite_deleetified_si_resultado_es_corto():
    """`'h0'` -> deleet='ho' (len 2 < 3) -> NO se emite."""
    acc = StatsAccumulator()
    update_stats(acc, "h0", dictionaries={})
    assert not any(t == "deleetified" for (t, _) in acc.token_freqs.keys())


def test_update_stats_pattern_stats_recibe_detecciones():
    """Una secuencia trivial debe registrar al menos un pattern_stats."""
    acc = StatsAccumulator()
    update_stats(acc, "abcdef", dictionaries={})
    # 'abcdef' es secuencia ascendente -> debe haber un pattern de tipo sequence
    types_detectados = {ptype for (ptype, _) in acc.pattern_stats.keys()}
    assert "sequence" in types_detectados


# ---------------------------------------------------------------------------
# absorb (composición)
# ---------------------------------------------------------------------------

def test_accumulator_absorb_suma_los_contadores():
    a = StatsAccumulator()
    b = StatsAccumulator()
    update_stats(a, "abc", dictionaries={})
    update_stats(b, "abc", dictionaries={})
    update_stats(b, "xyz", dictionaries={})
    a.absorb(b)
    assert a.length_hist[3] == 3  # 1 + (1+1)


# ---------------------------------------------------------------------------
# Paridad: el camino rápido (merged dict+spanish) produce los mismos
# token_freqs que la composición original dictionary.detect+spanish.detect
# para un conjunto de passwords representativo.
# ---------------------------------------------------------------------------

def _legacy_update(acc, password, dictionaries):
    """Replica del camino original de update_stats (pre-optim).

    Útil sólo en tests: ejecuta dictionary.detect y spanish.detect por
    separado como hacía el código legacy. Nos sirve para verificar
    paridad con el camino rápido fusionado.
    """
    from etl import _stats_accumulators as M
    from passeval.strength.patterns import dictionary as pat_dict
    from passeval.strength.patterns import spanish as pat_es

    for m in pat_dict.detect(password, dictionaries):
        token = m.metadata.get("matched_word", m.token).lower()
        if 3 <= len(token) <= M.TOKEN_MAX_LEN:
            acc.token_freqs[("substring", token)] += 1
    for m in pat_es.detect(password, dictionaries):
        token = m.token.lower()
        if 3 <= len(token) <= M.TOKEN_MAX_LEN:
            acc.token_freqs[("substring", token)] += 1


def test_paridad_token_substring_camino_rapido_vs_legacy():
    """Para un conjunto de passwords variadas, los token_freqs ['substring']
    del camino rápido deben coincidir con los del camino legacy."""
    dicts = {
        "palabras_es": {"casa": 0, "amor": 1, "perro": 2, "verano": 3},
        "nombres_es": {"juan": 0, "maria": 1, "pedro": 2},
    }
    passwords = [
        "casa1234", "amormio", "verano2024", "juan", "perropedro",
        "MariaCarmen", "noexiste", "aaaa", "casa", "casacasa",
    ]
    for pwd in passwords:
        fast = StatsAccumulator()
        legacy = StatsAccumulator()
        update_stats(fast, pwd, dictionaries=dicts)
        _legacy_update(legacy, pwd, dictionaries=dicts)
        # token_freqs.substring debe coincidir exactamente
        fast_subs = {(t, c) for (kind, t), c in fast.token_freqs.items() if kind == "substring"}
        legacy_subs = {(t, c) for (kind, t), c in legacy.token_freqs.items() if kind == "substring"}
        assert fast_subs == legacy_subs, f"paridad rota para {pwd!r}: {fast_subs} != {legacy_subs}"


def test_camino_rapido_emite_pattern_dictionary_y_spanish_para_dicts_es():
    """Un match en palabras_es debe contar tanto en pattern_type='dictionary'
    como en pattern_type='spanish' (compat con esquema legacy)."""
    dicts = {"palabras_es": {"hola": 0}}
    acc = StatsAccumulator()
    update_stats(acc, "holaaa", dictionaries=dicts)
    types = {ptype for (ptype, _) in acc.pattern_stats.keys()}
    assert "dictionary" in types
    assert "spanish" in types


def test_charset_histogram_acumula_combinaciones_exactas():
    """Cada contraseña aporta +1 a la entrada con su charset_mask exacto."""
    acc = StatsAccumulator()
    update_stats(acc, "abc", dictionaries={})       # mask=1 (lower)
    update_stats(acc, "ABC", dictionaries={})       # mask=2 (upper)
    update_stats(acc, "123", dictionaries={})       # mask=4 (digit)
    update_stats(acc, "abc123", dictionaries={})    # mask=5 (lower+digit)
    update_stats(acc, "abc", dictionaries={})       # mask=1 otra vez
    assert acc.charset_hist[1] == 2
    assert acc.charset_hist[2] == 1
    assert acc.charset_hist[4] == 1
    assert acc.charset_hist[5] == 1


def test_charset_histogram_distingue_mask_con_simbolo_y_sin():
    """'abc!' tiene lower+symbol (1|8=9); 'abc' tiene solo lower (1)."""
    acc = StatsAccumulator()
    update_stats(acc, "abc!", dictionaries={})
    update_stats(acc, "abc", dictionaries={})
    assert acc.charset_hist[1 | 8] == 1
    assert acc.charset_hist[1] == 1


def test_absorb_suma_charset_histograms():
    """absorb() debe combinar también el charset_hist (no solo length/entropy/etc)."""
    a = StatsAccumulator()
    b = StatsAccumulator()
    update_stats(a, "abc", dictionaries={})
    update_stats(b, "abc", dictionaries={})
    update_stats(b, "ABC", dictionaries={})
    a.absorb(b)
    assert a.charset_hist[1] == 2  # 'abc' x2
    assert a.charset_hist[2] == 1  # 'ABC' x1


def test_camino_rapido_no_dobla_pattern_para_dicts_no_es():
    """Diccionarios cuyo nombre no acaba en _es solo cuentan como 'dictionary'."""
    dicts = {"otros": {"hola": 0}}
    acc = StatsAccumulator()
    update_stats(acc, "holaaa", dictionaries=dicts)
    types = {ptype for (ptype, _) in acc.pattern_stats.keys()}
    assert "dictionary" in types
    # 'spanish' solo aparecería desde cultural_terms; "holaaa" no es uno.
    spanish_keys = [k for k in acc.pattern_stats if k[0] == "spanish"]
    # Si hay alguno tendrá que ser cultural=hola (que SÍ está en CULTURAL_TERMS)
    for (_, repr_str) in spanish_keys:
        assert repr_str.startswith("src=cultural"), repr_str


# ---------------------------------------------------------------------------
# Ruta light (Decisión v5.2): solo length + charset, sin heavy fields
# ---------------------------------------------------------------------------

def test_light_acumula_longitud():
    acc = LightStatsAccumulator()
    update_light_stats(acc, "abc")
    update_light_stats(acc, "abcdef")
    update_light_stats(acc, "abc")
    assert acc.length_hist == {3: 2, 6: 1}


def test_light_acumula_charset_mask():
    """charset_mask: 1=lower, 2=upper, 4=digit, 8=symbol, 16=unicode."""
    acc = LightStatsAccumulator()
    update_light_stats(acc, "abc")        # solo lower -> 1
    update_light_stats(acc, "ABC")        # solo upper -> 2
    update_light_stats(acc, "abc123")     # lower + digit -> 5
    update_light_stats(acc, "abc!")       # lower + symbol -> 9
    assert acc.charset_hist == {1: 1, 2: 1, 5: 1, 9: 1}


def test_light_password_vacio_no_modifica():
    acc = LightStatsAccumulator()
    update_light_stats(acc, "")
    assert acc.length_hist == {}
    assert acc.charset_hist == {}


def test_light_no_tiene_campos_heavy():
    """LightStatsAccumulator NO debe exponer entropy/pattern/token."""
    acc = LightStatsAccumulator()
    assert not hasattr(acc, "entropy_hist")
    assert not hasattr(acc, "pattern_stats")
    assert not hasattr(acc, "token_freqs")


def test_light_absorb_suma_contadores():
    a = LightStatsAccumulator()
    update_light_stats(a, "abc")
    update_light_stats(a, "abc123")  # mask 5
    b = LightStatsAccumulator()
    update_light_stats(b, "abc")     # mask 1
    update_light_stats(b, "ABCDEF")  # mask 2

    a.absorb(b)
    assert a.length_hist == {3: 2, 6: 2}
    assert a.charset_hist == {1: 2, 5: 1, 2: 1}


def test_light_absorb_no_modifica_origen():
    a = LightStatsAccumulator()
    b = LightStatsAccumulator()
    update_light_stats(b, "x")
    snapshot_b_len = dict(b.length_hist)
    snapshot_b_cs = dict(b.charset_hist)
    a.absorb(b)
    # Origen intacto tras absorb
    assert dict(b.length_hist) == snapshot_b_len
    assert dict(b.charset_hist) == snapshot_b_cs


def test_light_no_invoca_detectores_de_patrones():
    """update_light_stats NO debe acabar llamando a pat_dict/dates/leet/etc.

    Si lo hiciera, una contraseña con leet+diccionario poblaría tokens,
    pero el accumulator light no tiene token_freqs ni pattern_stats.
    Confirmación implícita por estructura: si añadiéramos un campo no
    declarado, el dataclass lo rechazaría.
    """
    acc = LightStatsAccumulator()
    update_light_stats(acc, "p4ssw0rd")  # con leet
    update_light_stats(acc, "verano2024")  # con diccionario
    # Solo length y charset poblados
    assert acc.length_hist == {8: 1, 10: 1}
    assert sum(acc.charset_hist.values()) == 2


def test_heavy_y_light_calculan_misma_charset_y_length():
    """Paridad entre StatsAccumulator y LightStatsAccumulator en sus dos
    métricas comunes: una contraseña debe producir las mismas cuentas."""
    pwds = ["abc", "ABC123!", "café", "p4ssw0rd"]

    light = LightStatsAccumulator()
    for p in pwds:
        update_light_stats(light, p)

    heavy = StatsAccumulator()
    for p in pwds:
        update_stats(heavy, p, dictionaries={})

    assert light.length_hist == heavy.length_hist
    assert light.charset_hist == heavy.charset_hist
