"""Cálculo offline de estadísticas derivadas.

Las tablas de histogramas (`length_histogram`, `charset_histogram`,
`entropy_histogram`, `pattern_stats`, `token_frequencies`) las puebla
el ETL de ingesta en línea durante la pasada sobre el dataset. Este
módulo calcula las agregaciones derivadas que requieren todas las filas
ya insertadas:

| Tabla                  | Quién la puebla  |
|------------------------|------------------|
| `stats.top_items`      | Este módulo      |
| `stats.global_summary` | Este módulo      |

Idempotente: cada función elimina las filas previas del dataset y
reinserta desde cero, lo que permite relanzar tras reanudaciones
parciales de la ingesta.

Flags:
- `--dataset-name`: requerido. Identifica la fila de `stats.datasets`.
- `--top-n`: número de items a guardar en `stats.top_items` por tipo
  ('substring' y 'pattern'). Default 100.
"""
from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from passeval import config, db


def _logger() -> logging.Logger:
    log = logging.getLogger("etl.stats_compute")
    if not log.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log


# ---------------------------------------------------------------------------
# Lookup de dataset_id
# ---------------------------------------------------------------------------

def lookup_dataset_id(cur, name: str) -> int:
    cur.execute("SELECT id FROM stats.datasets WHERE name = %s", (name,))
    row = cur.fetchone()
    if not row:
        raise ValueError(f"dataset '{name}' no existe en stats.datasets")
    return row[0]


# ---------------------------------------------------------------------------
# top_items: substrings y patterns
# ---------------------------------------------------------------------------

def compute_top_items(cur, dataset_id: int, top_n: int = 100) -> tuple[int, int]:
    """Recalcula `top_items` (substring + pattern). Devuelve `(n_subs, n_patterns)`.

    Lee `stats.token_frequencies` y `stats.pattern_stats`, ambas
    pobladas por el ETL B.3 durante la ingesta.
    """
    cur.execute(
        "DELETE FROM stats.top_items WHERE dataset_id = %s",
        (dataset_id,),
    )
    cur.execute(
        """
        INSERT INTO stats.top_items (dataset_id, item_type, rank, item_repr, count)
        SELECT %s, 'substring',
               ROW_NUMBER() OVER (ORDER BY count DESC, token ASC),
               token,
               count
          FROM stats.token_frequencies
         WHERE dataset_id = %s AND token_type = 'substring'
         ORDER BY count DESC, token ASC
         LIMIT %s
        """,
        (dataset_id, dataset_id, top_n),
    )
    n_subs = cur.rowcount
    cur.execute(
        """
        INSERT INTO stats.top_items (dataset_id, item_type, rank, item_repr, count)
        SELECT %s, 'pattern',
               ROW_NUMBER() OVER (ORDER BY count DESC, pattern_repr ASC),
               (pattern_type || ':' || pattern_repr),
               count
          FROM stats.pattern_stats
         WHERE dataset_id = %s
         ORDER BY count DESC, pattern_repr ASC
         LIMIT %s
        """,
        (dataset_id, dataset_id, top_n),
    )
    n_pat = cur.rowcount
    return n_subs, n_pat


# ---------------------------------------------------------------------------
# global_summary
# ---------------------------------------------------------------------------

# Bitmask de charset (de passeval.strength.charset):
#   1 = lower, 2 = upper, 4 = digit, 8 = symbol, 16 = unicode
_MASK_LOWER = 1
_MASK_UPPER = 2
_MASK_DIGIT = 4
_MASK_SYMBOL = 8
_MASK_UNICODE = 16
_MASK_DIGIT_ONLY = _MASK_DIGIT             # solo el bit de dígito
_MASK_LETTER_BITS = _MASK_LOWER | _MASK_UPPER
_MASK_NON_ALNUM = _MASK_SYMBOL | _MASK_UNICODE


