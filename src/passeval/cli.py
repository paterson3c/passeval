"""CLI de Passeval — evaluación interactiva de contraseñas.

Subcomando principal: `passeval evaluate`. Flujo:

1. Mensaje de bienvenida y disclaimer (no se loguea ni almacena nada).
2. Pregunta de consentimiento HIBP (opt-in único por sesión). La
   respuesta vive en memoria; al cerrar la CLI se descarta.
3. Loop:
   - Pide contraseña con `getpass.getpass()` (sin eco en pantalla).
   - Construye y renderiza el `Report`.
   - Pregunta si evaluar otra.

Decisiones de seguridad:

- La contraseña nunca se loguea (ni a stdout, ni a fichero, ni en
  trazas de excepciones). Las únicas referencias a la cadena en claro
  son los argumentos pasados a las funciones puras `build_report` y
  los detectores; no hay capturas en `try/except` que la incluyan.
- HIBP requiere consentimiento explícito; un "no" deja la sesión
  íntegramente offline.

Comando registrado en `pyproject.toml`:

    [project.scripts]
    passeval = "passeval.cli:main"
"""
from __future__ import annotations

import argparse
import getpass
import sys
from typing import Optional

from passeval import config, report, user_stats
from passeval.report import HIBPSession


_BANNER = """\
Bienvenido a Passeval. Esta herramienta evalúa contraseñas localmente.
Ninguna contraseña introducida se almacena ni se loguea.
"""

_HIBP_PROMPT = """\
¿Autorizas consultar Have I Been Pwned (HIBP) para cotejar tu contraseña
contra filtraciones públicas agregadas? La consulta usa k-anonymity:
solo se envían 5 caracteres del hash, nunca la contraseña.
[y/N]: """

_STATS_PROMPT = """\
¿Autorizas contribuir estadísticas anónimas al estudio? (opt-in)
Se registran únicamente métricas derivadas: longitud, tipo de caracteres
y entropía. Nunca se almacena la contraseña ni ningún hash de ella.
[y/N]: """


def _ask_yes_no(prompt: str, default_no: bool = True) -> bool:
    """Lee una respuesta sí/no de stdin con default configurable."""
    try:
        ans = input(prompt).strip().lower()
    except EOFError:
        return False
    if not ans:
        return not default_no
    return ans in ("y", "yes", "s", "si", "sí")


def _build_hibp_session_if_consented() -> Optional[HIBPSession]:
    if not _ask_yes_no(_HIBP_PROMPT, default_no=True):
        print("HIBP omitido para esta sesión.\n")
        return None
    cfg = config.load_config()
    print("HIBP activado para esta sesión. Puedes evaluar varias contraseñas.\n")
    return HIBPSession(
        user_agent=cfg.hibp_user_agent,
        timeout=cfg.hibp_timeout,
        add_padding=cfg.hibp_add_padding,
        api_url=cfg.hibp_api_url,
    )


def _evaluate_loop(
    hibp_session: Optional[HIBPSession],
    stats_dataset: str | None,
) -> None:
    while True:
        try:
            password = getpass.getpass("Introduce contraseña (no se mostrará): ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not password:
            print("Contraseña vacía, saliendo.")
            return

        rep = report.build_report(password, hibp_session=hibp_session)
        print()
        print(report.render_text(rep))
        print()

        if stats_dataset:
            user_stats.record_query(password, stats_dataset)

        if not _ask_yes_no("¿Evaluar otra contraseña? [y/N]: ", default_no=True):
            return


def cmd_evaluate(args: argparse.Namespace) -> int:
    print(_BANNER)
    hibp = _build_hibp_session_if_consented()

    stats_dataset: str | None = None
    cfg = config.load_config()
    if cfg.collect_user_stats:
        if _ask_yes_no(_STATS_PROMPT, default_no=True):
            stats_dataset = cfg.user_queries_dataset
            print("Estadísticas anónimas activadas para esta sesión.\n")
        else:
            print("Estadísticas omitidas para esta sesión.\n")

    _evaluate_loop(hibp, stats_dataset)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="passeval",
        description="Evaluación local de fortaleza de contraseñas (TFG).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_eval = sub.add_parser("evaluate", help="Evalúa contraseñas en modo interactivo.")
    p_eval.set_defaults(func=cmd_evaluate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
