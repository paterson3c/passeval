"""ETL de ingesta reanudable — Passeval.

Flujo:
1. Registrar ingestion_run + asegurar dataset en stats.datasets y dataset_totals.
2. Listar ficheros; saltar los ya ``completed`` en file_progress.
3. Por cada fichero (serial o worker paralelo):
   - Marcar file_progress -> running.
   - Bucle por líneas: normalizar -> actualizar acc en RAM.
   - Cada ``batch_size`` líneas válidas: FLUSH ATÓMICO (una transacción:
     histogramas + dataset_totals + file_progress) y reiniciar acc.
   - Al terminar el fichero: flush final + marcar file_progress -> completed.
4. Cerrar ingestion_run.

Modos (--mode):
- light:  solo length_histogram + charset_histogram.          ~3 µs/contraseña.
- heavy:  además entropy, patterns, tokens.                  ~25 µs/contraseña. (default)
- full:   heavy + score_histogram + sum_guesses.             ~35 µs/contraseña.

Paralelismo (--workers N):
- N=1: serial (default).
- N>1: N workers en procesos separados, cada uno con su propia conexión PG
  y flush directo por batch. El coordinador solo recoge los totales.
"""
from __future__ import annotations

import argparse
import logging
import multiprocessing as mp
import queue
import random
import signal
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

import psycopg2

from etl._stats_accumulators import (
    LightStatsAccumulator,
    StatsAccumulator,
    update_light_stats,
    update_stats,
)
from passeval import config, db
from passeval.normalize import decode_line
from passeval.strength import model as strength_model
from passeval.strength.patterns import dictionary as pat_dict

MODES = ("light", "heavy", "full")
DEFAULT_MODE = "heavy"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(log_dir: Path, level: str = "INFO") -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"ingest_{ts}.log"

    logger = logging.getLogger("etl.ingest")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.propagate = False
    logger.info("logging inicializado en %s", log_file)
    return logger


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="etl.ingest",
        description="ETL reanudable de ingesta de contraseñas a stats.* (Passeval final/clean).",
    )
    p.add_argument("--dataset-path", type=Path,
                   help="Directorio o fichero con contraseñas. Obligatorio salvo con --reset-only.")
    p.add_argument("--dataset-name",
                   help="Nombre lógico del dataset. Obligatorio salvo con --reset-only.")
    p.add_argument("--mode", choices=MODES, default=DEFAULT_MODE,
                   help=("Profundidad del análisis (default %(default)s). "
                         "light=solo length+charset; heavy=además entropy+patterns+tokens; "
                         "full=heavy+score+guesses."))
    p.add_argument("--limit", type=int, default=None,
                   help="Tope de líneas válidas. Solo soportado con --workers 1.")
    p.add_argument("--dry-run", action="store_true",
                   help="No toca BBDD; reporta lo que haría.")
    p.add_argument("--reset", action="store_true",
                   help="TRUNCATE stats.* antes de iniciar ingesta.")
    p.add_argument("--reset-only", action="store_true",
                   help="TRUNCATE stats.* y termina sin iniciar ingesta.")
    p.add_argument("--workers", type=int, default=1,
                   help="Número de procesos paralelos (1 = serial; default 1).")

    args = p.parse_args(argv)

    if args.workers < 1:
        p.error("--workers debe ser >= 1")
    if args.limit is not None and args.limit < 1:
        p.error("--limit debe ser >= 1")
    if args.reset_only and args.reset:
        p.error("usa --reset-only o --reset, no ambos")
    if args.workers > 1 and args.limit is not None:
        p.error("--limit no está soportado en modo paralelo; usa --workers 1")
    if not args.reset_only:
        if args.dataset_path is None:
            p.error("--dataset-path es obligatorio salvo con --reset-only")
        if args.dataset_name is None:
            p.error("--dataset-name es obligatorio salvo con --reset-only")
    return args


# ---------------------------------------------------------------------------
# Iteración de ficheros del dataset
# ---------------------------------------------------------------------------

