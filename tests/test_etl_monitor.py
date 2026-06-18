"""Tests del monitor de ETL.

Cubre:
- parse_args (flags actuales).
- _fmt_duration / _fmt_num (lógica pura).
- compute_speed_eta con velocidad sostenida basada en started_at.
- render: sección de dataset totals, en curso, fallidos, completados.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from etl import monitor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(offset_s: float = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=offset_s)


def _file(name: str, status: str, lines_processed: int = 0, lines_valid: int = 0,
          started_at: datetime | None = None, finished_at: datetime | None = None) -> monitor.FileRow:
    return monitor.FileRow(name, status, lines_processed, lines_valid, started_at, finished_at)


def _snap(files: list[monitor.FileRow], at: datetime,
          runs: list | None = None,
          totals: list | None = None) -> monitor.Snapshot:
    return monitor.Snapshot(
        runs=runs or [],
        files=files,
        totals=totals or [],
        captured_at=at,
    )


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------

def test_parse_args_default_es_snapshot_unico():
    args = monitor.parse_args([])
    assert args.watch is False
    assert args.interval == 30
    assert args.expected_lines == 423_000_000


def test_parse_args_watch_e_interval():
    args = monitor.parse_args(["--watch", "--interval", "5"])
    assert args.watch is True
    assert args.interval == 5


def test_parse_args_expected_lines():
    args = monitor.parse_args(["--expected-lines", "1000000"])
    assert args.expected_lines == 1_000_000


# ---------------------------------------------------------------------------
# _fmt_duration
# ---------------------------------------------------------------------------

def test_fmt_duration_segundos():
    assert monitor._fmt_duration(45) == "45s"


def test_fmt_duration_minutos_y_segundos():
    assert monitor._fmt_duration(90) == "1m30s"
    assert monitor._fmt_duration(3599) == "59m59s"


def test_fmt_duration_horas_y_minutos():
    assert monitor._fmt_duration(3600) == "1h00m"
    assert monitor._fmt_duration(7350) == "2h02m"


# ---------------------------------------------------------------------------
# _fmt_num
# ---------------------------------------------------------------------------

def test_fmt_num_formatea_con_comas():
    assert monitor._fmt_num(1_000_000) == "1,000,000"
    assert monitor._fmt_num(0) == "0"


# ---------------------------------------------------------------------------
# compute_speed_eta — velocidad sostenida desde started_at
# ---------------------------------------------------------------------------

def test_speed_sin_started_at_devuelve_cero():
    t0 = _utc()
    curr = _snap([_file("a.txt", "running", 0, 0, None)], t0)
    out = monitor.compute_speed_eta(None, curr, expected_lines=None)
    assert out["a.txt"]["speed"] == 0.0
    assert out["a.txt"]["eta"] == "—"


def test_speed_calcula_media_sostenida():
    t0 = _utc(-100)   # started 100s ago
    t1 = _utc()       # now
    curr = _snap([_file("a.txt", "running", 5000, 4500, t0)], t1)
    out = monitor.compute_speed_eta(None, curr, expected_lines=None)
    assert abs(out["a.txt"]["speed"] - 50.0) < 1.0   # 5000 / 100 = 50 lps


def test_speed_eta_con_expected_lines():
    t0 = _utc(-100)
    t1 = _utc()
    curr = _snap([_file("a.txt", "running", 5000, 4500, t0)], t1)
    # speed=50 lps, remaining=10_000-5000=5000, eta=5000/50=100s
    out = monitor.compute_speed_eta(None, curr, expected_lines=10_000)
    assert out["a.txt"]["eta"] == "1m40s"


def test_speed_solo_para_ficheros_running():
    t0 = _utc(-60)
    t1 = _utc()
    curr = _snap([
        _file("a.txt", "completed", 1000, 900, t0, t1),
        _file("b.txt", "running", 3000, 2700, t0),
    ], t1)
    out = monitor.compute_speed_eta(None, curr, expected_lines=None)
    assert "a.txt" not in out
    assert "b.txt" in out


def test_speed_sin_lines_processed_devuelve_cero():
    t0 = _utc(-60)
    t1 = _utc()
    curr = _snap([_file("a.txt", "running", 0, 0, t0)], t1)
    out = monitor.compute_speed_eta(None, curr, expected_lines=None)
    assert out["a.txt"]["speed"] == 0.0


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

def test_render_incluye_cabecera():
    snap = _snap([], _utc())
    out = monitor.render(snap, {})
    assert "Passeval ETL monitor" in out


def test_render_sin_ficheros_lo_indica():
    snap = _snap([], _utc())
    out = monitor.render(snap, {})
    assert "Sin ficheros" in out


def test_render_muestra_fichero_running_con_velocidad():
    t0 = _utc(-60)
    t1 = _utc()
    snap = _snap([_file("0.txt", "running", 3000, 2700, t0)], t1)
    speeds = {"0.txt": {"speed": 1500.0, "eta": "5m30s"}}
    out = monitor.render(snap, speeds)
    assert "0.txt" in out
    assert "1,500" in out
    assert "5m30s" in out


def test_render_muestra_seccion_fallidos():
    t0 = _utc(-60)
    snap = _snap([_file("1.txt", "failed", 500000, 490000, t0)], _utc())
    out = monitor.render(snap, {})
    assert "FALLIDOS" in out
    assert "1.txt" in out
    assert "checkpoint" in out


def test_render_muestra_dataset_totals():
    snap = _snap([], _utc(), totals=[
        monitor.TotalsRow("rockyou2024", 5_000_000, 11.74)
    ])
    out = monitor.render(snap, {})
    assert "rockyou2024" in out
    assert "5,000,000" in out


def test_render_conteo_completados_y_fallidos_en_resumen():
    t0 = _utc(-120)
    t1 = _utc(-10)
    snap = _snap([
        _file("0.txt", "completed", 1000, 900, t0, t1),
        _file("1.txt", "failed", 500, 450, t0),
        _file("2.txt", "running", 300, 270, t0),
    ], _utc())
    out = monitor.render(snap, {"2.txt": {"speed": 100.0, "eta": "1m00s"}})
    assert "1 completados" in out
    assert "1 fallidos" in out
    assert "1 en curso" in out


