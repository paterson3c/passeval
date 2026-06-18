"""Carga de configuración desde .env / variables de entorno.

Convención libpq: PGPASSWORD se trata como variable de entorno estándar
y NO se almacena en el dataclass de configuración. psycopg2 y psql la
leen automáticamente del entorno. Mantenerla fuera del objeto evita su
exposición en logs, repr() o trazas de error accidentales.

Cualquier variable obligatoria ausente provoca RuntimeError al llamar
load_config(). Las opcionales tienen defaults explícitos.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Variable de entorno obligatoria '{name}' no definida. "
            f"Revisa tu fichero .env (plantilla en .env.example)."
        )
    return value


def _optional(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value else default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


@dataclass(frozen=True)
class Config:
    db_host: str
    db_port: int
    db_name: str
    db_user: str

    dataset_path: Path
    dictionaries_path: Path
    log_dir: Path

    hibp_api_url: str
    hibp_user_agent: str
    hibp_timeout: float
    hibp_add_padding: bool

    etl_batch_size: int
    etl_workers: int

    sample_checkpoint_lines: int
    sample_log_every_lines: int

    collect_user_stats: bool
    user_queries_dataset: str

    log_level: str


_loaded: Config | None = None


def load_config(env_file: Path | None = None, force_reload: bool = False) -> Config:
    """Cargar configuración. Cacheada por proceso salvo force_reload=True.

    Si existe un fichero .env en la raíz del proyecto (o el indicado en
    `env_file`), sus variables se vuelcan a os.environ con override=False
    (las variables ya presentes en el entorno tienen prioridad sobre el .env,
    facilitando el despliegue en systemd / contenedores).
    """
    global _loaded
    if _loaded is not None and not force_reload:
        return _loaded

    env_path = env_file if env_file is not None else PROJECT_ROOT / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)

    if not os.environ.get("PGPASSWORD"):
        raise RuntimeError(
            "PGPASSWORD no está definida. Defínela en .env o expórtala antes "
            "de invocar el proceso. Es la variable estándar de libpq."
        )

    cfg = Config(
        db_host=_require("DB_HOST"),
        db_port=_int("DB_PORT", 5432),
        db_name=_require("DB_NAME"),
        db_user=_require("DB_USER"),
        dataset_path=Path(_require("DATASET_PATH")),
        dictionaries_path=Path(_require("DICTIONARIES_PATH")),
        log_dir=Path(_optional("LOG_DIR", str(PROJECT_ROOT / "logs"))),
        hibp_api_url=_optional("HIBP_API_URL", "https://api.pwnedpasswords.com/range"),
        hibp_user_agent=_optional("HIBP_USER_AGENT", "passeval-tfg/0.1"),
        hibp_timeout=_float("HIBP_TIMEOUT", 10.0),
        hibp_add_padding=_bool("HIBP_ADD_PADDING", True),
        etl_batch_size=_int("ETL_BATCH_SIZE", 1_000_000),
        etl_workers=_int("ETL_WORKERS", 4),
        sample_checkpoint_lines=_int("SAMPLE_CHECKPOINT_LINES", 50_000_000),
        sample_log_every_lines=_int("SAMPLE_LOG_EVERY_LINES", 10_000_000),
        collect_user_stats=_bool("COLLECT_USER_STATS", False),
        user_queries_dataset=_optional("USER_QUERIES_DATASET", "user_queries"),
        log_level=_optional("LOG_LEVEL", "INFO"),
    )
    _loaded = cfg
    return cfg
