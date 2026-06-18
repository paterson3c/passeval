"""Tests del módulo `breach_hibp`.

Mockea la capa HTTP construyendo una `requests.Session`-like fake con
métodos `get(url, headers, timeout)` que devuelven un objeto con
`status_code` y `text`. Así se ejercita toda la lógica del cliente
(parser, k-anonymity, caché, errores) sin tocar la red.

Un test marcado `@pytest.mark.network` ataca el endpoint real de HIBP
y se excluye por defecto con `pytest -m "not network"`.
"""
from __future__ import annotations

import hashlib

import pytest
import requests

from passeval.breach_hibp import (
    HIBPError,
    _parse_range_response,
    check_hibp,
)


# ---------------------------------------------------------------------------
# Helpers: SHA-1 y fakes HTTP
# ---------------------------------------------------------------------------

def _sha1_upper(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest().upper()


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code


class _FakeSession:
    """Fake mínimo con `get`. Registra última URL/headers para verificación."""

    def __init__(self, response: _FakeResponse | None = None, exc: Exception | None = None):
        self._response = response
        self._exc = exc
        self.last_url: str | None = None
        self.last_headers: dict | None = None
        self.last_timeout: float | None = None
        self.call_count = 0

    def get(self, url: str, headers: dict, timeout: float):
        self.call_count += 1
        self.last_url = url
        self.last_headers = headers
        self.last_timeout = timeout
        if self._exc is not None:
            raise self._exc
        return self._response


def _build_response_for(password: str, count: int, extra: list[tuple[str, int]] | None = None) -> _FakeResponse:
    """Construye una respuesta HIBP que incluye el sufijo de `password` con `count`."""
    digest = _sha1_upper(password)
    suffix = digest[5:]
    lines = [f"{suffix}:{count}"]
    for s, c in extra or []:
        lines.append(f"{s}:{c}")
    return _FakeResponse("\r\n".join(lines))


# ---------------------------------------------------------------------------
# _parse_range_response
# ---------------------------------------------------------------------------

def test_parse_range_simple():
    text = "ABCDE...:42\r\nFFFFF...:1"
    out = _parse_range_response(text)
    assert out["ABCDE..."] == 42
    assert out["FFFFF..."] == 1


def test_parse_range_ignora_lineas_vacias_y_malformadas():
    text = "\nAAA:5\nlinea_sin_dos_puntos\nBBB:no_es_int\nCCC:7\n"
    out = _parse_range_response(text)
    assert out == {"AAA": 5, "CCC": 7}


def test_parse_range_normaliza_sufijo_a_uppercase():
    out = _parse_range_response("abcde:9")
    assert out == {"ABCDE": 9}


# ---------------------------------------------------------------------------
# check_hibp: hits, misses, contrato del request
# ---------------------------------------------------------------------------

def test_check_hibp_hit_devuelve_true_y_count():
    session = _FakeSession(_build_response_for("password", 99))
    found, count = check_hibp("password", session=session)
    assert found is True
    assert count == 99


def test_check_hibp_miss_devuelve_false_y_cero():
    # Respuesta con sufijos que NO son los de "supersecret_inventado"
    session = _FakeSession(_FakeResponse("0000000000000000000000000000000000A:5"))
    found, count = check_hibp("supersecret_inventado", session=session)
    assert found is False
    assert count == 0


def test_check_hibp_envia_solo_prefijo_de_5_chars():
    """k-anonymity: la URL debe contener únicamente los 5 primeros hex."""
    session = _FakeSession(_build_response_for("hola", 1))
    check_hibp("hola", session=session)
    expected_prefix = _sha1_upper("hola")[:5]
    assert session.last_url.endswith(f"/{expected_prefix}")
    # Y el sufijo NO debe aparecer en la URL.
    assert _sha1_upper("hola")[5:] not in session.last_url


def test_check_hibp_envia_add_padding_y_user_agent():
    session = _FakeSession(_build_response_for("hola", 0))
    check_hibp("hola", session=session, user_agent="passeval-tfg/0.1", add_padding=True)
    assert session.last_headers["Add-Padding"] == "true"
    assert session.last_headers["User-Agent"] == "passeval-tfg/0.1"


def test_check_hibp_omite_add_padding_si_se_desactiva():
    session = _FakeSession(_build_response_for("hola", 0))
    check_hibp("hola", session=session, add_padding=False)
    assert "Add-Padding" not in session.last_headers


def test_check_hibp_padding_no_falsea_count():
    """Registros de padding (count=0) no deben aparecer como hits."""
    target_suffix = _sha1_upper("password")[5:]
    text = f"{target_suffix}:0\r\nDEADBEEF00000000000000000000000000A:42"
    session = _FakeSession(_FakeResponse(text))
    found, count = check_hibp("password", session=session)
    assert found is False
    assert count == 0


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def test_check_hibp_usa_cache_si_prefijo_ya_consultado():
    session = _FakeSession(_build_response_for("hola", 7))
    cache: dict[str, dict[str, int]] = {}
    check_hibp("hola", session=session, cache=cache)
    assert session.call_count == 1
    # Segunda llamada con misma password: debe servirse de la caché.
    check_hibp("hola", session=session, cache=cache)
    assert session.call_count == 1


def test_check_hibp_cache_compartida_entre_passwords_de_mismo_prefijo():
    """Dos passwords con prefijo SHA-1 distinto -> dos llamadas; con mismo prefijo -> una."""
    cache: dict[str, dict[str, int]] = {}
    session = _FakeSession(_build_response_for("hola", 1))
    check_hibp("hola", session=session, cache=cache)
    # Segunda con la misma password (mismo prefijo): no llama.
    check_hibp("hola", session=session, cache=cache)
    assert session.call_count == 1


# ---------------------------------------------------------------------------
# Errores -> HIBPError
# ---------------------------------------------------------------------------

def test_check_hibp_status_no_200_lanza_hibperror():
    session = _FakeSession(_FakeResponse("", status_code=503))
    with pytest.raises(HIBPError):
        check_hibp("hola", session=session)


def test_check_hibp_excepcion_de_red_se_envuelve_en_hibperror():
    session = _FakeSession(exc=requests.ConnectionError("DNS failure"))
    with pytest.raises(HIBPError):
        check_hibp("hola", session=session)


def test_check_hibp_timeout_se_envuelve_en_hibperror():
    session = _FakeSession(exc=requests.Timeout("read timeout"))
    with pytest.raises(HIBPError):
        check_hibp("hola", session=session, timeout=0.001)


# ---------------------------------------------------------------------------
# Integración real (omitido por defecto)
# ---------------------------------------------------------------------------

@pytest.mark.network
def test_check_hibp_integracion_password_es_pwned():
    """`'password'` debe aparecer en HIBP (es la #1 históricamente)."""
    found, count = check_hibp("password")
    assert found is True
    assert count > 0
