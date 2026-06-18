"""Conexión a PostgreSQL.

PGPASSWORD se obtiene del entorno (cargado por config.load_config()).
psycopg2 la lee automáticamente igual que psql/libpq, sin necesidad de
pasarla como parámetro.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2.extensions import connection as PgConnection

from passeval.config import load_config


def get_connection(autocommit: bool = False) -> PgConnection:
    """Devuelve una nueva conexión PostgreSQL configurada desde .env.

    El llamador es responsable de cerrarla. Para gestión automática usar
    `connection_scope()`.
    """
    cfg = load_config()
    conn = psycopg2.connect(
        host=cfg.db_host,
        port=cfg.db_port,
        dbname=cfg.db_name,
        user=cfg.db_user,
    )
    if autocommit:
        conn.autocommit = True
    return conn


@contextmanager
def connection_scope(autocommit: bool = False) -> Iterator[PgConnection]:
    """Context manager que abre y cierra la conexión."""
    conn = get_connection(autocommit=autocommit)
    try:
        yield conn
    finally:
        conn.close()