def iter_dataset_files(path: Path) -> Iterator[Path]:
    """Devuelve ficheros del dataset en orden estable."""
    if path.is_file():
        yield path
        return
    for p in sorted(path.rglob("*")):
        if p.is_file():
            yield p


# ---------------------------------------------------------------------------
# Operaciones SQL
# ---------------------------------------------------------------------------

def ensure_dataset(cur, name: str) -> int:
    """Devuelve el id de stats.datasets; lo crea si no existe. Garantiza dataset_totals."""
    cur.execute("SELECT id FROM stats.datasets WHERE name = %s", (name,))
    row = cur.fetchone()
    if row:
        dataset_id = row[0]
    else:
        cur.execute(
            "INSERT INTO stats.datasets (name, source) VALUES (%s, %s) RETURNING id",
            (name, "ETL ingest"),
        )
        dataset_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO stats.dataset_totals (dataset_id) VALUES (%s) ON CONFLICT DO NOTHING",
        (dataset_id,),
    )
    return dataset_id


def open_run(cur, dataset_id: int) -> int:
    cur.execute(
        "INSERT INTO stats.ingestion_runs (dataset_id, status) VALUES (%s, 'running') RETURNING id",
        (dataset_id,),
    )
    return cur.fetchone()[0]


def close_run(cur, run_id: int, status: str, totals: dict, error: str | None = None) -> None:
    cur.execute(
        """
        UPDATE stats.ingestion_runs
           SET finished_at = now(),
               status = %s,
               files_processed = %s,
               lines_read = %s,
               lines_valid = %s,
               lines_invalid = %s,
               error_message = %s
         WHERE id = %s
        """,
        (
            status,
            totals.get("files", 0),
            totals.get("lines_read", 0),
            totals.get("lines_valid", 0),
            totals.get("lines_invalid", 0),
            error,
            run_id,
        ),
    )


def file_already_completed(cur, dataset_id: int, filename: str) -> bool:
    cur.execute(
        "SELECT status FROM stats.file_progress WHERE dataset_id = %s AND filename = %s",
        (dataset_id, filename),
    )
    row = cur.fetchone()
    return bool(row and row[0] == "completed")


def mark_file_running(cur, dataset_id: int, filename: str) -> None:
    cur.execute(
        """
        INSERT INTO stats.file_progress (dataset_id, filename, status, started_at)
        VALUES (%s, %s, 'running', now())
        ON CONFLICT (dataset_id, filename) DO UPDATE
            SET status = 'running',
                started_at = COALESCE(stats.file_progress.started_at, EXCLUDED.started_at),
                error_message = NULL
        """,
        (dataset_id, filename),
    )


def mark_file_failed(cur, dataset_id: int, filename: str, error: str) -> None:
    cur.execute(
        """
        UPDATE stats.file_progress
           SET status = 'failed', finished_at = now(), error_message = %s
         WHERE dataset_id = %s AND filename = %s
        """,
        (error, dataset_id, filename),
    )


def reset_all(cur, logger: logging.Logger) -> None:
    logger.warning("reset solicitado: truncando stats.*")
    cur.execute(
        "TRUNCATE stats.length_histogram, stats.charset_histogram, "
        "stats.entropy_histogram, stats.pattern_stats, "
        "stats.token_frequencies, stats.score_histogram, "
        "stats.dataset_totals CASCADE"
    )
    cur.execute("DELETE FROM stats.file_progress")
    cur.execute("DELETE FROM stats.ingestion_runs")
    cur.execute("DELETE FROM stats.datasets")
    cur.execute(
        """
        INSERT INTO stats.datasets (name, dataset_type, description)
        VALUES ('user_queries', 'user_submitted',
                'Estadísticas anónimas de contraseñas evaluadas por usuarios (opt-in). '
                'Nunca se almacena la contraseña ni ningún hash de ella.')
        ON CONFLICT (name) DO NOTHING
        """
    )
    cur.execute(
        """
        INSERT INTO stats.dataset_totals (dataset_id)
        SELECT id FROM stats.datasets WHERE name = 'user_queries'
        ON CONFLICT DO NOTHING
        """
    )
    logger.info("seed: fila user_queries restaurada")


