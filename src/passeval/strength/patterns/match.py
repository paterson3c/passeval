"""Tipo compartido `Match` para todos los detectores de patrones.

Un `Match` describe una ocurrencia de un patrón dentro de una cadena: su
tipo, el rango `[start, end)` en la cadena original, el token detectado,
el coste estimado en intentos (`guesses`) y un diccionario `metadata` con
información específica del detector.

`guesses` es una estimación local del coste de adivinar este match en
aislamiento; el modelo de scoring lo combina con descomposición mínima
por programación dinámica.

Los detectores exponen `detect(s: str, ...) -> list[Match]` y pueden
emitir matches solapados; la deduplicación y agregación final son
responsabilidad del modelo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Match:
    type: str
    start: int
    end: int
    token: str
    guesses: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
