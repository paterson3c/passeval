"""Detector de sustituciones leet ("leet speak").

Trabaja en tándem con `dictionary.py` y `spanish.py`: genera la forma
"deleetificada" de cada subcadena de la contraseña que contenga al menos
un carácter leet y la busca en los diccionarios cargados. Si hay match,
se reporta un `Match` de tipo `leet` cuyo coste sigue el modelo Wheeler
2016:

    guesses_leet = rank_dict × 2 ** substitutions

Esta fórmula refleja que un atacante moderno (hashcat, john) aplica
reglas leet automáticamente, multiplicando el trabajo por un factor
modesto (del orden de decenas). La conclusión didáctica es que las
sustituciones leet apenas añaden resistencia frente al ataque de
diccionario + reglas.

Limitaciones documentadas:
- El mapa `LEET_MAP` es 1->1: cada carácter leet mapea a una sola letra.
  Casos ambiguos reales (`1` puede ser "i" o "l") no se enumeran.
- No se normaliza mayúsculas/minúsculas más allá del `.lower()` inicial.
  El diccionario debe estar en minúsculas.
- Subcadenas cuya deleetificación resulte en menos de 3 letras se
  descartan para evitar ruido (ej. "333" -> "eee" se ignora).
"""
from __future__ import annotations

from passeval.strength.patterns.dictionary import load_dictionaries
from passeval.strength.patterns.match import Match

LEET_MAP: dict[str, str] = {
    "4": "a",
    "@": "a",
    "3": "e",
    "1": "i",
    "!": "i",
    "|": "i",
    "0": "o",
    "5": "s",
    "$": "s",
    "7": "t",
    "8": "b",
}

MIN_MATCH_LEN = 3

# Prioridad de diccionarios cuando una palabra aparece en varios.
# Los ranks absolutos no son comparables entre diccionarios de tamaños
# distintos, así que forzamos un orden explícito.
DICT_PRIORITY = (
    "palabras_es",   # preferimos palabra común sobre nombre propio
    "nombres_es",
    "apellidos_es",
    "toponimos_es",
)


def deleet(s: str) -> str:
    """Sustituye los caracteres leet por su letra equivalente.

    La cadena se pasa a minúsculas antes de aplicar el mapa.
    """
    return "".join(LEET_MAP.get(c, c) for c in s.lower())


def count_substitutions(substr: str) -> int:
    """Número de caracteres leet en la subcadena original."""
    return sum(1 for c in substr.lower() if c in LEET_MAP)


def _lookup_in_dictionaries(
    deleeted: str,
    dictionaries: dict[str, dict[str, int]],
) -> tuple[str, int] | None:
    """Busca una palabra en los diccionarios respetando DICT_PRIORITY.

    Devuelve (nombre_diccionario, rank) del primer match según prioridad,
    o None si no hay match en ninguno.
    """
    # Primero los diccionarios en orden de prioridad
    for dict_name in DICT_PRIORITY:
        ranked = dictionaries.get(dict_name)
        if ranked is not None:
            rank = ranked.get(deleeted)
            if rank is not None:
                return dict_name, rank
    # Después cualquier otro diccionario no listado en la prioridad
    for dict_name, ranked in dictionaries.items():
        if dict_name in DICT_PRIORITY:
            continue
        rank = ranked.get(deleeted)
        if rank is not None:
            return dict_name, rank
    return None


def detect(
    s: str,
    dictionaries: dict[str, dict[str, int]] | None = None,
) -> list[Match]:
    """Devuelve matches para subcadenas leet cuya forma deleeted está en diccionario.

    Cada match incluye el campo `guesses` calculado como:
        guesses = rank × 2 ** substitutions

    donde `rank` es la posición de la palabra en el diccionario
    correspondiente y `substitutions` es el número de caracteres leet
    en la subcadena original.
    """
    if dictionaries is None:
        dictionaries = load_dictionaries()
    matches: list[Match] = []
    if not s or not dictionaries:
        return matches

    s_lower = s.lower()
    n = len(s)

    for start in range(n):
        for end in range(start + MIN_MATCH_LEN, n + 1):
            substr = s_lower[start:end]

            # Debe contener al menos un carácter leet; si no, es un
            # match de diccionario puro y lo captura `dictionary.py`.
            subs = count_substitutions(substr)
            if subs == 0:
                continue

            deleeted = deleet(substr)

            # La palabra deleeted debe tener al menos MIN_MATCH_LEN
            # letras reales (filtra casos como "333" -> "eee").
            if len(deleeted) < MIN_MATCH_LEN:
                continue

            lookup = _lookup_in_dictionaries(deleeted, dictionaries)
            if lookup is None:
                continue

            dict_name, rank = lookup
            guesses = rank * (2 ** subs)

            matches.append(
                Match(
                    type="leet",
                    start=start,
                    end=end,
                    token=s[start:end],
                    guesses=guesses,
                    metadata={
                        "deleeted": deleeted,
                        "dict_name": dict_name,
                        "rank": rank,
                        "substitutions": subs,
                    },
                )
            )

    return matches