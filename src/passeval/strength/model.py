"""Modelo propio de scoring de fortaleza (Wheeler 2016 adaptado).

Implementa el algoritmo de scoring en cinco fases:

- **Fase 1**: detección de matches (delegada a `passeval.strength.patterns`).
- **Fase 2**: descomposición mínima por programación dinámica (`decompose`).
- **Fase 3**: coste del bruteforce residual (`bruteforce_cost`).
- **Fase 4**: composición + sanity check `min(G_raw, S_charset)`
  (`estimate_guesses`).
- **Fase 5**: mapeo a score categórico 0-4 (`score`).

Referencia: Wheeler, D.L. (2016). "zxcvbn: Low-Budget Password Strength
Estimation", USENIX Security Symposium. La descomposición DP y la
aproximación de coste por segmento provienen de ese trabajo; las
adaptaciones específicas (alfabeto Unicode, calibración con
`stats.token_frequencies`) se documentan en la memoria del TFG.

Convenciones de coste:

- En la DP el bruteforce se extiende **carácter a carácter** con coste
  `N_full` por cada carácter, donde `N_full` es la cardinalidad del
  alfabeto completo de la contraseña (lower+upper+digit+symbol+unicode
  según los tipos detectados en `s`). Esto modela un atacante que **no
  conoce la estructura interna** y debe probar el alfabeto completo en
  cada posición; coincide con la convención de zxcvbn (Wheeler 2016) y
  hace que un bruteforce puro tienda a `N_full^L = S_charset(s)`.
- `bruteforce_cost(segment)` (Fase 3, ver función abajo) usa el
  alfabeto **del segmento concreto** `N_seg^L_seg`. Este coste se
  expone para análisis de subsegmentos fuera de la DP; dentro de la DP
  no se usa porque requeriría lookahead (no sabemos cuándo va a
  terminar el bruteforce mientras lo extendemos).
- El factor de configuración es `k!` donde `k` es el número de segmentos
  del path final (tras fusionar bruteforces consecutivos). Refleja las
  formas distintas en que el atacante podría haber ordenado los `k`
  bloques.
- El sanity check `G_final = min(G_raw, S_charset(s))` impide que el
  modelo devuelva valores superiores al bruteforce puro de toda la
  contraseña: un atacante siempre puede caer en esa cota.

La función pública principal es `score(s, token_freq=None)` que ejecuta
todo el pipeline y devuelve un entero 0-4. `estimate_guesses` y
`decompose` se exponen para tests y para análisis didáctico (mostrar
al usuario la descomposición elegida).
"""
from __future__ import annotations

import math
from typing import Iterable

from passeval.strength.charset import charset_mask, charset_size_from_mask
from passeval.strength.patterns import (
    Match,
    dates,
    dictionary,
    keyboard,
    leet,
    repetition,
    sequences,
    spanish,
)

# Umbrales de la Fase 5 (basados en Wheeler 2016 con el ajuste documentado
# en §4.6.3 para contemplar atacantes modernos con GPU).
SCORE_THRESHOLDS: tuple[int, ...] = (10, 20, 35, 60)
SCORE_LABELS: tuple[str, ...] = (
    "Trivial",
    "Muy débil",
    "Débil",
    "Fuerte",
    "Muy fuerte",
)


# ---------------------------------------------------------------------------
# Fase 3: coste de un segmento bruteforce
# ---------------------------------------------------------------------------

def bruteforce_cost(segment: str) -> int:
    """Coste de un segmento por bruteforce: `N_seg^L_seg` (Fase 3).

    `N_seg` es el alfabeto **del segmento concreto** (no del universo),
    para no sobreestimar. Cadena vacía -> 1 (producto vacío).
    """
    if not segment:
        return 1
    n = charset_size_from_mask(charset_mask(segment))
    return max(n, 1) ** len(segment)


# ---------------------------------------------------------------------------
# Fase 2: descomposición mínima (DP) — pseudocódigo §4.6.3 Fase 2
# ---------------------------------------------------------------------------

