"""Detector de palabras de diccionario.

Carga uno o varios ficheros `.txt` desde un directorio (por defecto
`data/dictionaries/`) y busca todas las subcadenas (case-insensitive)
de longitud >= 2 que coincidan con alguna entrada.

Formato de los ficheros:
- UTF-8, una palabra por línea.
- Líneas que empiezan por `#` son comentarios (cabecera con
  `# fuente: ..., fecha: ...`).
- El orden de las líneas se interpreta como ranking (la primera línea
  tiene rank 0). El rank se expone en `metadata` para que el modelo
  de scoring le asigne coste proporcional al rank.

Para tests se puede inyectar el diccionario directamente vía
`dictionaries` evitando IO.
"""
from __future__ import annotations

from pathlib import Path

from passeval.strength.patterns.match import Match

# Ruta absoluta derivada de la ubicación del paquete; funciona
# independientemente del directorio de trabajo actual.
DEFAULT_DICTIONARY_DIR = Path(__file__).resolve().parents[4] / "data" / "dictionaries"
# Longitud mínima de substring a considerar match. Fijada a 3 para alinearse
# con `spanish`, `leet` y `DICT_MIN_LEN` del acumulador del ETL. Substrings
# de 2 caracteres son ruido para la descomposición (cualquier contraseña
# contiene 'an', 'es', 'la', etc.); la pérdida de cobertura sobre las 269
# entradas de longitud 2 de los lemarios (apellidos chinos transliterados,
# contraseñas triviales de 2 chars) es marginal (~0,087% del corpus léxico).
MIN_MATCH_LEN = 3

_cache: dict[Path, dict[str, dict[str, int]]] = {}


def load_dictionaries(directory: Path | str = DEFAULT_DICTIONARY_DIR) -> dict[str, dict[str, int]]:
    """Carga todos los `.txt` del directorio.

    Devuelve un dict `{nombre_diccionario: {palabra: rank}}`. El nombre
    de cada diccionario es el stem del fichero. Cachea por path para
    que cargas repetidas en una misma sesión sean baratas.
    """
    directory = Path(directory).resolve()
    if directory in _cache:
        return _cache[directory]
    result: dict[str, dict[str, int]] = {}
    if directory.exists():
        for path in sorted(directory.glob("*.txt")):
            ranked: dict[str, int] = {}
            with path.open("r", encoding="utf-8") as f:
                rank = 0
                for line in f:
                    word = line.strip()
                    if not word or word.startswith("#"):
                        continue
                    word_lower = word.lower()
                    if word_lower not in ranked:
                        ranked[word_lower] = rank
                        rank += 1
            result[path.stem] = ranked
    _cache[directory] = result
    return result


def clear_cache() -> None:
    """Limpia la caché interna (útil en tests)."""
    _cache.clear()


def match_dictionary(
    s: str,
    dictionaries: dict[str, dict[str, int]] | None = None,
) -> list[Match]:
    """Devuelve todos los matches de subcadena contra los diccionarios."""
    if dictionaries is None:
        dictionaries = load_dictionaries()
    matches: list[Match] = []
    if not s:
        return matches
    s_lower = s.lower()
    n = len(s)
    for start in range(n):
        for end in range(start + MIN_MATCH_LEN, n + 1):
            substr = s_lower[start:end]
            for dict_name, ranked in dictionaries.items():
                rank = ranked.get(substr)
                if rank is not None:
                    # guesses = max(rank, 1): el rank 0 (palabra más común)
                    # se cuenta como 1 intento, no como 0.
                    matches.append(
                        Match(
                            type="dictionary",
                            start=start,
                            end=end,
                            token=s[start:end],
                            guesses=max(rank, 1),
                            metadata={
                                "dict_name": dict_name,
                                "rank": rank,
                                "matched_word": substr,
                            },
                        )
                    )
    return matches


detect = match_dictionary
