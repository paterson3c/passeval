"""Detectores de patrones de baja entropía en contraseñas.

Cada submódulo expone `detect(s: str) -> list[Match]` con un patrón
concreto:

- `dictionary`: palabras de diccionarios cargados desde `data/dictionaries/`.
- `dates`     : años sueltos, DDMMYYYY, MMDDYYYY, DDMMYY, YYYY-MM-DD.
- `keyboard`  : runs horizontales en QWERTY español (incluye Ñ).
- `repetition`: repeticiones `aaaa` y patrones cortos `abab`.
- `sequences` : secuencias alfabéticas y numéricas crecientes/decrecientes.
- `leet`      : sustituciones leet con cruce contra diccionario.
- `spanish`   : nombres/topónimos/términos culturales españoles.
"""
from __future__ import annotations

from passeval.strength.patterns.match import Match

__all__ = ["Match"]