def decompose(s: str, matches: list[Match]) -> tuple[list[Match], int]:
    """Descomposición mínima de `s` en matches + bruteforce.

    Algoritmo de programación dinámica de Wheeler 2016, Fase 2:

        full_n = charset(s)             # alfabeto completo de la contraseña
        best_cost[0] = 1
        for i in 1..n:
            # extender bruteforce un carácter (atacante sin conocer estructura)
            best_cost[i] = best_cost[i-1] * full_n
            # o terminar con un match m donde m.end == i
            for m in matches con m.end == i:
                cand = best_cost[m.start] * max(m.guesses, 1)
                if cand < best_cost[i]: actualizar

    `full_n` es la cardinalidad del alfabeto completo de la contraseña
    (no de cada carácter individual): el atacante no conoce qué
    posición es letra, dígito o símbolo, así que en cada posición debe
    probar todo el alfabeto. Esto hace que el bruteforce puro tienda a
    `N_full^L = S_charset(s)`, comportamiento esperado por el sanity
    check de la Fase 4.

    Devuelve `(path, coste)` donde `path` es la lista de segmentos que
    cubren toda la cadena. Los segmentos bruteforce se modelan como
    `Match(type='bruteforce', ..., metadata={'reason': 'single_char_bruteforce'})`
    (uno por carácter; ver `_merge_bruteforce` para la fusión final).

    `max(m.guesses, 1)` evita que un match con `guesses=0` (rank 0 del
    diccionario combinado con leet o cualquier otra causa) anule el
    producto haciéndolo cero.
    """
    n = len(s)
    if n == 0:
        return [], 1

    full_n = max(charset_size_from_mask(charset_mask(s)), 1)

    matches_by_end: dict[int, list[Match]] = {}
    for m in matches:
        if 0 <= m.start < m.end <= n:
            matches_by_end.setdefault(m.end, []).append(m)

    best_cost: list[int] = [1] + [0] * n
    best_path: list[list[Match]] = [[] for _ in range(n + 1)]

    for i in range(1, n + 1):
        # Opción A: extender bruteforce un carácter.
        bf_seg = Match(
            type="bruteforce",
            start=i - 1,
            end=i,
            token=s[i - 1 : i],
            guesses=full_n,
            metadata={"reason": "single_char_bruteforce", "cardinality": full_n},
        )
        best_cost[i] = best_cost[i - 1] * full_n
        best_path[i] = best_path[i - 1] + [bf_seg]

        # Opción B: terminar con un match m que acabe en i.
        for m in matches_by_end.get(i, []):
            cand = best_cost[m.start] * max(m.guesses, 1)
            if cand < best_cost[i]:
                best_cost[i] = cand
                best_path[i] = best_path[m.start] + [m]

    return best_path[n], best_cost[n]


def _merge_bruteforce(path: list[Match]) -> list[Match]:
    """Fusiona segmentos bruteforce consecutivos en un único segmento.

    El producto de costes se mantiene (es exactamente el mismo número
    que se acumuló en la DP carácter a carácter). El propósito de la
    fusión es contar correctamente `k` para el factor de configuración
    y producir un path didáctico para la CLI.
    """
    out: list[Match] = []
    for m in path:
        if (
            m.type == "bruteforce"
            and out
            and out[-1].type == "bruteforce"
            and out[-1].end == m.start
        ):
            prev = out[-1]
            out[-1] = Match(
                type="bruteforce",
                start=prev.start,
                end=m.end,
                token=prev.token + m.token,
                guesses=prev.guesses * m.guesses,
                metadata={"reason": "bruteforce_merged", "length": m.end - prev.start},
            )
        else:
            out.append(m)
    return out


# ---------------------------------------------------------------------------
# Detección y calibración (Fase 1 + §4.6.5)
# ---------------------------------------------------------------------------

