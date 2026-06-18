"""Construcción y renderizado del informe de evaluación de una contraseña.

`build_report(password, hibp_session)` orquesta:

- Métricas descriptivas: longitud, composición (alfabetos detectados),
  entropía de Shannon (bits/char y totales), entropía charset (límite
  superior ingenuo).
- Modelo propio (Wheeler 2016 adaptado): descomposición mínima vía
  `strength.model.decompose`, número de guesses estimado (`G_final`),
  bits de guessing entropy (`log2(G)`), score 0-4.
- Cotejo HIBP (`HIBPSession`): opt-in; si `hibp_session is None`, se
  reporta como omitido.
- Tiempos de cracking en los 5 escenarios obligatorios (`cracking.all_scenarios`).

`render_text(report)` produce la salida en texto plano con todas las
secciones del informe de evaluación.

`HIBPSession` encapsula el consentimiento, la caché efímera de prefijos
y los parámetros de configuración. Construido por la CLI cuando el
usuario acepta el opt-in; pasado a `build_report` por referencia.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from passeval import cracking
from passeval.breach_hibp import HIBPError, check_hibp
from passeval.strength import model as strength_model
from passeval.strength.charset import (
    DIGIT_MASK,
    LOWER_MASK,
    SYMBOL_MASK,
    UNICODE_MASK,
    UPPER_MASK,
    charset_mask,
    charset_size_from_mask,
)
from passeval.strength.shannon import shannon_entropy


SCORE_LABELS = ("Trivial", "Muy débil", "Débil", "Fuerte", "Muy fuerte")

_RISK_LABEL = {
    0: ("CRÍTICO", "La contraseña es trivialmente adivinable. Sustitúyela ya."),
    1: ("ALTO", "Muy débil: caería en segundos contra cualquier ataque offline."),
    2: ("MEDIO", "Débil contra ataques con GPU. Mejora longitud o aleatoriedad."),
    3: ("BAJO", "Fortaleza razonable. Aún así, mejor un gestor de contraseñas."),
    4: ("MUY BAJO", "Fuerte. Apropiada para uso general."),
}


@dataclass
class HIBPSession:
    """Sesión efímera de HIBP con consentimiento explícito y caché por prefijo."""

    user_agent: str = "passeval-tfg/0.1"
    timeout: float = 10.0
    add_padding: bool = True
    api_url: str = "https://api.pwnedpasswords.com/range"
    cache: dict[str, dict[str, int]] = field(default_factory=dict)

    def lookup(self, password: str) -> tuple[bool, int]:
        return check_hibp(
            password,
            timeout=self.timeout,
            cache=self.cache,
            user_agent=self.user_agent,
            add_padding=self.add_padding,
            api_url=self.api_url,
        )


@dataclass
class Report:
    # Identidad y composición
    length: int
    composition: list[str]
    charset_size: int

    # Métricas descriptivas
    shannon_per_char: float
    shannon_total: float
    charset_bits: float

    # Modelo propio (Wheeler 2016 adaptado)
    decomposition: list[dict[str, Any]]
    guesses: int
    guess_bits: float
    score: int
    score_label: str

    # Cotejo HIBP
    hibp_status: str  # "found", "not_found", "skipped", "error"
    hibp_count: int

    # Tiempos de cracking por escenario
    cracking_times: dict[str, str]

    # Riesgo derivado del peor cotejo + score
    risk_label: str
    risk_explanation: str


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------

def _composition_labels(mask: int) -> list[str]:
    out = []
    if mask & LOWER_MASK:
        out.append("minúsculas")
    if mask & UPPER_MASK:
        out.append("mayúsculas")
    if mask & DIGIT_MASK:
        out.append("dígitos")
    if mask & SYMBOL_MASK:
        out.append("símbolos")
    if mask & UNICODE_MASK:
        out.append("unicode")
    return out


def _decomposition_view(path: list[Any]) -> list[dict[str, Any]]:
    """Vista didáctica de la descomposición para el reporte."""
    out = []
    for seg in path:
        out.append(
            {
                "type": seg.type,
                "token": seg.token,
                "guesses": max(seg.guesses, 1),
                "metadata": dict(seg.metadata),
            }
        )
    return out


def _risk(score: int, hibp_status: str) -> tuple[str, str]:
    """Combina score con cotejo HIBP para determinar nivel de riesgo y explicación."""
    if hibp_status == "found":
        return (
            "CRÍTICO",
            "La contraseña aparece en filtraciones públicas. Sustitúyela por "
            "una contraseña única generada por un gestor.",
        )
    return _RISK_LABEL[score]


def build_report(
    password: str,
    hibp_session: HIBPSession | None = None,
) -> Report:
    """Construye el `Report` completo a partir de la contraseña en claro.

    `hibp_session` opcional: si `None`, HIBP queda como 'skipped'.
    Errores de HIBP se capturan y reportan como 'error' sin propagar
    la excepción al llamador.
    """
    n = len(password)
    mask = charset_mask(password)
    charset_size = charset_size_from_mask(mask)
    composition = _composition_labels(mask)

    sh_per_char = shannon_entropy(password)
    sh_total = sh_per_char * n
    charset_bits = n * math.log2(charset_size) if charset_size > 0 else 0.0

    matches = strength_model._detect_all(password)
    path, _ = strength_model.decompose(password, matches)
    merged = strength_model._merge_bruteforce(path)
    guesses = strength_model.estimate_guesses(password, matches=matches)
    guess_bits = strength_model._safe_log2(max(guesses, 1))
    score = strength_model.score(password)
    score_label = SCORE_LABELS[score]

    if hibp_session is None:
        hibp_status = "skipped"
        hibp_count = 0
    else:
        try:
            found, count = hibp_session.lookup(password)
        except HIBPError:
            hibp_status, hibp_count = "error", 0
        else:
            hibp_status = "found" if found else "not_found"
            hibp_count = count

    cracking_times = cracking.all_scenarios(guesses)

    risk_label, risk_explanation = _risk(score, hibp_status)

    return Report(
        length=n,
        composition=composition,
        charset_size=charset_size,
        shannon_per_char=sh_per_char,
        shannon_total=sh_total,
        charset_bits=charset_bits,
        decomposition=_decomposition_view(merged),
        guesses=guesses,
        guess_bits=guess_bits,
        score=score,
        score_label=score_label,
        hibp_status=hibp_status,
        hibp_count=hibp_count,
        cracking_times=cracking_times,
        risk_label=risk_label,
        risk_explanation=risk_explanation,
    )


# ---------------------------------------------------------------------------
# render_text
# ---------------------------------------------------------------------------

_SCENARIO_LABELS: dict[str, str] = {
    "online_throttled": "Online con rate limit   (10/s)",
    "cpu_sha1": "CPU doméstica SHA-1     (10⁷/s)",
    "gpu_consumer_sha1": "GPU consumer SHA-1      (10¹⁰/s)",
    "gpu_rig_sha1": "Rig 8×GPU SHA-1         (10¹¹/s)",
    "argon2id_modern": "Contra Argon2id moderno (10/s)",
}

_LINE = "─" * 61


def _format_decomposition(items: list[dict[str, Any]]) -> str:
    """Formato `[tipo: "token"] + [tipo: "token"]`.

    Los segmentos `bruteforce` se enmascaran (`bruteforce: ●●●● (N chars)`)
    para no filtrar trozos crudos de la contraseña en el reporte. Los
    matches sí muestran el token: ese es el valor didáctico (el usuario
    ve qué palabra del diccionario, qué fecha, etc. fue reconocida).
    """
    parts = []
    for it in items:
        tok = it["token"]
        kind = it["type"]
        if kind == "bruteforce":
            parts.append(f"[bruteforce: {'●' * len(tok)} ({len(tok)} chars)]")
            continue
        if kind == "dictionary":
            kind = it["metadata"].get("dict_name", "dict")
        elif kind == "spanish":
            src = it["metadata"].get("source", "es")
            kind = f"es:{src}"
        elif kind == "leet":
            kind = f"leet→{it['metadata'].get('deleeted', '?')}"
        parts.append(f'[{kind}: "{tok}"]')
    return " + ".join(parts)


def render_text(report: Report) -> str:
    """Renderiza el `Report` como texto plano con todas las secciones del informe."""
    lines: list[str] = []
    lines.append(_LINE)
    lines.append(f"Longitud: {report.length} caracteres")
    lines.append(f"Composición: {' + '.join(report.composition) if report.composition else '—'}")
    lines.append("")

    lines.append("Métricas descriptivas:")
    lines.append(
        f"  Entropía Shannon:       {report.shannon_per_char:.2f} bits/char "
        f"({report.shannon_total:.1f} bits totales)"
    )
    lines.append(
        f"  Entropía charset:       log2({report.charset_size}^{report.length}) "
        f"= {report.charset_bits:.1f} bits  (límite superior ingenuo)"
    )
    lines.append("")

    lines.append("Fortaleza (modelo propio, Wheeler 2016 adaptado):")
    lines.append(f"  Descomposición:         {_format_decomposition(report.decomposition)}")
    for it in report.decomposition:
        cost = it["guesses"]
        if it["type"] == "bruteforce":
            lines.append(f"    Coste bruteforce ({len(it['token'])} chars): {cost}")
        else:
            lines.append(f'    Coste {it["type"]:10} "{it["token"]}": {cost}')
    lines.append(
        f"  Intentos estimados:     {report.guesses:,} = {report.guess_bits:.1f} bits "
        f"de guessing entropy"
    )
    lines.append(f"  Score:                  {report.score}/4 ({report.score_label})")
    lines.append("")

    if report.hibp_status == "found":
        hibp_line = f"Sí encontrada ({report.hibp_count:,} apariciones)"
    elif report.hibp_status == "not_found":
        hibp_line = "No encontrada"
    elif report.hibp_status == "error":
        hibp_line = "error de red (omitida)"
    else:
        hibp_line = "omitida (no autorizada en esta sesión)"
    lines.append(f"Consulta HIBP: {hibp_line}")
    lines.append("")

    lines.append("Estimación de tiempo de cracking (caso medio, T = G / 2v):")
    for key, label in _SCENARIO_LABELS.items():
        lines.append(f"  {label:38}: {report.cracking_times[key]}")
    lines.append("")

    lines.append(f"RIESGO: {report.risk_label}")
    lines.append(report.risk_explanation)
    lines.append(_LINE)
    return "\n".join(lines)
