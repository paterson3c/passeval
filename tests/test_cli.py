"""Tests del módulo `cli`.

Mockean stdin (consent + getpass) para ejercitar el flujo end-to-end
de `cmd_evaluate` sin pedir entrada interactiva ni consultar HIBP real.
"""
from __future__ import annotations

import argparse
import io
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from passeval import cli, config


def _ns() -> argparse.Namespace:
    """Namespace mínimo de argparse para `cmd_evaluate`."""
    return argparse.Namespace()


def _fake_config(collect_user_stats: bool = False) -> config.Config:
    return config.Config(
        db_host="localhost", db_port=5432, db_name="test", db_user="test",
        dataset_path=Path("/tmp"), dictionaries_path=Path("/tmp"),
        log_dir=Path("/tmp"),
        hibp_api_url="https://example.com", hibp_user_agent="test",
        hibp_timeout=5.0, hibp_add_padding=False,
        etl_batch_size=1000, etl_workers=1,
        sample_checkpoint_lines=50_000_000, sample_log_every_lines=10_000_000,
        collect_user_stats=collect_user_stats,
        user_queries_dataset="user_queries",
        log_level="INFO",
    )


@contextmanager
def _patched_inputs(consent: str, passwords: list[str], cont_answers: list[str]):
    """input(): primera llamada=consent HIBP, después una respuesta de continuar
    por cada password. getpass(): devuelve passwords en orden."""
    input_answers = iter([consent] + cont_answers)
    pwd_iter = iter(passwords)
    with mock.patch("builtins.input", side_effect=lambda _prompt="": next(input_answers)):
        with mock.patch("getpass.getpass", side_effect=lambda _prompt="": next(pwd_iter)):
            yield


@contextmanager
def _capture_stdout():
    """Reemplaza sys.stdout por un StringIO y lo cede al test."""
    buf = io.StringIO()
    with mock.patch.object(sys, "stdout", buf):
        yield buf


# ---------------------------------------------------------------------------

def test_evaluate_sin_consentimiento_y_una_password_termina_limpio():
    with mock.patch.object(cli.config, "load_config", return_value=_fake_config()):
        with _patched_inputs(consent="n", passwords=["pwd1"], cont_answers=["n"]):
            with _capture_stdout() as fake_out:
                rc = cli.cmd_evaluate(_ns())
    assert rc == 0
    out = fake_out.getvalue()
    assert "Bienvenido" in out
    assert "HIBP omitido" in out
    assert "RIESGO" in out  # se imprimió el reporte
    assert "pwd1" not in out  # privacidad: la contraseña no se filtra


def test_evaluate_con_consentimiento_evalua_dos_passwords():
    """Mockea _build_hibp_session_if_consented (no consume input) y los dos
    inputs que sí consume el loop son las respuestas a "¿Evaluar otra?"."""
    pwd_iter = iter(["xq8$wM", "Lp#3vK"])
    input_iter = iter(["y", "n"])  # solo las del loop, no la del consent
    with mock.patch.object(cli, "_build_hibp_session_if_consented", return_value=None):
        with mock.patch.object(cli.config, "load_config", return_value=_fake_config()):
            with mock.patch("builtins.input", side_effect=lambda _p="": next(input_iter)):
                with mock.patch("getpass.getpass", side_effect=lambda _p="": next(pwd_iter)):
                    with _capture_stdout() as fake_out:
                        rc = cli.cmd_evaluate(_ns())
    assert rc == 0
    out = fake_out.getvalue()
    assert out.count("RIESGO") == 2
    assert "xq8$wM" not in out and "Lp#3vK" not in out


def test_evaluate_password_vacia_termina_loop():
    with mock.patch.object(cli.config, "load_config", return_value=_fake_config()):
        with _patched_inputs(consent="n", passwords=[""], cont_answers=[]):
            with _capture_stdout() as fake_out:
                rc = cli.cmd_evaluate(_ns())
    assert rc == 0
    assert "Contraseña vacía" in fake_out.getvalue()


def test_evaluate_aborto_con_eof_en_getpass_sale_sin_excepcion():
    def _raise_eof(_prompt=""):
        raise EOFError

    with mock.patch.object(cli.config, "load_config", return_value=_fake_config()):
        with mock.patch("builtins.input", side_effect=lambda _p="": "n"):
            with mock.patch("getpass.getpass", side_effect=_raise_eof):
                with _capture_stdout():
                    rc = cli.cmd_evaluate(_ns())
    assert rc == 0


def test_evaluate_stats_consent_llama_record_query():
    """Con collect_user_stats=True y consentimiento 'y', record_query es llamado."""
    with mock.patch.object(cli, "_build_hibp_session_if_consented", return_value=None):
        with mock.patch.object(cli.config, "load_config",
                               return_value=_fake_config(collect_user_stats=True)):
            with mock.patch.object(cli.user_stats, "record_query") as mock_record:
                # input: "y" para stats consent, "n" para continuar
                input_iter = iter(["y", "n"])
                pwd_iter = iter(["xq8$wM"])
                with mock.patch("builtins.input", side_effect=lambda _p="": next(input_iter)):
                    with mock.patch("getpass.getpass", side_effect=lambda _p="": next(pwd_iter)):
                        with _capture_stdout():
                            rc = cli.cmd_evaluate(_ns())
    assert rc == 0
    mock_record.assert_called_once_with("xq8$wM", "user_queries")


def test_evaluate_stats_consent_no_llama_record_query():
    """Con collect_user_stats=True pero consentimiento 'n', record_query no se llama."""
    with mock.patch.object(cli, "_build_hibp_session_if_consented", return_value=None):
        with mock.patch.object(cli.config, "load_config",
                               return_value=_fake_config(collect_user_stats=True)):
            with mock.patch.object(cli.user_stats, "record_query") as mock_record:
                # input: "n" para stats consent, "n" para continuar
                input_iter = iter(["n", "n"])
                pwd_iter = iter(["xq8$wM"])
                with mock.patch("builtins.input", side_effect=lambda _p="": next(input_iter)):
                    with mock.patch("getpass.getpass", side_effect=lambda _p="": next(pwd_iter)):
                        with _capture_stdout():
                            rc = cli.cmd_evaluate(_ns())
    assert rc == 0
    mock_record.assert_not_called()


def test_main_help_no_falla():
    """`passeval --help` debe imprimir ayuda y terminar con SystemExit(0)."""
    with mock.patch.object(sys, "stdout", io.StringIO()):
        try:
            cli.main(["--help"])
        except SystemExit as e:
            assert e.code == 0


def test_main_evaluate_subcomando_es_reconocido():
    """`passeval evaluate` debe rutear a cmd_evaluate."""
    with mock.patch.object(cli, "cmd_evaluate", return_value=0) as m:
        rc = cli.main(["evaluate"])
    assert rc == 0
    m.assert_called_once()


def test_ask_yes_no_default_no_acepta_enter_como_no():
    with mock.patch("builtins.input", return_value=""):
        assert cli._ask_yes_no("?", default_no=True) is False


def test_ask_yes_no_acepta_si_y_yes():
    for affirmative in ("y", "yes", "s", "si", "sí"):
        with mock.patch("builtins.input", return_value=affirmative):
            assert cli._ask_yes_no("?", default_no=True) is True
