"""Acumuladores en RAM para los volcados a `stats.*`.

El ETL no escribe en `stats.*` por cada línea: acumula en `Counter`
y sumas durante un batch (~1M líneas) y vuelca al final con
`INSERT ... ON CONFLICT DO UPDATE SET count = count + EXCLUDED.count`,
de modo que reanudaciones posteriores se suman sin duplicar.

Las funciones puras de este módulo se testan sin tocar BBDD; el flush
real se hace desde `etl.ingest` con un cursor ya abierto.

Dos rutas de acumulación (controladas por --mode en ingest.py):

- **Light** (`LightStatsAccumulator` + `update_light_stats`):
  solo `length_histogram`, `charset_histogram` y `sum_length`.
  Coste por contraseña ~3 µs. Suficiente para los porcentajes de
  composición del capítulo 5.3.

- **Heavy/Full** (`StatsAccumulator` + `update_stats`): además de length y
  charset puebla `entropy_histogram`, `pattern_stats` y
  `token_frequencies` (los siete detectores y los lookups en
  diccionarios de 155k entradas), más sumas acumuladas para medias
  exactas. Full añade `score_hist` y `sum_guesses`.

Estadísticas cubiertas por la ruta heavy:

- length_histogram: conteo por longitud.
- charset_histogram: conteo por bitmask exacto (lower/upper/digit/symbol/unicode).
- entropy_histogram (shannon, charset): bucket de 0,5 bits truncando
  hacia abajo (`floor(bits * 2) / 2`). El bucket [a, a+0,5) se identifica
  por `bucket_min=a`.
- pattern_stats: `(pattern_type, pattern_repr) -> count`. `pattern_repr`
  es una etiqueta legible y compacta (no la cadena en claro: respeta la
  privacidad del dataset y mantiene la cardinalidad acotada).
- token_frequencies: `(token_type, token) -> count` con dos tipos:
    - `'substring'`: palabras de diccionario detectadas dentro de la
      contraseña (vía `dictionary.detect` + `spanish.detect`).
    - `'deleetified'`: forma deleet de la contraseña entera si contenía
      al menos un carácter leet y la forma resultante tiene >= 3 letras.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from passeval.strength.charset import charset_mask, charset_size_from_mask
from passeval.strength.patterns import dates as pat_dates
from passeval.strength.patterns import dictionary as pat_dict
from passeval.strength.patterns import keyboard as pat_kb
from passeval.strength.patterns import leet as pat_leet
from passeval.strength.patterns import repetition as pat_rep
from passeval.strength.patterns import sequences as pat_seq
from passeval.strength.patterns import spanish as pat_es
from passeval.strength.shannon import shannon_entropy

BUCKET_STEP = 0.5
DELEET_MIN_LEN = 3
TOKEN_MAX_LEN = 64  # debe coincidir con stats.token_frequencies.token VARCHAR(64)
DICT_MIN_LEN = 3   # longitud mínima de substring para buscar en diccionarios

# Cultural terms del módulo `spanish` reproducidos como conjunto para
# lookup O(1) en el camino rápido (evita reentrar en pat_es.detect).
_CULTURAL_RANK: dict[str, int] = {term: rank for rank, term in enumerate(pat_es.CULTURAL_TERMS)}


_BUCKET_MAX = 99999.0  # cota NUMERIC(10,2): passwords de >6000 chars unicode

def entropy_bucket(bits: float) -> float:
    """Devuelve el `bucket_min` correspondiente a `bits` (paso 0,5)."""
    if bits < 0:
        return 0.0
    return min(math.floor(bits / BUCKET_STEP) * BUCKET_STEP, _BUCKET_MAX)


def _is_leet(s: str) -> bool:
    return any(c in pat_leet.LEET_MAP for c in s)


# ---------------------------------------------------------------------------
# Ruta light (Decisión v5.2): solo length + charset, coste insignificante
# ---------------------------------------------------------------------------

@dataclass
class LightStatsAccumulator:
    """Contador para las métricas baratas (length + charset + sum_length)."""

    length_hist: Counter = field(default_factory=Counter)
    charset_hist: Counter = field(default_factory=Counter)
    sum_length: int = 0

    def absorb(self, other: "LightStatsAccumulator") -> None:
        self.length_hist.update(other.length_hist)
        self.charset_hist.update(other.charset_hist)
        self.sum_length += other.sum_length


def update_light_stats(acc: LightStatsAccumulator, password: str) -> None:
    """Actualiza `length_hist`, `charset_hist` y `sum_length` para `password`."""
    if not password:
        return
    n = len(password)
    acc.length_hist[n] += 1
    acc.charset_hist[charset_mask(password)] += 1
    acc.sum_length += n


# ---------------------------------------------------------------------------
# Ruta heavy: el accumulator completo y `update_stats` original
# ---------------------------------------------------------------------------

@dataclass
class StatsAccumulator:
    """Contador agregado para todos los tipos de estadísticas (heavy + full)."""

    length_hist: Counter = field(default_factory=Counter)
    entropy_hist: Counter = field(default_factory=Counter)
    pattern_stats: Counter = field(default_factory=Counter)
    token_freqs: Counter = field(default_factory=Counter)
    charset_hist: Counter = field(default_factory=Counter)
    score_hist: Counter = field(default_factory=Counter)

    sum_length: int = 0
    sum_shannon: float = 0.0
    sum_charset_bits: float = 0.0
    sum_guesses: float = 0.0

    def absorb(self, other: "StatsAccumulator") -> None:
        self.length_hist.update(other.length_hist)
        self.entropy_hist.update(other.entropy_hist)
        self.pattern_stats.update(other.pattern_stats)
        self.token_freqs.update(other.token_freqs)
        self.charset_hist.update(other.charset_hist)
        self.score_hist.update(other.score_hist)
        self.sum_length += other.sum_length
        self.sum_shannon += other.sum_shannon
        self.sum_charset_bits += other.sum_charset_bits
        self.sum_guesses += other.sum_guesses


def update_stats(
    acc: StatsAccumulator,
    password: str,
    dictionaries: dict[str, dict[str, int]] | None = None,
) -> None:
    """Actualiza `acc` con todas las métricas derivadas de `password`.

    Diseñada para llamarse una vez por contraseña dentro del bucle del
    ETL. No hace IO; es pura sobre `acc`.
    """
    if not password:
        return

    n = len(password)
    acc.length_hist[n] += 1
    acc.sum_length += n

    # Entropías -> bucket 0,5 bits
    sh_total = shannon_entropy(password) * n
    mask = charset_mask(password)
    n_alphabet = charset_size_from_mask(mask)
    ch_total = n * math.log2(n_alphabet) if n_alphabet > 0 else 0.0
    acc.entropy_hist[("shannon", entropy_bucket(sh_total))] += 1
    acc.entropy_hist[("charset", entropy_bucket(ch_total))] += 1
    acc.sum_shannon += sh_total
    acc.sum_charset_bits += ch_total

    # Histograma de composición exacta (v4 §A.2): combinación de bits
    # del alfabeto. Permite responder "% de pwd con exactamente
    # lower+digit+symbol", "% con unicode", etc., sin recomputar.
    acc.charset_hist[mask] += 1

    # Patterns que no consumen substrings (rápidos): dates, keyboard,
    # repetition, sequences. Cada uno itera la cadena en O(L).
    for det in (pat_dates, pat_kb, pat_rep, pat_seq):
        for m in det.detect(password):
            acc.pattern_stats[(m.type, _pattern_repr(m))] += 1

    # Camino rápido para dictionary + spanish: una sola iteración de
    # substrings, todas las consultas a diccionarios en el mismo bucle.
    # Reemplaza la doble pasada de `pat_dict.detect` + `pat_es.detect`
    # del ETL legacy. Mantiene el mismo número de incrementos por
    # token_freqs que el legacy (paridad bit-a-bit, ver tests):
    #
    #   - pat_dict.detect: 1 incremento por cada hit de cualquier dict.
    #   - pat_es.detect:   1 incremento por cada hit de cultural_terms
    #                      + 1 incremento por cada hit de un dict _es.
    #
    # Por tanto un substring que matchee en `palabras_es` AND `cultural`
    # genera 3 incrementos (1 dict + 1 cultural + 1 _es-extra) — esto es
    # el comportamiento original.
    if dictionaries is None:
        dictionaries = pat_dict.load_dictionaries()
    s_lower = password.lower()
    for start in range(n):
        for end in range(start + DICT_MIN_LEN, n + 1):
            substr = s_lower[start:end]
            sub_len = end - start
            in_token_range = sub_len <= TOKEN_MAX_LEN
            # Cultural terms (equivalente a la primera mitad de pat_es.detect).
            if substr in _CULTURAL_RANK:
                rank = _CULTURAL_RANK[substr]
                acc.pattern_stats[("spanish", f"src=cultural;rank={rank}")] += 1
                if in_token_range:
                    acc.token_freqs[("substring", substr)] += 1
            # Diccionarios: cada hit emite un pattern_stats type='dictionary'
            # y +1 token_freqs (equivalente al legacy pat_dict.detect).
            # Si el dict es _es, además emite type='spanish' Y +1
            # token_freqs adicional (equivalente a pat_es.detect sobre _es).
            for dict_name, ranked in dictionaries.items():
                rank = ranked.get(substr)
                if rank is None:
                    continue
                acc.pattern_stats[("dictionary", f"dict={dict_name};rank={rank}")] += 1
                if in_token_range:
                    acc.token_freqs[("substring", substr)] += 1
                if dict_name.endswith("_es"):
                    acc.pattern_stats[("spanish", f"src={dict_name};rank={rank}")] += 1
                    if in_token_range:
                        acc.token_freqs[("substring", substr)] += 1

    # Leet: si el password tiene caracteres leet, registrar la forma
    # deleetificada como token de tipo 'deleetified'.
    if _is_leet(password):
        deleet = pat_leet.deleet(password)
        if DELEET_MIN_LEN <= len(deleet) <= TOKEN_MAX_LEN and deleet.isalpha():
            acc.token_freqs[("deleetified", deleet)] += 1


def _pattern_repr(m) -> str:
    """Repr legible y compacta de un Match para `stats.pattern_stats`.

    No incluye la cadena cruda salvo cuando el patrón ya carga señal
    estructural (p. ej. el rank del diccionario). Respeta la cota de
    255 chars del esquema `stats.pattern_stats.pattern_repr`.
    """
    md = m.metadata or {}
    if m.type == "dictionary":
        return f"dict={md.get('dict_name', '?')};rank={md.get('rank', '?')}"
    if m.type == "spanish":
        return f"src={md.get('source', '?')};rank={md.get('rank', '?')}"
    if m.type == "leet":
        return f"dict={md.get('dict_name', '?')};subs={md.get('substitutions', '?')}"
    if m.type == "date":
        return f"format={md.get('format', '?')}"
    if m.type == "keyboard":
        return f"len={md.get('length', '?')};dir={md.get('direction', '?')}"
    if m.type == "sequence":
        return f"len={md.get('length', '?')};dir={md.get('direction', '?')};kind={md.get('kind', '?')}"
    if m.type == "repetition":
        return f"kind={md.get('kind', '?')};count={md.get('count', '?')}"
    return "."