# ---------------------------------------------------------------------------
# Flush atómico a PG
# ---------------------------------------------------------------------------

def _flush_histograms(
    cur,
    dataset_id: int,
    acc: StatsAccumulator | LightStatsAccumulator,
) -> None:
    """Inserta/suma los contadores del acc en las tablas de histograma.

    Las filas se ordenan por clave antes de executemany para que todos los
    workers upsertean en el mismo orden y se evitan deadlocks circulares.
    """
    if acc.length_hist:
        cur.executemany(
            """
            INSERT INTO stats.length_histogram (dataset_id, length, count)
            VALUES (%s, %s, %s)
            ON CONFLICT (dataset_id, length) DO UPDATE
                SET count = stats.length_histogram.count + EXCLUDED.count
            """,
            [(dataset_id, length, c) for length, c in sorted(acc.length_hist.items())],
        )

    if acc.charset_hist:
        cur.executemany(
            """
            INSERT INTO stats.charset_histogram (dataset_id, charset_mask, count)
            VALUES (%s, %s, %s)
            ON CONFLICT (dataset_id, charset_mask) DO UPDATE
                SET count = stats.charset_histogram.count + EXCLUDED.count
            """,
            [(dataset_id, mask, c) for mask, c in sorted(acc.charset_hist.items())],
        )

    entropy_hist = getattr(acc, "entropy_hist", None)
    if entropy_hist:
        cur.executemany(
            """
            INSERT INTO stats.entropy_histogram (dataset_id, entropy_type, bucket_min, count)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (dataset_id, entropy_type, bucket_min) DO UPDATE
                SET count = stats.entropy_histogram.count + EXCLUDED.count
            """,
            [(dataset_id, etype, bmin, c)
             for (etype, bmin), c in sorted(entropy_hist.items())],
        )

    pattern_stats = getattr(acc, "pattern_stats", None)
    if pattern_stats:
        cur.executemany(
            """
            INSERT INTO stats.pattern_stats (dataset_id, pattern_type, pattern_repr, count)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (dataset_id, pattern_type, pattern_repr) DO UPDATE
                SET count = stats.pattern_stats.count + EXCLUDED.count
            """,
            [(dataset_id, ptype, prepr[:255], c)
             for (ptype, prepr), c in sorted(pattern_stats.items())],
        )

    token_freqs = getattr(acc, "token_freqs", None)
    if token_freqs:
        cur.executemany(
            """
            INSERT INTO stats.token_frequencies (dataset_id, token_type, token, count)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (dataset_id, token_type, token) DO UPDATE
                SET count = stats.token_frequencies.count + EXCLUDED.count
            """,
            [(dataset_id, ttype, tok[:64], c)
             for (ttype, tok), c in sorted(token_freqs.items())],
        )

    score_hist = getattr(acc, "score_hist", None)
    if score_hist:
        cur.executemany(
            """
            INSERT INTO stats.score_histogram (dataset_id, score, count)
            VALUES (%s, %s, %s)
            ON CONFLICT (dataset_id, score) DO UPDATE
                SET count = stats.score_histogram.count + EXCLUDED.count
            """,
            [(dataset_id, s, c) for s, c in sorted(score_hist.items())],
        )


