"""Entropía de Shannon a nivel de carácter.

Definición:
    H(X) = - Σ p(x_i) · log2 p(x_i)
donde p(x_i) es la frecuencia relativa del símbolo x_i en la cadena.

Referencia:
    Shannon, C. E. (1948). "A Mathematical Theory of Communication".
    Bell System Technical Journal 27 (3): 379-423.

La entropía de Shannon mide la incertidumbre por carácter de la cadena
observada. NO es una medida de fortaleza criptográfica (no considera el
espacio de búsqueda ni patrones); se reporta junto a otras métricas en
el informe de evaluación como característica descriptiva.
"""
from __future__ import annotations

import math
from collections import Counter


def shannon_entropy(s: str) -> float:
    """Entropía de Shannon en bits por carácter.

    - Devuelve 0.0 para cadenas vacías o de un solo carácter (no hay
      incertidumbre observable).
    - Para cadenas uniformes con N símbolos distintos: log2(N).
    """
    if len(s) <= 1:
        return 0.0
    n = len(s)
    counts = Counter(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())
