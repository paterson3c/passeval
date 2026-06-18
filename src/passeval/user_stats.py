"""Registro anónimo opt-in de estadísticas de contraseñas evaluadas por el CLI.

Qué se recoge (derivado de la contraseña, nunca la contraseña en sí):
  - Longitud (bucket en length_histogram)
  - Composición de caracteres (bitmask en charset_histogram)
  - Entropía Shannon total y de charset (bucket 0,5 bits en entropy_histogram)
  - Sumas acumuladas en dataset_totals (total_lines, sum_length, sum_shannon,
    sum_charset_bits) para calcular medias globales.

Qué NO se recoge:
  - La contraseña en claro ni ningún hash de ella.
  - IP, usuario, sesión u otro identificador.

El registro falla silenciosamente si la BD no está disponible para que el
CLI nunca se vea afectado por problemas de conectividad.
"""
from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)


def record_query(password: str, dataset_name: str) -> None:
    """Registra estadísticas anónimas de una contraseña evaluada por el usuario.

    Abre una conexión propia a la BD, hace los upserts en una transacción
    y la cierra. Si algo falla (BD caída, dataset no encontrado, etc.)
    loguea a DEBUG y continúa sin propagar la excepción.
    """
    if not password:
        return
    try:
        _do_record(password, dataset_name)
    except Exception as exc:  # noqa: BLE001
        logger.debug("user_stats: error al registrar (ignorado): %s", exc)


def _do_record(password: str, dataset_name: str) -> None:
    from passeval import db
    from passeval.strength.charset import charset_mask, charset_size_from_mask
    from passeval.strength.shannon import shannon_entropy
    from etl._stats_accumulators import entropy_bucket

    n = len(password)
    mask = charset_mask(password)
    n_alpha = charset_size_from_mask(mask)
    sh_total = shannon_entropy(password) * n
    ch_total = n * math.log2(n_alpha) if n_alpha > 0 else 0.0
    sh_bucket = entropy_bucket(sh_total)
    ch_bucket = entropy_bucket(ch_total)

    conn = db.get_connection()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM stats.datasets WHERE name = %s",
                (dataset_name,),
            )
            row = cur.fetchone()
            if row is None:
                logger.debug("user_stats: dataset '%s' no encontrado en BD", dataset_name)
                return
            dataset_id = row[0]

            cur.execute(
                """
                INSERT INTO stats.length_histogram (dataset_id, length, count)
                VALUES (%s, %s, 1)
                ON CONFLICT (dataset_id, length)
                DO UPDATE SET count = stats.length_histogram.count + 1
                """,
                (dataset_id, n),
            )
            cur.execute(
                """
                INSERT INTO stats.charset_histogram (dataset_id, charset_mask, count)
                VALUES (%s, %s, 1)
                ON CONFLICT (dataset_id, charset_mask)
                DO UPDATE SET count = stats.charset_histogram.count + 1
                """,
                (dataset_id, mask),
            )
            cur.executemany(
                """
                INSERT INTO stats.entropy_histogram (dataset_id, entropy_type, bucket_min, count)
                VALUES (%s, %s, %s, 1)
                ON CONFLICT (dataset_id, entropy_type, bucket_min)
                DO UPDATE SET count = stats.entropy_histogram.count + 1
                """,
                [
                    (dataset_id, "shannon", sh_bucket),
                    (dataset_id, "charset", ch_bucket),
                ],
            )
            cur.execute(
                """
                UPDATE stats.dataset_totals
                   SET total_lines      = total_lines + 1,
                       sum_length       = sum_length + %s,
                       sum_shannon      = sum_shannon + %s,
                       sum_charset_bits = sum_charset_bits + %s,
                       updated_at       = now()
                 WHERE dataset_id = %s
                """,
                (n, sh_total, ch_total, dataset_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
