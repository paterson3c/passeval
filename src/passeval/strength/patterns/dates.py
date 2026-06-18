"""Detector de fechas embebidas en la contraseña.

Reconoce los siguientes formatos:

- Año suelto (1900-2099) en cualquier posición.
- DDMMYYYY (8 dígitos, día primero).
- MMDDYYYY (8 dígitos, mes primero).
- DDMMYY   (6 dígitos; años 50-99 -> 1950-1999, 00-49 -> 2000-2049).
- YYYY-MM-DD (con guiones).

Costes (sección 4.6.3 Fase 1):
- Año suelto:    `guesses = 200` (rango 1900-2099, 200 valores).
- Fecha completa: `guesses = 365 * 200 = 73000` (días * años).

Los formatos de 8 dígitos pueden coincidir simultáneamente; ambos
matches se emiten para que el modelo de scoring escoja la
interpretación que produzca la descomposición de menor coste.
La validez de día/mes se valida con `datetime.date`.
"""
from __future__ import annotations

import re
from datetime import date

from passeval.strength.patterns.match import Match

_YEAR_RE = re.compile(r"(19\d{2}|20\d{2})")
_ISO_RE = re.compile(r"(19\d{2}|20\d{2})-(\d{2})-(\d{2})")

_GUESSES_YEAR = 200
_GUESSES_FULL_DATE = 365 * 200


def _is_ascii_digits(s: str) -> bool:
    """`True` solo para dígitos ASCII 0-9.

    `str.isdigit()` acepta dígitos Unicode (p. ej. superíndices `²³`,
    devanagari `१२`) que `int()` rechaza. En ETL real esto provocaba
    `ValueError: invalid literal for int() with base 10: '²²'` al
    procesar contraseñas con esos caracteres. Esta función filtra
    estrictamente al subconjunto ASCII que `int()` acepta.
    """
    return s.isascii() and s.isdigit()


def _is_valid(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def _expand_two_digit_year(yy: int) -> int:
    return 2000 + yy if yy < 50 else 1900 + yy


def detect(s: str) -> list[Match]:
    matches: list[Match] = []
    n = len(s)

    # YYYY-MM-DD (busqueda primero porque consume separadores).
    for m in _ISO_RE.finditer(s):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _is_valid(y, mo, d):
            matches.append(
                Match(
                    type="date",
                    start=m.start(),
                    end=m.end(),
                    token=m.group(0),
                    guesses=_GUESSES_FULL_DATE,
                    metadata={
                        "year": y,
                        "month": mo,
                        "day": d,
                        "format": "YYYY-MM-DD",
                    },
                )
            )

    # Año suelto en cualquier posición.
    for m in _YEAR_RE.finditer(s):
        y = int(m.group(0))
        matches.append(
            Match(
                type="date",
                start=m.start(),
                end=m.end(),
                token=m.group(0),
                guesses=_GUESSES_YEAR,
                metadata={"year": y, "format": "YYYY"},
            )
        )

    # Ventanas de 8 dígitos: DDMMYYYY y MMDDYYYY (ambas se emiten si son
    # válidas, el modelo de scoring escoge la más barata).
    for i in range(n - 7):
        chunk = s[i:i + 8]
        if not _is_ascii_digits(chunk):
            continue
        y = int(chunk[4:8])
        if y < 1900 or y > 2099:
            continue
        a, b = int(chunk[0:2]), int(chunk[2:4])
        # DDMMYYYY: a=día, b=mes.
        if _is_valid(y, b, a):
            matches.append(
                Match(
                    type="date",
                    start=i,
                    end=i + 8,
                    token=chunk,
                    guesses=_GUESSES_FULL_DATE,
                    metadata={"year": y, "month": b, "day": a, "format": "DDMMYYYY"},
                )
            )
        # MMDDYYYY: a=mes, b=día.
        if _is_valid(y, a, b):
            matches.append(
                Match(
                    type="date",
                    start=i,
                    end=i + 8,
                    token=chunk,
                    guesses=_GUESSES_FULL_DATE,
                    metadata={"year": y, "month": a, "day": b, "format": "MMDDYYYY"},
                )
            )

    # Ventanas de 6 dígitos: DDMMYY.
    for i in range(n - 5):
        chunk = s[i:i + 6]
        if not _is_ascii_digits(chunk):
            continue
        d, mo, yy = int(chunk[0:2]), int(chunk[2:4]), int(chunk[4:6])
        y = _expand_two_digit_year(yy)
        if _is_valid(y, mo, d):
            matches.append(
                Match(
                    type="date",
                    start=i,
                    end=i + 6,
                    token=chunk,
                    guesses=_GUESSES_FULL_DATE,
                    metadata={
                        "year": y,
                        "month": mo,
                        "day": d,
                        "format": "DDMMYY",
                    },
                )
            )

    return matches
