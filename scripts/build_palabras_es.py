"""Genera `data/dictionaries/palabras_es.txt`.

Combina dos fuentes públicas para obtener simultáneamente:
- Cobertura léxica amplia del español (lemario de >100k palabras).
- Ordenación por frecuencia real de uso (necesaria por la fórmula de
  coste `guesses = rank` del módulo `dictionary.py`, ver §4.6.3 del
  informe v3).

Fuentes:

1. JorgeDuenasLerin/diccionario-espanol-txt
   https://github.com/JorgeDuenasLerin/diccionario-espanol-txt
   Licencia: dominio público / GPL-compatible (ver repo).
   Aporta el conjunto de palabras válidas (membresía). Lista
   alfabética, exhaustiva, con conjugaciones y formas derivadas.

2. hermitdave/FrequencyWords (OpenSubtitles2018)
   https://github.com/hermitdave/FrequencyWords
   Licencia: CC-BY-SA 4.0.
   Aporta el orden por frecuencia: cuántas veces aparece cada palabra
   en el corpus de subtítulos en español de OpenSubtitles2018.

Algoritmo:
- Se cargan ambas fuentes.
- Para cada lema del lemario, se busca su frecuencia en es_50k.
  - Si aparece -> se le asigna esa frecuencia.
  - Si no aparece -> frecuencia 0 (palabra rara, ranking final).
- Se filtran tokens: solo letras (incluye acentos y ñ), longitud >= 3.
- Se ordena por (frecuencia desc, alfabético asc).
- Se escribe el fichero con cabecera de procedencia, fecha, licencia
  y contadores de cobertura.

Justificación del diseño (v3 §4.6.3):
- Sólo el lemario aporta cobertura, pero su orden alfabético rompe
  `guesses = rank` (palabras raras saldrían "fáciles", comunes "difíciles").
- Sólo OpenSubtitles aporta frecuencia, pero pierde vocabulario raro
  o literario que sí aparece en contraseñas (p. ej. "abacero").
- La combinación da cobertura del lemario y orden de OpenSubtitles.

Ejecución:
    python3 scripts/build_palabras_es.py
"""
from __future__ import annotations

import unicodedata
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "dictionaries" / "palabras_es.txt"
DATE = "2026-04-24"

LEMARIO_URL = (
    "https://raw.githubusercontent.com/JorgeDuenasLerin/"
    "diccionario-espanol-txt/master/data/archive/2024-05-22/"
    "0_palabras_todas_no_conjugaciones.txt"
)
FREQ_URL = (
    "https://raw.githubusercontent.com/hermitdave/FrequencyWords/"
    "master/content/2018/es/es_50k.txt"
)


def fetch(url: str) -> str:
    print(f"GET {url}")
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode("utf-8")


def norm(s: str) -> str:
    return unicodedata.normalize("NFC", s).strip().lower()


def is_word(s: str) -> bool:
    return len(s) >= 3 and all(c.isalpha() for c in s)


def main() -> None:
    lemario_raw = fetch(LEMARIO_URL).splitlines()
    freq_raw = fetch(FREQ_URL).splitlines()

    # Frecuencias de OpenSubtitles2018: {palabra: ocurrencias}.
    freq: dict[str, int] = {}
    for line in freq_raw:
        parts = line.split()
        if len(parts) == 2:
            freq[norm(parts[0])] = int(parts[1])

    # Lemario: conjunto de palabras válidas, normalizadas y filtradas.
    lemmas: set[str] = set()
    for raw in lemario_raw:
        t = norm(raw)
        if is_word(t):
            lemmas.add(t)

    # Asigna frecuencia a cada lema (0 si no está en el corpus de subtítulos).
    pairs = [(w, freq.get(w, 0)) for w in lemmas]

    # Ordena por (frecuencia desc, alfabético asc).
    pairs.sort(key=lambda x: (-x[1], x[0]))

    in_corpus = sum(1 for _, f in pairs if f > 0)
    out_corpus = len(pairs) - in_corpus

    header = [
        "# fuente: combinación de dos repositorios públicos",
        "#   - JorgeDuenasLerin/diccionario-espanol-txt (lemario, membresía)",
        "#     https://github.com/JorgeDuenasLerin/diccionario-espanol-txt",
        "#   - hermitdave/FrequencyWords (OpenSubtitles2018, frecuencias)",
        "#     https://github.com/hermitdave/FrequencyWords",
        "# corpus de frecuencia: OpenSubtitles2018 (subtítulos en español)",
        "# licencias: lemario - GPL-compatible; FrequencyWords - CC-BY-SA 4.0",
        f"# fecha: {DATE}",
        "# generado por scripts/build_palabras_es.py",
        "#",
        "# Estructura:",
        f"#   - {len(pairs)} entradas en total",
        f"#   - {in_corpus} lemas con frecuencia conocida (van primero, ordenados desc)",
        f"#   - {out_corpus} lemas no presentes en el corpus de subtítulos",
        "#     (van después, ordenados alfabéticamente; tratados como 'palabras raras')",
        "#",
        "# Filtros aplicados: NFC + minúsculas + solo letras (incluye ñ y acentos) + longitud >= 3",
        "#",
        "# Justificación del orden (ver §4.6.3 del informe v3):",
        "# La fórmula de coste del módulo dictionary.py es guesses = rank, donde rank",
        "# es la posición de la palabra en este fichero (0 = la más común). Para que",
        "# esto refleje fortaleza real, las palabras de uso frecuente deben ir primero.",
        "# Por eso se reordena el lemario alfabético usando frecuencias empíricas de",
        "# OpenSubtitles2018 como criterio principal.",
    ]

    with OUTPUT.open("w", encoding="utf-8") as f:
        for line in header:
            f.write(line + "\n")
        for word, _ in pairs:
            f.write(word + "\n")

    print(f"escrito {OUTPUT} ({len(pairs)} entradas, {in_corpus} con frecuencia)")


if __name__ == "__main__":
    main()
