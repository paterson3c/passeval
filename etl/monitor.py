"""Monitor de ingesta en tiempo real — Passeval.

Muestra un dashboard textual con el estado del ETL consultando:

- stats.ingestion_runs   — run en curso y últimos runs.
- stats.file_progress    — progreso por fichero (líneas, velocidad, ETA).
- stats.dataset_totals   — total de líneas procesadas y media de longitud.

Modo único (default) o continuo (--watch --interval N).

Uso:
    python -m etl.monitor
    python -m etl.monitor --watch --interval 30
    python -m etl.monitor --expected-lines 423000000
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from passeval import config, db


# ---------------------------------------------------------------------------
# Dataclasses de snapshot
# ---------------------------------------------------------------------------

@dataclass
class RunRow:
    id: int
    dataset_name: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    lines_valid: int


@dataclass
class FileRow:
    filename: str
    status: str
    lines_processed: int
    lines_valid: int
    started_at: datetime | None
    finished_at: datetime | None


@dataclass
class TotalsRow:
    dataset_name: str
    total_lines: int
    mean_length: float


@dataclass
class Snapshot:
    runs: list[RunRow]
    files: list[FileRow]
    totals: list[TotalsRow]
    captured_at: datetime


# ---------------------------------------------------------------------------
# Lectura de BBDD
# ---------------------------------------------------------------------------

def fetch_snapshot(cur) -> Snapshot:
    cur.execute(
        """
        SELECT r.id, d.name, r.status, r.started_at, r.finished_at,
               COALESCE(r.lines_valid, 0)
          FROM stats.ingestion_runs r
          JOIN stats.datasets d ON d.id = r.dataset_id
         ORDER BY r.started_at DESC NULLS LAST
         LIMIT 5
        """
    )
    runs = [RunRow(*row) for row in cur.fetchall()]

    cur.execute(
        """
        SELECT filename, status,
               COALESCE(lines_processed, 0),
               COALESCE(lines_valid, 0),
               started_at, finished_at
          FROM stats.file_progress
         ORDER BY status DESC, filename
         LIMIT 60
        """
    )
    files = [FileRow(*row) for row in cur.fetchall()]

    cur.execute(
        """
        SELECT d.name,
               COALESCE(dt.total_lines, 0),
               CASE WHEN dt.total_lines > 0
                    THEN ROUND(dt.sum_length::numeric / dt.total_lines, 2)
                    ELSE 0 END
          FROM stats.datasets d
          JOIN stats.dataset_totals dt ON dt.dataset_id = d.id
         WHERE d.dataset_type = 'leaked'
        """
    )
    totals = [TotalsRow(*row) for row in cur.fetchall()]

    return Snapshot(
        runs=runs,
        files=files,
        totals=totals,
        captured_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Velocidad y ETA
# ---------------------------------------------------------------------------

def _running(s: Snapshot) -> Iterable[FileRow]:
    return (f for f in s.files if f.status == "running")


def compute_speed_eta(
    prev: Snapshot | None,
    curr: Snapshot,
    expected_lines: int | None,
) -> dict[str, dict]:
    """Velocidad media sostenida desde started_at; ETA sobre esa media."""
    out: dict[str, dict] = {}
    now = curr.captured_at
    for f in _running(curr):
        if f.started_at and f.lines_processed > 0:
            started = f.started_at
            if started.tzinfo is None:
                from datetime import timezone
                started = started.replace(tzinfo=timezone.utc)
            elapsed = (now - started).total_seconds()
            speed = f.lines_processed / elapsed if elapsed > 0 else 0.0
        else:
            speed = 0.0
        if expected_lines and speed > 0:
            remaining = max(0, expected_lines - f.lines_processed)
            eta = _fmt_duration(remaining / speed)
        else:
            eta = "—"
        out[f.filename] = {"speed": speed, "eta": eta}
    return out


def _fmt_duration(s: float) -> str:
    if s < 60:
        return f"{int(s)}s"
    if s < 3600:
        return f"{int(s // 60)}m{int(s % 60):02d}s"
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    return f"{h}h{m:02d}m"


def _fmt_num(n: int) -> str:
    return f"{n:,}"


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

_CLEAR = "\033[2J\033[H"


def render(snap: Snapshot, speeds: dict[str, dict]) -> str:
    out: list[str] = []
    out.append(f"=== Passeval ETL monitor === {snap.captured_at:%Y-%m-%d %H:%M:%S} UTC")

    # Totales por dataset
    if snap.totals:
        out.append("")
        out.append("Dataset totals:")
        for t in snap.totals:
            out.append(
                f"  {t.dataset_name:<25}  {_fmt_num(t.total_lines):>15} líneas válidas"
                f"  media longitud: {t.mean_length:.2f}"
            )

    # Último run activo
    if snap.runs:
        out.append("")
        out.append("Últimos runs:")
        for r in snap.runs[:3]:
            started = r.started_at.strftime("%H:%M:%S") if r.started_at else "—"
            finished = r.finished_at.strftime("%H:%M:%S") if r.finished_at else "running"
            out.append(
                f"  #{r.id:<4} {r.dataset_name:<20} [{r.status:<11}]"
                f"  {started} → {finished}"
                f"  {_fmt_num(r.lines_valid)} líneas"
            )

    # Ficheros
    if snap.files:
        running = [f for f in snap.files if f.status == "running"]
        completed = [f for f in snap.files if f.status == "completed"]
        failed = [f for f in snap.files if f.status == "failed"]
        pending = [f for f in snap.files if f.status not in ("running", "completed", "failed")]

        if running:
            total_speed = sum(speeds.get(f.filename, {}).get("speed", 0.0) for f in running)
            out.append("")
            out.append(f"En curso:  (velocidad total: {total_speed:,.0f} lps)")
            out.append(f"  {'fichero':<45} {'válidas':>12}  {'lps':>8}  {'ETA':>8}")
            for f in running:
                sp = speeds.get(f.filename, {})
                speed = sp.get("speed", 0.0)
                eta = sp.get("eta", "—")
                name = f.filename.split("/")[-1] or f.filename
                out.append(
                    f"  {name:<45} {_fmt_num(f.lines_valid):>12}"
                    f"  {speed:>8.0f}  {eta:>8}"
                )

        if failed:
            out.append("")
            out.append(f"*** FALLIDOS ({len(failed)}) ***")
            for f in failed:
                name = f.filename.split("/")[-1] or f.filename
                out.append(f"  {name:<45} {_fmt_num(f.lines_valid):>12}  (checkpoint: {_fmt_num(f.lines_processed)} líneas)")

        out.append("")
        completed_count = len(completed)
        pending_count = len(pending)
        total_files = len(snap.files)
        out.append(
            f"Ficheros: {completed_count} completados  "
            f"{len(running)} en curso  "
            f"{len(failed)} fallidos  "
            f"{pending_count} pendientes  "
            f"(total {total_files})"
        )

        if completed:
            out.append("")
            recent = sorted(completed, key=lambda f: f.finished_at or datetime.min, reverse=True)[:5]
            out.append(f"Últimos completados ({len(recent)}):")
            for f in recent:
                name = f.filename.split("/")[-1] or f.filename
                duration = ""
                if f.started_at and f.finished_at:
                    secs = (f.finished_at - f.started_at).total_seconds()
                    duration = f"  {_fmt_duration(secs)}"
                out.append(
                    f"  {name:<45} {_fmt_num(f.lines_valid):>12}{duration}"
                )
    else:
        out.append("")
        out.append("Sin ficheros en file_progress (¿ingesta aún no iniciada?)")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="etl.monitor",
        description="Monitor en tiempo real del ETL de ingesta.",
    )
    p.add_argument("--watch", action="store_true",
                   help="Modo continuo: refresca cada --interval segundos.")
    p.add_argument("--interval", type=int, default=30,
                   help="Segundos entre refrescos en --watch (default 30).")
    p.add_argument("--expected-lines", type=int, default=423_000_000,
                   help="Líneas esperadas por fichero para ETA (default 423M = 0.txt).")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config.load_config()

    if not args.watch:
        with db.connection_scope() as conn:
            with conn.cursor() as cur:
                snap = fetch_snapshot(cur)
        speeds = compute_speed_eta(None, snap, args.expected_lines)
        print(render(snap, speeds))
        return 0

    stop = {"flag": False}
    def _on_signal(_s, _f): stop["flag"] = True  # noqa: E306
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    prev: Snapshot | None = None
    with db.connection_scope() as conn:
        while not stop["flag"]:
            with conn.cursor() as cur:
                snap = fetch_snapshot(cur)
            speeds = compute_speed_eta(prev, snap, args.expected_lines)
            sys.stdout.write(_CLEAR)
            sys.stdout.write(render(snap, speeds))
            sys.stdout.write("\n")
            sys.stdout.flush()
            prev = snap
            for _ in range(args.interval):
                if stop["flag"]:
                    break
                time.sleep(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