def _length_aggregates_from_histogram(cur, dataset_id: int) -> dict[str, Any]:
    """Calcula sample_size, avg/median/min/max length desde length_histogram.

    Las agregaciones sobre el histograma son exactas para `sample_size`,
    `avg`, `min` y `max`; la mediana se aproxima con interpolación lineal
    dentro del bin (paso 1 carácter, sin error >0,5 en la práctica).
    """
    cur.execute(
        """
        SELECT length, count
          FROM stats.length_histogram
         WHERE dataset_id = %s
         ORDER BY length
        """,
        (dataset_id,),
    )
    rows = [(int(length), int(count)) for length, count in cur.fetchall()]
    if not rows:
        return {"sample_size": 0}
    total = sum(c for _, c in rows)
    if total == 0:
        return {"sample_size": 0}

    avg = sum(length * c for length, c in rows) / total
    min_len = rows[0][0]
    max_len = rows[-1][0]
    # Mediana: bucket que contiene la posición total/2 (paso 1 carácter).
    target = total / 2
    cum = 0
    median = float(min_len)
    for length, c in rows:
        if cum + c >= target:
            median = float(length)
            break
        cum += c
    return {
        "sample_size": total,
        "avg_length": round(avg, 2),
        "median_length": round(median, 2),
        "min_length": min_len,
        "max_length": max_len,
    }


def _charset_aggregates_from_histogram(cur, dataset_id: int,
                                       sample_size: int) -> dict[str, Any]:
    """Calcula los porcentajes de composición desde charset_histogram.

    `charset_histogram` (introducida en A.2 v4) almacena el conteo por
    combinación exacta de bits. Las cuatro métricas de `global_summary`
    son sumas de subconjuntos de combinaciones:

    - `pct_digits_only`:  mask == DIGIT
    - `pct_letters_only`: bit de letra activo y bits de digit/symbol/unicode a 0
    - `pct_alphanumeric`: ningún bit de symbol ni unicode (solo lower/upper/digit)
    - `pct_with_symbols`: bit de symbol activo
    """
    if sample_size == 0:
        return {
            "pct_digits_only": None,
            "pct_letters_only": None,
            "pct_alphanumeric": None,
            "pct_with_symbols": None,
        }
    cur.execute(
        """
        SELECT charset_mask, count
          FROM stats.charset_histogram
         WHERE dataset_id = %s
        """,
        (dataset_id,),
    )
    digits_only = letters_only = alphanumeric = with_symbols = 0
    for mask, count in cur.fetchall():
        m = int(mask)
        c = int(count)
        if m == _MASK_DIGIT_ONLY:
            digits_only += c
        if (m & _MASK_LETTER_BITS) != 0 and (m & (_MASK_DIGIT | _MASK_NON_ALNUM)) == 0:
            letters_only += c
        if (m & _MASK_NON_ALNUM) == 0:
            alphanumeric += c
        if (m & _MASK_SYMBOL) != 0:
            with_symbols += c
    return {
        "pct_digits_only":  round(100.0 * digits_only  / sample_size, 2),
        "pct_letters_only": round(100.0 * letters_only / sample_size, 2),
        "pct_alphanumeric": round(100.0 * alphanumeric / sample_size, 2),
        "pct_with_symbols": round(100.0 * with_symbols / sample_size, 2),
    }


def _entropy_aggregates_from_histogram(cur, dataset_id: int) -> dict[str, Any]:
    """Aprox. de mean/p50/p75/p90 de entropía a partir del histograma 0,5 bits.

    Las medias usan el midpoint del bucket (`bucket_min + 0,25`). Los
    percentiles se aproximan por suma acumulada sobre los buckets
    ordenados — error máximo medio bucket = 0,25 bits.
    """
    cur.execute(
        """
        SELECT entropy_type, bucket_min, count
          FROM stats.entropy_histogram
         WHERE dataset_id = %s
         ORDER BY entropy_type, bucket_min
        """,
        (dataset_id,),
    )
    rows = cur.fetchall()

    by_type: dict[str, list[tuple[float, int]]] = {}
    for etype, bmin, count in rows:
        by_type.setdefault(etype, []).append((float(bmin), int(count)))

    out: dict[str, Any] = {
        "mean_shannon_entropy": None,
        "p50_shannon_entropy": None,
        "p75_shannon_entropy": None,
        "p90_shannon_entropy": None,
        "mean_charset_entropy": None,
    }
    for etype, buckets in by_type.items():
        total = sum(c for _, c in buckets)
        if total == 0:
            continue
        mean = sum((bmin + 0.25) * c for bmin, c in buckets) / total
        if etype == "shannon":
            out["mean_shannon_entropy"] = round(mean, 3)
            out["p50_shannon_entropy"] = round(_percentile_from_buckets(buckets, 0.50), 3)
            out["p75_shannon_entropy"] = round(_percentile_from_buckets(buckets, 0.75), 3)
            out["p90_shannon_entropy"] = round(_percentile_from_buckets(buckets, 0.90), 3)
        elif etype == "charset":
            out["mean_charset_entropy"] = round(mean, 3)
    return out


