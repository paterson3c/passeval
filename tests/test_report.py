"""Tests del módulo `report`.

Verifica:
- `build_report` produce un Report coherente para varios casos.
- `render_text` incluye las secciones obligatorias del ejemplo §4.3.
- HIBPSession se respeta como opt-in (skipped si None).
- HIBPError no se propaga: se reporta como 'error' en el Report.
- La contraseña NUNCA aparece en la salida renderizada.
"""
from __future__ import annotations

import pytest

from passeval.breach_hibp import HIBPError
from passeval.report import HIBPSession, build_report, render_text


class _StubHIBP(HIBPSession):
    """HIBPSession sin red: devuelve un valor preestablecido o lanza."""

    def __init__(self, result=(False, 0), exc: Exception | None = None):
        super().__init__()
        self._result = result
        self._exc = exc

    def lookup(self, password):
        if self._exc is not None:
            raise self._exc
        return self._result


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------

def test_build_report_sin_hibp_es_offline_puro():
    rep = build_report("juan1234")
    assert rep.length == 8
    assert rep.hibp_status == "skipped"
    assert rep.hibp_count == 0
    assert rep.score == 0  # nombre + secuencia numérica
    assert rep.guesses >= 1


def test_build_report_hibp_hit_marca_riesgo_critico():
    rep = build_report(
        "alguna_password",
        hibp_session=_StubHIBP(result=(True, 9_999)),
    )
    assert rep.hibp_status == "found"
    assert rep.hibp_count == 9_999
    assert rep.risk_label == "CRÍTICO"


def test_build_report_hibp_error_no_aborta_y_se_reporta():
    """HIBPError NO se propaga; el Report queda con hibp_status='error'."""
    rep = build_report(
        "otra_pass",
        hibp_session=_StubHIBP(exc=HIBPError("simulated")),
    )
    assert rep.hibp_status == "error"
    assert rep.hibp_count == 0


def test_build_report_score_label_es_string_legible():
    rep = build_report("aaaa")
    assert rep.score_label in {"Trivial", "Muy débil", "Débil", "Fuerte", "Muy fuerte"}


def test_build_report_cracking_times_tiene_los_5_escenarios():
    rep = build_report("password123")
    obligatorios = {
        "online_throttled",
        "cpu_sha1",
        "gpu_consumer_sha1",
        "gpu_rig_sha1",
        "argon2id_modern",
    }
    assert set(rep.cracking_times.keys()) == obligatorios


# ---------------------------------------------------------------------------
# render_text: contiene secciones esperadas
# ---------------------------------------------------------------------------

def test_render_text_incluye_todas_las_secciones_obligatorias():
    rep = build_report("verano2024")
    out = render_text(rep)
    assert "Métricas descriptivas" in out
    assert "Entropía Shannon" in out
    assert "Entropía charset" in out
    assert "Fortaleza" in out
    assert "Wheeler 2016" in out
    assert "Descomposición" in out
    assert "Score" in out
    assert "Consulta HIBP" in out
    assert "Estimación de tiempo de cracking" in out
    assert "T = G / 2v" in out
    assert "RIESGO" in out


def test_render_text_no_filtra_la_contrasena_en_la_salida():
    """Sanity check de privacidad: la contraseña NUNCA debe aparecer."""
    pwd = "mi_password_super_secreto_42!"
    rep = build_report(pwd)
    out = render_text(rep)
    assert pwd not in out


def test_render_text_hibp_omitida_cuando_no_hay_consentimiento():
    rep = build_report("prueba_offline")
    out = render_text(rep)
    assert "omitida" in out


def test_render_text_hibp_error_se_indica_explicitamente():
    rep = build_report("x", hibp_session=_StubHIBP(exc=HIBPError("net")))
    out = render_text(rep)
    assert "error" in out.lower()


# ---------------------------------------------------------------------------
# HIBPSession (sanity)
# ---------------------------------------------------------------------------

def test_hibp_session_construccion_por_defecto():
    s = HIBPSession()
    assert s.add_padding is True
    assert s.cache == {}
    assert s.timeout > 0