def _detect_all(
    s: str,
    dictionaries: dict[str, dict[str, int]] | None = None,
) -> list[Match]:
    """Ejecuta todos los detectores y concatena sus matches sin deduplicar.

    La deduplicación es responsabilidad de la DP: la descomposición de
    menor coste eliminará automáticamente los matches solapados que no
    convengan.
    """
    if dictionaries is None:
        dictionaries = dictionary.load_dictionaries()
    out: list[Match] = []
    out.extend(dictionary.detect(s, dictionaries))
    out.extend(spanish.detect(s, dictionaries))
    out.extend(dates.detect(s))
    out.extend(keyboard.detect(s))
    out.extend(repetition.detect(s))
    out.extend(sequences.detect(s))
    out.extend(leet.detect(s, dictionaries))
    return out


def _calibrate(
    matches: Iterable[Match],
    token_freq: dict[str, int],
    total_tokens: int,
) -> list[Match]:
    """Recalcula `guesses` de matches `dictionary`/`spanish` con frecuencias.

    Implementa §4.6.5: `guesses_ajustado = total_tokens / freq(token)`.
    Si el token no aparece en `token_freq`, el match se deja como está.
    """
    out: list[Match] = []
    for m in matches:
        if m.type in ("dictionary", "spanish"):
            tok = m.token.lower()
            f = token_freq.get(tok)
            if f and f > 0:
                out.append(
                    Match(
                        type=m.type,
                        start=m.start,
                        end=m.end,
                        token=m.token,
                        guesses=max(total_tokens // f, 1),
                        metadata={
                            **m.metadata,
                            "calibrated": True,
                            "empirical_freq": f,
                        },
                    )
                )
                continue
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# Fase 4 + 5: estimación final y score categórico
# ---------------------------------------------------------------------------

def _safe_log2(x: int) -> float:
    """log2 robusto frente a enteros enormes (usa bit_length si overflow)."""
    if x <= 1:
        return 0.0
    try:
        return math.log2(x)
    except OverflowError:
        # Para enteros que no caben en float, log2 ≈ bit_length - 1.
        return float(x.bit_length() - 1)


def estimate_guesses(
    s: str,
    matches: list[Match] | None = None,
    token_freq: dict[str, int] | None = None,
    dictionaries: dict[str, dict[str, int]] | None = None,
) -> int:
    """Coste total `G_final` con factor de configuración y sanity check.

    Pipeline completo de la Fase 4:

        1. Si no se pasan matches, los ejecuta todos los detectores.
        2. Si se pasa `token_freq`, recalibra los matches dict/spanish.
        3. Resuelve la DP -> (path, G_raw).
        4. Cuenta `k` = número de segmentos en el path fusionado.
        5. G_with_config = G_raw * k!
        6. G_final = min(G_with_config, S_charset(s)) — sanity check.

    Para `s` vacía devuelve 1 (no hay nada que adivinar pero el modelo
    multiplicativo necesita un valor neutro).
    """
    if not s:
        return 1
    if matches is None:
        matches = _detect_all(s, dictionaries)
    if token_freq is not None:
        total = sum(token_freq.values()) or 1
        matches = _calibrate(matches, token_freq, total)

    path, g_raw = decompose(s, matches)
    merged = _merge_bruteforce(path)
    k = len(merged)
    g_with_config = g_raw * math.factorial(k)

    n = charset_size_from_mask(charset_mask(s))
    s_charset = max(n, 1) ** len(s)
    return min(g_with_config, s_charset)


def score(
    s: str,
    token_freq: dict[str, int] | None = None,
    dictionaries: dict[str, dict[str, int]] | None = None,
) -> int:
    """Score categórico 0-4 según los umbrales de §4.6.3 Fase 5.

    | log2(G)       | Score | Etiqueta   |
    |---------------|-------|------------|
    | < 10          | 0     | Trivial    |
    | [10, 20)      | 1     | Muy débil  |
    | [20, 35)      | 2     | Débil      |
    | [35, 60)      | 3     | Fuerte     |
    | >= 60         | 4     | Muy fuerte |
    """
    if not s:
        return 0
    g = estimate_guesses(s, token_freq=token_freq, dictionaries=dictionaries)
    bits = _safe_log2(g)
    for i, threshold in enumerate(SCORE_THRESHOLDS):
        if bits < threshold:
            return i
    return len(SCORE_THRESHOLDS)
