"""Tests del módulo `cracking` (§4.6.1 + §4.6.4)."""
from __future__ import annotations

import math

import pytest

from passeval.cracking import (
    SCENARIOS,
    all_scenarios,
    estimate_time,
    format_time,
)


# ---------------------------------------------------------------------------
# estimate_time
# ---------------------------------------------------------------------------

def test_estimate_time_caso_medio_gpu_consumer():
    """1e10 guesses / (2 · 1e10 v) = 0,5 s exacto."""
    t = estimate_time(1e10, "gpu_consumer_sha1")
    assert math.isclose(t, 0.5, rel_tol=1e-9)


def test_estimate_time_online_throttled_es_lento():
    """1000 guesses / (2 · 10) = 50 s."""
    assert estimate_time(1000, "online_throttled") == 50.0


def test_estimate_time_argon2_es_lento_aunque_pocos_guesses():
    """1e6 guesses / (2 · 10) = 50.000 s ≈ 13,9 horas."""
    t = estimate_time(1_000_000, "argon2id_modern")
    assert math.isclose(t, 50_000.0, rel_tol=1e-9)


def test_estimate_time_escenario_inexistente_lanza_keyerror():
    with pytest.raises(KeyError):
        estimate_time(100, "no_existe")


def test_estimate_time_acepta_int_y_float():
    assert estimate_time(1_000_000, "cpu_sha1") == estimate_time(1e6, "cpu_sha1")


# ---------------------------------------------------------------------------
# format_time: cortes documentados
# ---------------------------------------------------------------------------

def test_format_time_microsegundos():
    assert format_time(0.0000001) == "< 1 μs"


def test_format_time_milisegundos():
    assert format_time(0.0001) == "< 1 ms"


def test_format_time_subsegundo():
    assert format_time(0.5) == "< 1 segundo"


def test_format_time_segundos_singular_y_plural():
    assert format_time(1.0) == "1 segundo"
    assert format_time(45.0) == "45 segundos"


def test_format_time_minutos():
    assert format_time(60.0) == "1 minuto"
    assert format_time(120.0) == "2 minutos"
    assert format_time(3599.0) == "59 minutos"


def test_format_time_horas():
    assert format_time(3600.0) == "1 hora"
    assert format_time(7200.0) == "2 horas"


def test_format_time_dias():
    assert format_time(86400.0) == "1 día"
    assert format_time(86400.0 * 5) == "5 días"


def test_format_time_anos_contiene_cadena_ano():
    """Caso del informe: format_time(86400*400) contiene 'año'."""
    out = format_time(86400 * 400)
    assert "año" in out


def test_format_time_milenio_es_cota_superior():
    # 5000 años -> "> 1.000 años"
    assert format_time(31_536_000 * 5_000) == "> 1.000 años"


# ---------------------------------------------------------------------------
# all_scenarios
# ---------------------------------------------------------------------------

def test_all_scenarios_devuelve_los_cinco_obligatorios():
    obligatorios = {
        "online_throttled",
        "cpu_sha1",
        "gpu_consumer_sha1",
        "gpu_rig_sha1",
        "argon2id_modern",
    }
    out = all_scenarios(1)
    assert set(out.keys()) == obligatorios


def test_all_scenarios_con_un_guess_son_tiempos_pequenos():
    """Caso del informe: all_scenarios(1) -> tiempos muy pequeños en escenarios rápidos."""
    out = all_scenarios(1)
    # Los escenarios rápidos (GPU) deberían reportar < μs/ms
    assert "<" in out["gpu_consumer_sha1"]
    assert "<" in out["gpu_rig_sha1"]
    # Los lentos (online, argon2) con 1 intento dan < segundo igualmente
    # (1/(2·10) = 0,05 s -> "< 1 segundo")
    assert out["online_throttled"] == "< 1 segundo"
    assert out["argon2id_modern"] == "< 1 segundo"


def test_all_scenarios_con_guesses_grandes_diferencia_escenarios():
    """1e15 guesses: rápidos en horas, lentos en milenios."""
    out = all_scenarios(1e15)
    # online (v=10): T = 1e15/20 = 5e13 s ≈ 1.585.000 años -> "> 1.000 años"
    assert out["online_throttled"] == "> 1.000 años"
    # gpu_rig (v=1e11): T = 1e15/2e11 = 5000 s = 83 min = 1,39 horas -> "1 hora"
    assert out["gpu_rig_sha1"] == "1 hora"


# ---------------------------------------------------------------------------
# SCENARIOS: contrato de constantes
# ---------------------------------------------------------------------------

def test_scenarios_velocidades_documentadas():
    assert SCENARIOS["online_throttled"] == 1e1
    assert SCENARIOS["cpu_sha1"] == 1e7
    assert SCENARIOS["gpu_consumer_sha1"] == 1e10
    assert SCENARIOS["gpu_rig_sha1"] == 1e11
    assert SCENARIOS["argon2id_modern"] == 1e1
