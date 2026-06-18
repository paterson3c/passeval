"""Detector de secuencias alfabéticas y numéricas.

Detecta runs de longitud >= 3 donde cada carácter consecutivo difiere
en +1 o -1 respecto del anterior, manteniendo la misma "clase" (todos
dígitos o todos letras). Ejemplos: `abc`, `cba`, `123`, `9876`.

Coste (sección 4.6.3 Fase 1):

    guesses = L * 4

donde `L` es la longitud del run y `4 = 2 alfabetos × 2 direcciones`.

No detecta cambios de clase (`a1b2` no es secuencia) ni saltos > 1
(`acegi`). La metadata distingue la dirección (asc/desc) y la clase
(numeric/alphabetic).
"""
from __future__ import annotations

from passeval.strength.patterns.match import Match


def _same_class(c1: str, c2: str) -> bool:
    return (c1.isdigit() and c2.isdigit()) or (c1.isalpha() and c2.isalpha())


def _kind(c: str) -> str:
    return "numeric" if c.isdigit() else "alphabetic"


def detect(s: str) -> list[Match]:
    matches: list[Match] = []
    n = len(s)
    if n < 3:
        return matches

    s_lower = s.lower()
    i = 0
    while i < n - 1:
        if not _same_class(s_lower[i], s_lower[i + 1]):
            i += 1
            continue
        delta = ord(s_lower[i + 1]) - ord(s_lower[i])
        if delta not in (1, -1):
            i += 1
            continue
        j = i + 2
        while (
            j < n
            and _same_class(s_lower[j - 1], s_lower[j])
            and ord(s_lower[j]) - ord(s_lower[j - 1]) == delta
        ):
            j += 1
        if j - i >= 3:
            length = j - i
            matches.append(
                Match(
                    type="sequence",
                    start=i,
                    end=j,
                    token=s[i:j],
                    guesses=length * 4,
                    metadata={
                        "length": length,
                        "direction": "asc" if delta == 1 else "desc",
                        "kind": _kind(s_lower[i]),
                    },
                )
            )
            i = j
        else:
            i += 1
    return matches
