"""Tests del ETL de ingesta — Passeval.

Cubre:
- parse_args: flags y validaciones.
- iter_dataset_files.
- process_file: acumula stats y hace flush atómico por batch.
- flush_batch: inserta histogramas + dataset_totals + file_progress en una transacción.
- reset_all: TRUNCATE stats.* y DELETE catálogos.
- ensure_dataset: crea o recupera dataset_id + dataset_totals.
- dry-run: NO abre conexión.
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest import mock

import pytest

from etl import ingest
from etl._stats_accumulators import LightStatsAccumulator, StatsAccumulator


# ---------------------------------------------------------------------------
# Helpers fake (cursor y conexión sin BBDD real)
# ---------------------------------------------------------------------------

class _FakeCur:
    def __init__(self):
        self.executed: list[tuple[str, object]] = []
        self.executed_many: list[tuple[str, list]] = []
        self._return: object = None

    def execute(self, sql: str, params=None) -> None:
        self.executed.append((sql.strip(), params))

    def executemany(self, sql: str, rows) -> None:
        self.executed_many.append((sql.strip(), list(rows)))

    def fetchone(self):
        return self._return

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    @property
    def all_sql(self) -> str:
        return " ".join(s for s, _ in self.executed)


class _FakeConn:
    def __init__(self):
        self.commits = 0
        self._cur = _FakeCur()

    def commit(self) -> None:
        self.commits += 1

    def cursor(self) -> _FakeCur:
        return self._cur


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------

def test_parse_args_defaults():
    args = ingest.parse_args(["--dataset-path", "/tmp/x", "--dataset-name", "ry"])
    assert args.dataset_path == Path("/tmp/x")
    assert args.dataset_name == "ry"
    assert args.mode == ingest.DEFAULT_MODE
    assert args.limit is None
    assert args.dry_run is False
    assert args.reset is False
    assert args.workers == 1


def test_parse_args_modo_light():
    args = ingest.parse_args(["--dataset-path", "/d", "--dataset-name", "n", "--mode", "light"])
    assert args.mode == "light"


def test_parse_args_modo_full():
    args = ingest.parse_args(["--dataset-path", "/d", "--dataset-name", "n", "--mode", "full"])
    assert args.mode == "full"


def test_parse_args_limit_no_soportado_en_paralelo():
    with pytest.raises(SystemExit):
        ingest.parse_args([
            "--dataset-path", "/d", "--dataset-name", "n",
            "--limit", "1000", "--workers", "4",
        ])


def test_parse_args_reset_y_reset_only_mutuamente_exclusivos():
    with pytest.raises(SystemExit):
        ingest.parse_args([
            "--dataset-path", "/d", "--dataset-name", "n",
            "--reset", "--reset-only",
        ])


# ---------------------------------------------------------------------------
# iter_dataset_files
# ---------------------------------------------------------------------------

def test_iter_dataset_files_fichero_unico(tmp_path: Path):
    f = tmp_path / "x.txt"
    f.write_text("a\nb\n", encoding="utf-8")
    assert list(ingest.iter_dataset_files(f)) == [f]


def test_iter_dataset_files_directorio_recursivo_orden_estable(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    a = tmp_path / "a.txt"; a.write_text("x", encoding="utf-8")
    b = tmp_path / "sub" / "b.txt"; b.write_text("y", encoding="utf-8")
    files = list(ingest.iter_dataset_files(tmp_path))
    assert files == sorted(files)
    assert a in files and b in files


# ---------------------------------------------------------------------------
# ensure_dataset
# ---------------------------------------------------------------------------

def test_ensure_dataset_crea_si_no_existe():
    cur = _FakeCur()
    calls = [None, (99,)]
    idx = [0]
    def _fetch():
        r = calls[idx[0]]; idx[0] += 1; return r
    cur.fetchone = _fetch
    assert ingest.ensure_dataset(cur, "test_ds") == 99
    assert any("INSERT INTO stats.datasets" in s for s, _ in cur.executed)
    assert any("dataset_totals" in s for s, _ in cur.executed)


def test_ensure_dataset_devuelve_existente():
    cur = _FakeCur()
    cur._return = (42,)
    assert ingest.ensure_dataset(cur, "test_ds") == 42
    assert any("dataset_totals" in s for s, _ in cur.executed)


# ---------------------------------------------------------------------------
# reset_all
# ---------------------------------------------------------------------------

def test_reset_all_trunca_tablas_correctas():
    cur = _FakeCur()
    ingest.reset_all(cur, logging.getLogger("t"))
    sqls = cur.all_sql
    assert "TRUNCATE stats" in sqls
    assert "DELETE FROM stats.file_progress" in sqls
    assert "DELETE FROM stats.ingestion_runs" in sqls
    assert "DELETE FROM stats.datasets" in sqls


# ---------------------------------------------------------------------------
# flush_batch
# ---------------------------------------------------------------------------

def test_flush_batch_hace_commit():
    conn = _FakeConn()
    acc = LightStatsAccumulator()
    ingest.flush_batch(conn._cur, conn, 1, acc, "light", "/f",
                       10, 8, 2, is_final=False)
    assert conn.commits == 1


def test_flush_batch_final_marca_completed():
    conn = _FakeConn()
    acc = LightStatsAccumulator()
    ingest.flush_batch(conn._cur, conn, 1, acc, "light", "/f",
                       10, 8, 2, is_final=True)
    assert "status = 'completed'" in conn._cur.all_sql


def test_flush_batch_aborted_marca_failed():
    conn = _FakeConn()
    acc = LightStatsAccumulator()
    ingest.flush_batch(conn._cur, conn, 1, acc, "light", "/f",
                       5, 3, 2, is_final=True, aborted=True)
    assert "status = 'failed'" in conn._cur.all_sql


def test_flush_batch_intermedio_no_marca_completed():
    conn = _FakeConn()
    acc = LightStatsAccumulator()
    ingest.flush_batch(conn._cur, conn, 1, acc, "light", "/f",
                       5, 3, 2, is_final=False)
    assert "status = 'completed'" not in conn._cur.all_sql


# ---------------------------------------------------------------------------
# process_file
# ---------------------------------------------------------------------------

def test_process_file_acumula_longitud(tmp_path: Path):
    f = tmp_path / "x.txt"
    f.write_text("password\n123456\nqwerty\n", encoding="utf-8")
    conn = _FakeConn()
    sigstate = ingest._SignalState()
    result = ingest.process_file(
        conn, f, dataset_id=1, batch_size=1000,
        mode="light", dictionaries={}, sigstate=sigstate,
        limit=None, logger=logging.getLogger("test"),
    )
    assert result["lines_read"] == 3
    assert result["lines_valid"] == 3
    assert result["lines_invalid"] == 0
    assert conn.commits >= 1  # flush final


def test_process_file_respeta_limit(tmp_path: Path):
    f = tmp_path / "x.txt"
    f.write_text("a\nb\nc\nd\n", encoding="utf-8")
    conn = _FakeConn()
    sigstate = ingest._SignalState()
    result = ingest.process_file(
        conn, f, 1, 1000, "light", {}, sigstate,
        limit=2, logger=logging.getLogger("t"),
    )
    assert result["lines_valid"] == 2


def test_process_file_flush_periodico_por_batch(tmp_path: Path):
    f = tmp_path / "x.txt"
    f.write_text("\n".join(f"pass{i}" for i in range(7)) + "\n", encoding="utf-8")
    conn = _FakeConn()
    sigstate = ingest._SignalState()
    ingest.process_file(
        conn, f, 1, batch_size=3, mode="light", dictionaries={},
        sigstate=sigstate, limit=None, logger=logging.getLogger("t"),
    )
    # 7 lines, batch=3: flush at 3, flush at 6, flush final = at least 3 commits
    # Plus 1 commit for mark_file_running = at least 4 total
    assert conn.commits >= 3


def test_process_file_marca_running_y_completed(tmp_path: Path):
    f = tmp_path / "x.txt"
    f.write_text("hello\n", encoding="utf-8")
    conn = _FakeConn()
    sigstate = ingest._SignalState()
    ingest.process_file(
        conn, f, 1, 1000, "light", {}, sigstate,
        limit=None, logger=logging.getLogger("t"),
    )
    sqls = conn._cur.all_sql
    assert "INSERT INTO stats.file_progress" in sqls
    assert "status = 'completed'" in sqls


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------

def test_dry_run_no_abre_conexion(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("pwd1\n", encoding="utf-8")
    fake_cfg = mock.Mock(
        log_dir=tmp_path / "logs",
        log_level="INFO",
        dictionaries_path=Path("data/dictionaries"),
        etl_batch_size=1000,
        etl_workers=4,
    )
    args = ingest.parse_args([
        "--dataset-path", str(tmp_path),
        "--dataset-name", "test_dataset",
        "--dry-run",
    ])
    with mock.patch.object(ingest.config, "load_config", return_value=fake_cfg):
        with mock.patch.object(ingest.db, "get_connection") as gc:
            rc = ingest.run(args, logger=logging.getLogger("t"))
    assert rc == 0
    gc.assert_not_called()