def _update_dataset_totals(
    cur,
    dataset_id: int,
    acc: StatsAccumulator | LightStatsAccumulator,
    mode: str,
) -> None:
    total_lines = sum(acc.length_hist.values())
    if total_lines == 0:
        return
    sum_length = acc.sum_length
    sum_shannon = getattr(acc, "sum_shannon", 0.0)
    sum_charset_bits = getattr(acc, "sum_charset_bits", 0.0)
    sum_guesses = getattr(acc, "sum_guesses", None) if mode == "full" else None

    if sum_guesses is not None:
        cur.execute(
            """
            UPDATE stats.dataset_totals
               SET total_lines = total_lines + %s,
                   sum_length = sum_length + %s,
                   sum_shannon = sum_shannon + %s,
                   sum_charset_bits = sum_charset_bits + %s,
                   sum_guesses = COALESCE(sum_guesses, 0) + %s,
                   updated_at = now()
             WHERE dataset_id = %s
            """,
            (total_lines, sum_length, sum_shannon, sum_charset_bits, sum_guesses, dataset_id),
        )
    else:
        cur.execute(
            """
            UPDATE stats.dataset_totals
               SET total_lines = total_lines + %s,
                   sum_length = sum_length + %s,
                   sum_shannon = sum_shannon + %s,
                   sum_charset_bits = sum_charset_bits + %s,
                   updated_at = now()
             WHERE dataset_id = %s
            """,
            (total_lines, sum_length, sum_shannon, sum_charset_bits, dataset_id),
        )


def flush_batch(
    cur,
    conn,
    dataset_id: int,
    acc: StatsAccumulator | LightStatsAccumulator,
    mode: str,
    filename: str,
    lines_read: int,
    lines_valid: int,
    lines_invalid: int,
    is_final: bool,
    aborted: bool = False,
    _max_retries: int = 5,
) -> None:
    """Vuelca acc a stats.* y actualiza file_progress en una transacción.

    Reintenta hasta _max_retries veces en caso de deadlock (con backoff
    exponencial + jitter). Las filas se ordenan en _flush_histograms para
    minimizar la probabilidad de deadlock circular entre workers.
    """
    for attempt in range(_max_retries):
        try:
            _flush_histograms(cur, dataset_id, acc)
            _update_dataset_totals(cur, dataset_id, acc, mode)
            if aborted:
                cur.execute(
                    """
                    UPDATE stats.file_progress
                       SET status = 'failed', finished_at = now(),
                           error_message = 'interrupted',
                           lines_processed = %s, lines_valid = %s, lines_invalid = %s
                     WHERE dataset_id = %s AND filename = %s
                    """,
                    (lines_read, lines_valid, lines_invalid, dataset_id, filename),
                )
            elif is_final:
                cur.execute(
                    """
                    UPDATE stats.file_progress
                       SET status = 'completed', finished_at = now(),
                           lines_processed = %s, lines_valid = %s, lines_invalid = %s
                     WHERE dataset_id = %s AND filename = %s
                    """,
                    (lines_read, lines_valid, lines_invalid, dataset_id, filename),
                )
            else:
                cur.execute(
                    """
                    UPDATE stats.file_progress
                       SET lines_processed = %s, lines_valid = %s, lines_invalid = %s
                     WHERE dataset_id = %s AND filename = %s
                    """,
                    (lines_read, lines_valid, lines_invalid, dataset_id, filename),
                )
            conn.commit()
            return
        except psycopg2.errors.DeadlockDetected:
            conn.rollback()
            if attempt == _max_retries - 1:
                raise
            wait = (2 ** attempt) * 0.1 + random.uniform(0, 0.1)
            time.sleep(wait)


# ---------------------------------------------------------------------------
# Acumulación en RAM
# ---------------------------------------------------------------------------

def _make_acc(mode: str) -> StatsAccumulator | LightStatsAccumulator:
    if mode == "light":
        return LightStatsAccumulator()
    return StatsAccumulator()


def _update_acc(
    acc: StatsAccumulator | LightStatsAccumulator,
    decoded: str,
    mode: str,
    dictionaries: dict,
) -> None:
    if mode == "light":
        update_light_stats(acc, decoded)
    else:
        update_stats(acc, decoded, dictionaries)
        if mode == "full":
            s = strength_model.score(decoded, dictionaries=dictionaries)
            acc.score_hist[s] += 1
            g = strength_model.estimate_guesses(decoded, dictionaries=dictionaries)
            acc.sum_guesses += float(g)


# ---------------------------------------------------------------------------
# Estado mutable mínimo para signal handlers
# ---------------------------------------------------------------------------