def _percentile_from_buckets(buckets: list[tuple[float, int]], q: float) -> float:
    """Percentil aproximado por interpolación lineal dentro del bucket que lo contiene."""
    total = sum(c for _, c in buckets)
    target = q * total
    cum = 0
    for bmin, c in buckets:
        if cum + c >= target:
            # interpola dentro del bucket [bmin, bmin+0,5]
            into = (target - cum) / c if c else 0.0
            return bmin + into * 0.5
        cum += c
    return buckets[-1][0] + 0.5 if buckets else 0.0


def compute_global_summary(cur, dataset_id: int) -> None:
    """Genera/actualiza la fila `stats.global_summary` para el dataset.

    Todas las agregaciones provienen de tablas `stats.*`. Si
    `stats.length_histogram` está vacía para el dataset, no se inserta
    nada (no hay base estadística sobre la que generar el resumen).
    """
    base = _length_aggregates_from_histogram(cur, dataset_id)
    sample_size = int(base.get("sample_size") or 0)
    if sample_size == 0:
        return
    charset = _charset_aggregates_from_histogram(cur, dataset_id, sample_size)
    ent = _entropy_aggregates_from_histogram(cur, dataset_id)

    cur.execute("DELETE FROM stats.global_summary WHERE dataset_id = %s", (dataset_id,))
    cur.execute(
        """
        INSERT INTO stats.global_summary (
            dataset_id, sample_size, avg_length, median_length, min_length, max_length,
            mean_shannon_entropy, p50_shannon_entropy, p75_shannon_entropy, p90_shannon_entropy,
            mean_charset_entropy,
            pct_digits_only, pct_letters_only, pct_alphanumeric, pct_with_symbols
        ) VALUES (
            %(dataset_id)s, %(sample_size)s, %(avg_length)s, %(median_length)s,
            %(min_length)s, %(max_length)s,
            %(mean_shannon)s, %(p50_shannon)s, %(p75_shannon)s, %(p90_shannon)s,
            %(mean_charset)s,
            %(pct_digits_only)s, %(pct_letters_only)s, %(pct_alphanumeric)s, %(pct_with_symbols)s
        )
        """,
        {
            "dataset_id": dataset_id,
            "sample_size": sample_size,
            "avg_length": base.get("avg_length"),
            "median_length": base.get("median_length"),
            "min_length": base.get("min_length"),
            "max_length": base.get("max_length"),
            "mean_shannon": ent["mean_shannon_entropy"],
            "p50_shannon": ent["p50_shannon_entropy"],
            "p75_shannon": ent["p75_shannon_entropy"],
            "p90_shannon": ent["p90_shannon_entropy"],
            "mean_charset": ent["mean_charset_entropy"],
            "pct_digits_only": charset["pct_digits_only"],
            "pct_letters_only": charset["pct_letters_only"],
            "pct_alphanumeric": charset["pct_alphanumeric"],
            "pct_with_symbols": charset["pct_with_symbols"],
        },
    )


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="etl.stats_compute",
        description="Cálculo offline de top_items y global_summary "
                    "derivado de las tablas stats.*.",
    )
    p.add_argument("--dataset-name", required=True)
    p.add_argument("--top-n", type=int, default=100,
                   help="Items por tipo en stats.top_items (default 100).")
    return p.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    log = _logger()
    config.load_config()  # valida .env temprano
    with db.connection_scope() as conn:
        with conn.cursor() as cur:
            dataset_id = lookup_dataset_id(cur, args.dataset_name)
            log.info("dataset '%s' -> id=%d", args.dataset_name, dataset_id)
            n_subs, n_pat = compute_top_items(cur, dataset_id, args.top_n)
            log.info("top_items: %d substrings + %d patterns", n_subs, n_pat)
            compute_global_summary(cur, dataset_id)
            log.info("global_summary: actualizado")
        conn.commit()
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
