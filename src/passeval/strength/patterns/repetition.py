"""Detector de repeticiones.

Reconoce dos tipos:

- Repetición de un solo carácter de longitud >= 3 (`aaaa`, `1111`).
- Repetición de un patrón corto de período 2..4 al menos dos veces
  (`abab`, `123123`, `xyzxyz`).

Coste (sección 4.6.3 Fase 1):

    guesses = cost_base * count

donde `count` es el número de repeticiones y `cost_base` es el coste
de adivinar la unidad repetida: tamaño del alfabeto del carácter para
las repeticiones single, o `N^period` para los períodos. Se usa el
charset reducido del segmento (no el universo completo).

Las comparaciones se hacen sobre la cadena en minúsculas para que
`AaAa` se considere repetición de `aa`. Los matches solapados son
posibles y se dejan sin deduplicar; corresponde al modelo de scoring
quedarse con la descomposición de menor coste.
"""
from __future__ import annotations

from passeval.strength.patterns.match import Match


def _alphabet_size(ch: str) -> int:
    """Tamaño del alfabeto del carácter (lower=26, upper=26, digit=10, otros=33)."""
    if ch.isdigit():
        return 10
    if "a" <= ch <= "z":
        return 26
    if "A" <= ch <= "Z":
        return 26
    return 33


def _segment_charset(seg: str) -> int:
    """Tamaño del alfabeto unión de los caracteres del segmento."""
    has_lower = any("a" <= c <= "z" for c in seg)
    has_upper = any("A" <= c <= "Z" for c in seg)
    has_digit = any(c.isdigit() for c in seg)
    has_symbol = any(
        not (c.isalpha() or c.isdigit()) for c in seg
    )
    n = 0
    if has_lower:
        n += 26
    if has_upper:
        n += 26
    if has_digit:
        n += 10
    if has_symbol:
        n += 33
    return max(n, 1)


def detect(s: str) -> list[Match]:
    matches: list[Match] = []
    if not s:
        return matches
    s_lower = s.lower()
    n = len(s_lower)

    # Repeticiones de un solo carácter (length >= 3).
    i = 0
    while i < n:
        j = i + 1
        while j < n and s_lower[j] == s_lower[i]:
            j += 1
        if j - i >= 3:
            count = j - i
            cost_base = _alphabet_size(s[i])
            matches.append(
                Match(
                    type="repetition",
                    start=i,
                    end=j,
                    token=s[i:j],
                    guesses=cost_base * count,
                    metadata={
                        "kind": "single",
                        "base": s[i:i + 1],
                        "count": count,
                        "cost_base": cost_base,
                    },
                )
            )
        i = j

    # Repeticiones de patrón corto, período 2..4, al menos 2 ocurrencias.
    for period in (2, 3, 4):
        start = 0
        while start <= n - period * 2:
            base = s_lower[start:start + period]
            count = 1
            j = start + period
            while j + period <= n and s_lower[j:j + period] == base:
                count += 1
                j += period
            if count >= 2:
                base_seg = s[start:start + period]
                cost_base = _segment_charset(base_seg) ** period
                matches.append(
                    Match(
                        type="repetition",
                        start=start,
                        end=j,
                        token=s[start:j],
                        guesses=cost_base * count,
                        metadata={
                            "kind": "period",
                            "base": base_seg,
                            "period": period,
                            "count": count,
                            "cost_base": cost_base,
                        },
                    )
                )
                start = j
            else:
                start += 1

    return matches