class _SignalState:
    def __init__(self):
        self.aborted = False
        self.signal_name: str | None = None

    def request_abort(self, signum: int, _frame) -> None:
        self.signal_name = signal.Signals(signum).name
        self.aborted = True


# ---------------------------------------------------------------------------
# Procesamiento de un fichero
# ---------------------------------------------------------------------------

def process_file(
    conn,
    path: Path,
    dataset_id: int,
    batch_size: int,
    mode: str,
    dictionaries: dict,
    sigstate: _SignalState,
    limit: int | None,
    logger: logging.Logger,
) -> dict:
    logger.info("procesando %s [mode=%s]", path, mode)

    # Checkpoint resume: read existing progress before marking running
    with conn.cursor() as cur:
        cur.execute(
            "SELECT lines_processed, lines_valid, lines_invalid "
            "FROM stats.file_progress "
            "WHERE dataset_id = %s AND filename = %s",
            (dataset_id, str(path)),
        )
        checkpoint = cur.fetchone()

    skip_lines = 0
    lines_read = 0
    lines_valid = 0
    lines_invalid = 0
    if checkpoint and checkpoint[0]:
        skip_lines = checkpoint[0]
        lines_read = checkpoint[0]
        lines_valid = checkpoint[1] or 0
        lines_invalid = checkpoint[2] or 0
        logger.info(
            "retomando %s desde línea %d (%d válidas ya procesadas)",
            path.name, skip_lines, lines_valid,
        )

    with conn.cursor() as cur:
        mark_file_running(cur, dataset_id, str(path))
    conn.commit()

    acc = _make_acc(mode)
    batch_count = 0

    with path.open("rb") as fh:
        if skip_lines:
            logger.info("saltando %d líneas del checkpoint…", skip_lines)
            for _ in range(skip_lines):
                fh.readline()

        for raw in fh:
            if sigstate.aborted:
                break
            if limit is not None and limit <= 0:
                break

            lines_read += 1
            decoded, had_invalid = decode_line(raw)
            if had_invalid:
                lines_invalid += 1
            if not decoded:
                continue

            lines_valid += 1
            if limit is not None:
                limit -= 1
            _update_acc(acc, decoded, mode, dictionaries)
            batch_count += 1

            if batch_count >= batch_size:
                with conn.cursor() as cur:
                    flush_batch(cur, conn, dataset_id, acc, mode, str(path),
                                lines_read, lines_valid, lines_invalid, is_final=False)
                acc = _make_acc(mode)
                batch_count = 0

    with conn.cursor() as cur:
        flush_batch(cur, conn, dataset_id, acc, mode, str(path),
                    lines_read, lines_valid, lines_invalid,
                    is_final=True, aborted=sigstate.aborted)

    logger.info(
        "fichero %s: %d líneas, %d válidas, %d inválidas",
        path.name, lines_read, lines_valid, lines_invalid,
    )
    return {
        "lines_read": lines_read,
        "lines_valid": lines_valid,
        "lines_invalid": lines_invalid,
    }


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

@contextmanager
def _connection(dry_run: bool):
    if dry_run:
        yield None
        return
    conn = db.get_connection()
    conn.autocommit = False
    try:
        yield conn
    finally:
        conn.close()


def run_reset_only(args: argparse.Namespace, logger: logging.Logger) -> int:
    with _connection(False) as conn:
        try:
            with conn.cursor() as cur:
                reset_all(cur, logger)
            conn.commit()
            logger.info("reset-only completado; saliendo sin iniciar ingesta")
            return 0
        except Exception as exc:  # noqa: BLE001
            logger.exception("fallo en reset-only: %s", exc)
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            return 1


