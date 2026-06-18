"""API web de Passeval — evaluación de contraseñas vía HTTP.

Expone un único endpoint POST /api/evaluate que recibe la contraseña,
ejecuta el mismo pipeline que la CLI y devuelve el informe en JSON.
La contraseña nunca se loguea ni se persiste.

Arranque:
    uvicorn web.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from passeval import config, user_stats
from passeval.report import HIBPSession, build_report

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

DEBUG = os.getenv("WEB_DEBUG", "false").lower() == "true"

_cfg = None


def _get_config():
    global _cfg
    if _cfg is None:
        _cfg = config.load_config()
    return _cfg


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

class EvaluateRequest(BaseModel):
    password: str
    hibp: bool = False
    stats_consent: bool = False

    @field_validator("password")
    @classmethod
    def password_not_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("La contraseña no puede estar vacía.")
        if len(v) > 128:
            raise ValueError("Contraseña demasiado larga (máx. 128 caracteres).")
        return v


class DecompositionSegment(BaseModel):
    type: str
    length: int
    guesses: int
    token: str | None = None  # None para bruteforce; visible para dict/leet/date


class EvaluateResponse(BaseModel):
    length: int
    composition: list[str]
    charset_size: int
    shannon_per_char: float
    shannon_total: float
    charset_bits: float
    decomposition: list[DecompositionSegment]
    guesses: int
    guess_bits: float
    score: int
    score_label: str
    hibp_status: str
    hibp_count: int
    cracking_times: dict[str, str]
    risk_label: str
    risk_explanation: str


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@app.post("/api/evaluate", response_model=EvaluateResponse)
async def evaluate(req: EvaluateRequest) -> EvaluateResponse:
    cfg = _get_config()

    hibp_session: HIBPSession | None = None
    if req.hibp:
        hibp_session = HIBPSession(
            user_agent=cfg.hibp_user_agent,
            timeout=cfg.hibp_timeout,
            add_padding=cfg.hibp_add_padding,
            api_url=cfg.hibp_api_url,
        )

    try:
        rep = build_report(req.password, hibp_session=hibp_session)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Error interno al evaluar.") from exc

    decomp = [
        DecompositionSegment(
            type=seg["type"],
            length=len(seg["token"]),
            guesses=seg["guesses"],
            token=seg["token"] if DEBUG else None,
        )
        for seg in rep.decomposition
    ]

    if req.stats_consent:
        cfg2 = _get_config()
        user_stats.record_query(req.password, cfg2.user_queries_dataset)

    return EvaluateResponse(
        length=rep.length,
        composition=rep.composition,
        charset_size=rep.charset_size,
        shannon_per_char=rep.shannon_per_char,
        shannon_total=rep.shannon_total,
        charset_bits=rep.charset_bits,
        decomposition=decomp,
        guesses=rep.guesses,
        guess_bits=rep.guess_bits,
        score=rep.score,
        score_label=rep.score_label,
        hibp_status=rep.hibp_status,
        hibp_count=rep.hibp_count,
        cracking_times=rep.cracking_times,
        risk_label=rep.risk_label,
        risk_explanation=rep.risk_explanation,
    )


# ---------------------------------------------------------------------------
# Frontend estático
# ---------------------------------------------------------------------------

_STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/")
async def index():
    return FileResponse(_STATIC / "index.html")
