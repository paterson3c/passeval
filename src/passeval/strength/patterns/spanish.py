"""Detector de términos específicos del castellano.

Combina dos fuentes:

1. Diccionarios cuyo `stem` termina en `_es` (cargados por
   `dictionary.load_dictionaries()`): nombres, apellidos, palabras
   genéricas, topónimos.
2. Lista inline de "términos culturales" — referencias frecuentes en
   contraseñas españolas que no necesariamente aparecen en los lemarios
   estándar (clubes deportivos, expresiones afectivas comunes, fechas
   significativas escritas en texto).

El detector emite un `Match` por cada hit; la deduplicación frente a
otros detectores (p. ej. `dictionary` que carga los mismos ficheros)
se hace en el modelo de scoring.
"""
from __future__ import annotations

from passeval.strength.patterns.dictionary import load_dictionaries
from passeval.strength.patterns.match import Match

CULTURAL_TERMS: tuple[str, ...] = (
    # Equipos de fútbol y referencias deportivas.
    "realmadrid", "barca", "barça", "atletico", "athletic", "betis",
    "valencia", "sevilla", "villarreal", "espanyol", "celta", "rayo",
    # Ciudades grandes y comunidades.
    "madrid", "barcelona", "sevilla", "valencia", "zaragoza", "malaga",
    "bilbao", "granada", "murcia", "alicante", "cordoba", "vigo",
    "andalucia", "cataluna", "cataluña", "galicia", "euskadi",
    # Expresiones afectivas y palabras frecuentes en passwords ES.
    "tequiero", "miamor", "amor", "cariño", "carino", "vida", "casa",
    "familia", "amigos", "feliz", "navidad", "verano", "invierno",
    # Genéricos y "fillers" típicos.
    "españa", "espana", "hola", "futbol", "campeon", "campeón",
)

MIN_MATCH_LEN = 3


def _es_dictionaries(
    dictionaries: dict[str, dict[str, int]] | None,
) -> dict[str, dict[str, int]]:
    if dictionaries is not None:
        return dictionaries
    all_dicts = load_dictionaries()
    return {k: v for k, v in all_dicts.items() if k.endswith("_es")}


def detect(
    s: str,
    dictionaries: dict[str, dict[str, int]] | None = None,
) -> list[Match]:
    """Devuelve matches contra diccionarios `_es` y términos culturales."""
    matches: list[Match] = []
    if not s:
        return matches
    s_lower = s.lower()
    n = len(s)

    cultural_rank = {term: rank for rank, term in enumerate(CULTURAL_TERMS)}
    es_dicts = _es_dictionaries(dictionaries)

    for start in range(n):
        for end in range(start + MIN_MATCH_LEN, n + 1):
            substr = s_lower[start:end]
            if substr in cultural_rank:
                rank = cultural_rank[substr]
                matches.append(
                    Match(
                        type="spanish",
                        start=start,
                        end=end,
                        token=s[start:end],
                        guesses=max(rank, 1),
                        metadata={"source": "cultural", "rank": rank},
                    )
                )
            for dict_name, ranked in es_dicts.items():
                rank = ranked.get(substr)
                if rank is not None:
                    matches.append(
                        Match(
                            type="spanish",
                            start=start,
                            end=end,
                            token=s[start:end],
                            guesses=max(rank, 1),
                            metadata={"source": dict_name, "rank": rank},
                        )
                    )
    return matches