def run(args: argparse.Namespace, logger: logging.Logger | None = None) -> int:
    """Camino serial single-process."""
    cfg = config.load_config()
    if logger is None:
        logger = setup_logging(cfg.log_dir, cfg.log_level)

    files = list(iter_dataset_files(args.dataset_path))
    logger.info(
        "dataset_path=%s files=%d dry_run=%s reset=%s limit=%s mode=%s",
        args.dataset_path, len(files), args.dry_run, args.reset, args.limit, args.mode,
    )

    if args.dry_run:
        for p in files:
            logger.info("[dry-run] procesaría %s", p)
        return 0

    dictionaries = pat_dict.load_dictionaries(cfg.dictionaries_path) if args.mode != "light" else {}

    sigstate = _SignalState()
    signal.signal(signal.SIGINT, sigstate.request_abort)
    signal.signal(signal.SIGTERM, sigstate.request_abort)

    totals = {"files": 0, "lines_read": 0, "lines_valid": 0, "lines_invalid": 0}
    run_id: int | None = None

    with _connection(False) as conn:
        try:
            with conn.cursor() as cur:
                if args.reset:
                    reset_all(cur, logger)
                    conn.commit()
                dataset_id = ensure_dataset(cur, args.dataset_name)
                run_id = open_run(cur, dataset_id)
                conn.commit()

                pending = [p for p in files
                           if not file_already_completed(cur, dataset_id, str(p))]
            conn.commit()

            limit = args.limit
            for path in pending:
                if sigstate.aborted:
                    break
                result = process_file(
                    conn, path, dataset_id, cfg.etl_batch_size,
                    args.mode, dictionaries, sigstate, limit, logger,
                )
                totals["files"] += 1
                totals["lines_read"] += result["lines_read"]
                totals["lines_valid"] += result["lines_valid"]
                totals["lines_invalid"] += result["lines_invalid"]
                if limit is not None:
                    limit -= result["lines_valid"]
                    if limit <= 0:
                        break

            final_status = "interrupted" if sigstate.aborted else "completed"
            with conn.cursor() as cur:
                close_run(cur, run_id, final_status, totals)
            conn.commit()
            logger.info("ingesta finalizada: status=%s totals=%s", final_status, totals)
            return 0 if final_status == "completed" else 130
        except Exception as exc:  # noqa: BLE001
            logger.exception("fallo en ingesta: %s", exc)
            try:
                if run_id is not None:
                    with conn.cursor() as cur:
                        close_run(cur, run_id, "failed", totals, error=str(exc))
                    conn.commit()
            except psycopg2.Error:
                logger.error("además fallo al cerrar el run")
            return 1


def _terminate_workers(workers: list, logger: logging.Logger) -> None:
    for w in workers:
        if w.is_alive():
            w.terminate()
    for w in workers:
        w.join(timeout=5)


def _worker_main(
    file_queue,
    result_queue,
    dataset_id: int,
    worker_id: int,
    mode: str,
    dictionaries_path: str,
    batch_size: int,
) -> None:
    logger = logging.getLogger(f"etl.worker.{worker_id}")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler(sys.stdout))

    dictionaries = pat_dict.load_dictionaries(Path(dictionaries_path)) if mode != "light" else {}
    sigstate = _SignalState()
    signal.signal(signal.SIGINT, sigstate.request_abort)
    signal.signal(signal.SIGTERM, sigstate.request_abort)

    totals: dict = {"files": 0, "lines_read": 0, "lines_valid": 0, "lines_invalid": 0}
    conn = db.get_connection()
    conn.autocommit = False
    try:
        while not sigstate.aborted:
            try:
                path_str = file_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if path_str is None:
                break
            result = process_file(
                conn, Path(path_str), dataset_id, batch_size,
                mode, dictionaries, sigstate, None, logger,
            )
            totals["files"] += 1
            totals["lines_read"] += result["lines_read"]
            totals["lines_valid"] += result["lines_valid"]
            totals["lines_invalid"] += result["lines_invalid"]
    except Exception as exc:  # noqa: BLE001
        logger.exception("worker %d fallo: %s", worker_id, exc)
        totals["error"] = str(exc)
    finally:
        conn.close()

    result_queue.put({"worker_id": worker_id, "interrupted": sigstate.aborted, **totals})


