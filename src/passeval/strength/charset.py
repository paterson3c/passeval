"""Detección de alfabeto y entropía por espacio de búsqueda.

Modelo "naive" de fortaleza basado en el tamaño del alfabeto detectado:
    H_charset = L · log2(N)
donde L es la longitud de la contraseña y N el tamaño del alfabeto efectivo.

Esta métrica sobreestima la fortaleza real de contraseñas con patrón
(palabras, fechas, secuencias) pero es útil como cota superior y como
referencia comparativa frente al modelo Wheeler 2016 que se implementa
en `strength.model`.

Mascara de bits del alfabeto detectado:
    1  = lower    (a-z, 26 caracteres)
    2  = upper    (A-Z, 26 caracteres)
    4  = digit    (0-9, 10 caracteres)
    8  = symbol   (ASCII imprimible no alfanumérico, 33 caracteres
                   incluyendo espacio)
   16  = unicode  (cualquier carácter no ASCII; tamaño efectivo 100,
                   aproximación conservadora del subconjunto realmente
                   accesible al usuario en contextos comunes)
"""
from __future__ import annotations

import math

LOWER_MASK = 1
UPPER_MASK = 2
DIGIT_MASK = 4
SYMBOL_MASK = 8
UNICODE_MASK = 16

# Tamaños del alfabeto por tipo (cardinalidad efectiva).
SIZES = {
    LOWER_MASK: 26,
    UPPER_MASK: 26,
    DIGIT_MASK: 10,
    SYMBOL_MASK: 33,
    UNICODE_MASK: 100,
}


def charset_mask(s: str) -> int:
    """Devuelve el bitmask del alfabeto detectado en la cadena.

    Una sola pasada sobre la cadena, sin regex. Se sale temprano si ya
    se detectaron todos los tipos posibles.
    """
    mask = 0
    full = LOWER_MASK | UPPER_MASK | DIGIT_MASK | SYMBOL_MASK | UNICODE_MASK
    for ch in s:
        code = ord(ch)
        if code > 127:
            mask |= UNICODE_MASK
        elif "a" <= ch <= "z":
            mask |= LOWER_MASK
        elif "A" <= ch <= "Z":
            mask |= UPPER_MASK
        elif "0" <= ch <= "9":
            mask |= DIGIT_MASK
        elif 32 <= code <= 126:
            mask |= SYMBOL_MASK
        # Caracteres de control (<32 o 127) no contribuyen.
        if mask == full:
            break
    return mask


def charset_size_from_mask(mask: int) -> int:
    """Suma los tamaños de los alfabetos presentes en el bitmask."""
    total = 0
    for bit, size in SIZES.items():
        if mask & bit:
            total += size
    return total


def charset_entropy(s: str) -> tuple[int, float]:
    """Devuelve (tamaño_alfabeto, bits_totales = L · log2(N)).

    Para cadena vacía devuelve (0, 0.0). Para alfabeto vacío (cadena
    compuesta exclusivamente de caracteres de control no contemplados)
    devuelve (0, 0.0) también.
    """
    if not s:
        return 0, 0.0
    mask = charset_mask(s)
    n = charset_size_from_mask(mask)
    if n == 0:
        return 0, 0.0
    return n, len(s) * math.log2(n)
