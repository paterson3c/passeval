"""Normalización Unicode y decodificación tolerante de bytes.

Decisión 8 (informe v3): se aplica NFC sin trim ni case folding. NFC es la
forma de composición canónica definida en Unicode Standard Annex #15 y
recomendada por RFC 8265 (PRECIS Framework for Usernames and Passwords)
para campos de tipo OpaqueString como contraseñas.

NFC se elige frente a NFKC porque NFKC pliega caracteres visualmente
distintos que el usuario distingue al teclear (ligaduras, superíndices,
formas anchas), lo que alteraría semánticamente contraseñas válidas.
"""
from __future__ import annotations

import unicodedata

REPLACEMENT_CHAR = "�"  # U+FFFD: marcador insertado por errors='replace'


def normalize(password: str) -> str:
    """Aplica normalización Unicode NFC a la contraseña.

    No aplica trim de espacios ni case folding: la contraseña se conserva
    tal cual la introduce el usuario, salvo por la composición canónica
    de las secuencias Unicode equivalentes.
    """
    return unicodedata.normalize("NFC", password)


def decode_line(raw: bytes) -> tuple[str, bool]:
    """Decodifica una línea cruda del dataset a str NFC.

    - Decodifica como UTF-8 con `errors='replace'`: bytes inválidos se
      sustituyen por U+FFFD (REPLACEMENT CHARACTER) en lugar de descartarse
      silenciosamente como hacía el ETL legacy con `errors='ignore'`.
    - Elimina el separador de línea final (\\n, \\r o \\r\\n) si existe;
      el separador es parte del protocolo de fichero, no de la contraseña,
      así que su eliminación no contradice la regla "sin trim" de la D8.
    - Aplica NFC sobre el resultado.

    Devuelve (cadena_normalizada, hubo_bytes_invalidos). El segundo flag
    permite al ETL contabilizar líneas con errores de codificación.
    """
    decoded = raw.decode("utf-8", errors="replace")
    had_invalid = REPLACEMENT_CHAR in decoded

    if decoded.endswith("\r\n"):
        decoded = decoded[:-2]
    elif decoded.endswith(("\n", "\r")):
        decoded = decoded[:-1]

    return normalize(decoded), had_invalid
