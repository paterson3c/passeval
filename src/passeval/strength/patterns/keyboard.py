"""Detector de runs de teclado QWERTY español.

Reconoce secuencias horizontales (mismo row) de longitud >= 3 sobre el
layout QWERTY-ES, incluyendo la fila central con `ñ`. Solo se modelan
runs adyacentes en la misma fila ("qwert", "asdfg", "12345", "0987");
no se modelan diagonales ("qaz") ni saltos.

Coste (aproximación de la fórmula espacial de Wheeler 2016, sección
4.6.3 Fase 1):

    guesses = L * direcciones * teclas_por_nivel

donde `L` es la longitud del run, `direcciones=2` (left/right) y
`teclas_por_nivel` es el tamaño de la fila en que ocurre el run.

Notas:
- La detección es case-insensitive.
- Caracteres fuera del layout (acentos, símbolos no presentes) cortan
  el run.
- Se emite metadata con `direction` ("right"/"left"), `length` y `row`.
"""
from __future__ import annotations

from passeval.strength.patterns.match import Match

_ROWS = (
    "1234567890",
    "qwertyuiop",
    "asdfghjklñ",
    "zxcvbnm",
)

_POS: dict[str, tuple[int, int]] = {}
for _r, _row in enumerate(_ROWS):
    for _c, _ch in enumerate(_row):
        _POS[_ch] = (_r, _c)


def detect(s: str) -> list[Match]:
    matches: list[Match] = []
    s_lower = s.lower()
    n = len(s_lower)
    if n < 3:
        return matches

    i = 0
    while i < n - 1:
        if s_lower[i] not in _POS or s_lower[i + 1] not in _POS:
            i += 1
            continue
        r0, c0 = _POS[s_lower[i]]
        r1, c1 = _POS[s_lower[i + 1]]
        if r0 != r1 or abs(c1 - c0) != 1:
            i += 1
            continue
        direction = c1 - c0  # +1 o -1
        j = i + 2
        while j < n and s_lower[j] in _POS:
            r_prev, c_prev = _POS[s_lower[j - 1]]
            r_curr, c_curr = _POS[s_lower[j]]
            if r_prev != r_curr or (c_curr - c_prev) != direction:
                break
            j += 1
        if j - i >= 3:
            length = j - i
            keys_in_row = len(_ROWS[r0])
            guesses = length * 2 * keys_in_row
            matches.append(
                Match(
                    type="keyboard",
                    start=i,
                    end=j,
                    token=s[i:j],
                    guesses=guesses,
                    metadata={
                        "length": length,
                        "direction": "right" if direction == 1 else "left",
                        "row": r0,
                        "keys_in_row": keys_in_row,
                    },
                )
            )
            i = j
        else:
            i += 1
    return matches
