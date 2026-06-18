"""Estimación de tiempos de cracking por escenario (§4.6.1 y §4.6.4).

Convención **caso medio**: el atacante acierta a la mitad del espacio de
búsqueda en promedio (asumiendo orden aleatorio de pruebas), luego

    T = G / (2 · v)             [segundos]

donde `G` es el número de guesses estimado por el modelo de scoring
(`passeval.strength.model.estimate_guesses`) y `v` es la velocidad del
atacante en intentos por segundo. Esta convención coincide con zxcvbn
y con la mayoría de la literatura académica; la decisión y la fórmula
se documentan en el capítulo 4.2 de la memoria.

Escenarios implementados (tabla §4.6.1 del informe v3):

| Escenario           | v (intentos/s) | Justificación                                |
|---------------------|----------------|----------------------------------------------|
| online_throttled    | 10             | Login web con rate limiting típico           |
| cpu_sha1            | 1e7            | CPU moderna, SHA-1, sin optimización         |
| gpu_consumer_sha1   | 1e10           | RTX 4090 (~21 GH/s SHA-1, redondeado)        |
| gpu_rig_sha1        | 1e11           | Extrapolación 8x GPU profesional             |
| argon2id_modern     | 10             | KDF moderna parametrizada (~250 ms por hash) |

Fuentes y referencias:
- Hashcat benchmark oficial para SHA-1 en NVIDIA RTX 4090:
  https://openbenchmarking.org/test/pts/hashcat (consultado 2026-04-24,
  hashcat v6.2.6). Valor reportado ~21,3 GH/s, redondeado a 1e10 para
  conservadurismo y para que la cifra sea memorable en el informe.
- Argon2id parámetros recomendados OWASP 2024 (m=64MiB, t=3, p=4):
  https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
  Tiempo objetivo ~250 ms => v ~ 4 hash/s; el escenario usa 10 como
  cota superior conservadora a favor del atacante.
- TODO: confirmar benchmark cpu_sha1 con OpenSSL speed sha1 en CPU
  representativa (i7-12700, AMD Ryzen 5800X) y dejar valor exacto en
  esta tabla. El 1e7 actual es estimación conservadora.

Las funciones expuestas:

- `estimate_time(guesses, scenario)`: segundos en caso medio para un
  escenario concreto.
- `format_time(seconds)`: cadena legible con cortes documentados.
- `all_scenarios(guesses)`: dict {escenario: tiempo_formateado} para los
  cinco escenarios.
"""
from __future__ import annotations

from typing import Mapping

# Velocidades de cada escenario en intentos por segundo.
# Las claves son los identificadores que la CLI y el reporte usan al
# referenciar escenarios.
SCENARIOS: Mapping[str, float] = {
    "online_throttled": 1e1,
    "cpu_sha1": 1e7,
    "gpu_consumer_sha1": 1e10,
    "gpu_rig_sha1": 1e11,
    "argon2id_modern": 1e1,
}

_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600
_SECONDS_PER_DAY = 86_400
_SECONDS_PER_YEAR = 31_536_000  # 365 * 24 * 3600 (año juliano sin bisiesto)
_THRESHOLD_THOUSAND_YEARS = _SECONDS_PER_YEAR * 1_000


def estimate_time(guesses: int | float, scenario: str) -> float:
    """Tiempo de cracking en **caso medio** para un escenario.

    Fórmula: `T = guesses / (2 · v)` (segundos), §4.6.4 del informe.

    Lanza `KeyError` si `scenario` no está en `SCENARIOS`. `guesses`
    puede ser `int` o `float`; se asume no negativo.
    """
    v = SCENARIOS[scenario]
    return guesses / (2 * v)


def format_time(seconds: float) -> str:
    """Convierte segundos a cadena legible con los cortes documentados.

    Cortes (de menor a mayor):
      < 1 μs, < 1 ms, < 1 segundo,
      N segundos / minutos / horas / días / años,
      > 1.000 años (cota superior cuando excede el milenio).

    Singular vs plural se ajustan en español ("1 año" vs "5 años").
    """
    if seconds < 1e-6:
        return "< 1 μs"
    if seconds < 1e-3:
        return "< 1 ms"
    if seconds < 1:
        return "< 1 segundo"
    if seconds < _SECONDS_PER_MINUTE:
        n = int(seconds)
        return f"{n} {'segundo' if n == 1 else 'segundos'}"
    if seconds < _SECONDS_PER_HOUR:
        n = int(seconds / _SECONDS_PER_MINUTE)
        return f"{n} {'minuto' if n == 1 else 'minutos'}"
    if seconds < _SECONDS_PER_DAY:
        n = int(seconds / _SECONDS_PER_HOUR)
        return f"{n} {'hora' if n == 1 else 'horas'}"
    if seconds < _SECONDS_PER_YEAR:
        n = int(seconds / _SECONDS_PER_DAY)
        return f"{n} {'día' if n == 1 else 'días'}"
    if seconds < _THRESHOLD_THOUSAND_YEARS:
        n = int(seconds / _SECONDS_PER_YEAR)
        return f"{n} {'año' if n == 1 else 'años'}"
    return "> 1.000 años"


def all_scenarios(guesses: int | float) -> dict[str, str]:
    """Devuelve `{escenario: tiempo_formateado}` para los cinco escenarios."""
    return {name: format_time(estimate_time(guesses, name)) for name in SCENARIOS}