def run_parallel(args: argparse.Namespace, logger: logging.Logger | None = None) -> int:
    """Camino paralelo: N workers con conexiones PG independientes."""
    cfg = config.load_config()
    if logger is None:
        logger = setup_logging(cfg.log_dir, cfg.log_level)

    files = list(iter_dataset_files(args.dataset_path))
    totals: dict = {"files": 0, "lines_read": 0, "lines_valid": 0, "lines_invalid": 0}
    run_id: int | None = None
    workers: list = []

    sigstate = _SignalState()
    signal.signal(signal.SIGINT, sigstate.request_abort)
    signal.signal(signal.SIGTERM, sigstate.request_abort)

    with _connection(False) as conn:
        try:
            with conn.cursor() as cur:
                if args.reset:
                    reset_all(cur, logger)
                    conn.commit()
                dataset_id = ensure_dataset(cur, args.dataset_name)
                run_id = open_run(cur, dataset_id)
                conn.commit()

                pending = [str(p) for p in files
                           if not file_already_completed(cur, dataset_id, str(p))]
            conn.commit()

            if not pending:
                logger.info("nada que procesar; cerrando run vacío")
                with conn.cursor() as cur:
                    close_run(cur, run_id, "completed", totals)
                conn.commit()
                return 0

            ctx = mp.get_context("spawn")
            file_queue = ctx.Queue()
            result_queue = ctx.Queue()
            for path_str in pending:
                file_queue.put(path_str)
            for _ in range(args.workers):
                file_queue.put(None)

            for i in range(args.workers):
                proc = ctx.Process(
                    target=_worker_main,
                    args=(
                        file_queue, result_queue, dataset_id, i,
                        args.mode, str(cfg.dictionaries_path), cfg.etl_batch_size,
                    ),
                    name=f"passeval-worker-{i}",
                )
                proc.start()
                workers.append(proc)
            logger.info("spawneados %d workers", len(workers))

            received = 0
            while received < args.workers:
                if sigstate.aborted:
                    logger.warning("aborto solicitado")
                    _terminate_workers(workers, logger)
                    break
                try:
                    payload = result_queue.get(timeout=1.0)
                except queue.Empty:
                    dead_bad = [w for w in workers
                                if not w.is_alive() and w.exitcode not in (0, None)]
                    if dead_bad:
                        raise RuntimeError(
                            f"worker muerto sin resultado: {[w.name for w in dead_bad]}"
                        )
                    continue

                received += 1
                totals["files"] += payload["files"]
                totals["lines_read"] += payload["lines_read"]
                totals["lines_valid"] += payload["lines_valid"]
                totals["lines_invalid"] += payload["lines_invalid"]
                if payload.get("interrupted"):
                    sigstate.aborted = True
                logger.info(
                    "worker %d devuelto: %d ficheros, %d líneas válidas",
                    payload["worker_id"], payload["files"], payload["lines_valid"],
                )

            for w in workers:
                w.join(timeout=10)

            final_status = "interrupted" if sigstate.aborted else "completed"
            with conn.cursor() as cur:
                close_run(cur, run_id, final_status, totals)
            conn.commit()
            logger.info("ingesta paralela finalizada: status=%s totals=%s", final_status, totals)
            return 0 if final_status == "completed" else 130
        except Exception as exc:  # noqa: BLE001
            logger.exception("fallo en ingesta paralela: %s", exc)
            _terminate_workers(workers, logger)
            try:
                if run_id is not None:
                    with conn.cursor() as cur:
                        close_run(cur, run_id, "failed", totals, error=str(exc))
                    conn.commit()
            except psycopg2.Error:
                logger.error("además fallo al cerrar el run")
            return 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = config.load_config()
    logger = setup_logging(cfg.log_dir, cfg.log_level)

    if args.reset_only:
        return run_reset_only(args, logger)

    if args.workers > 1 and not args.dry_run:
        return run_parallel(args, logger)

    return run(args, logger)


if __name__ == "__main__":
    raise SystemExit(main())
