"""Consulta a Have I Been Pwned con k-anonymity.

API de HIBP usada: `GET https://api.pwnedpasswords.com/range/{prefix5}`,
descrita en https://haveibeenpwned.com/API/v3#PwnedPasswords. El cliente
envía únicamente los **5 primeros caracteres hex (uppercase)** del SHA-1
de la contraseña; el servidor responde con todos los sufijos (35 chars
restantes) que comparten ese prefijo, junto con su número de apariciones.
El cliente busca su sufijo concreto en la respuesta.

Esto se llama "k-anonymity": HIBP nunca recibe la contraseña ni su hash
completo, solo un prefijo que comparte ~500-600 contraseñas distintas.

Cabeceras enviadas:

- `Add-Padding: true` (cuando `add_padding=True`): pide a HIBP que
  inserte registros falsos en la respuesta para que el tamaño no
  filtre cuántos sufijos reales había. Recomendación oficial.
- `User-Agent`: identificador estable, configurable vía
  `HIBP_USER_AGENT` en `.env`. HIBP rechaza requests sin User-Agent.

Errores:
- `HIBPError` envuelve cualquier fallo de red, timeout, status != 200,
  respuesta malformada o prefijo inesperado. La función nunca propaga
  excepciones de `requests` directamente: el llamador (CLI/Report)
  decide si ignorar o mostrar el fallo sin interrumpir el resto del
  análisis.

Caché:
- Si se pasa `cache` (dict mutable), `check_hibp` lo usa como mapa
  `{prefix5: {suffix35: count}}`. Es **efímera** por convención: vive en
  memoria del proceso, se descarta al cerrar la CLI. Su propósito es
  evitar re-consultar HIBP varias veces durante una misma sesión
  (p. ej. cuando el usuario evalúa varias contraseñas con el mismo
  prefijo, o reintenta tras un typo).
"""
from __future__ import annotations

import hashlib

import requests

from passeval.normalize import normalize


class HIBPError(Exception):
    """Cualquier fallo al contactar HIBP o procesar la respuesta."""


def _password_sha1_hex_upper(password: str) -> str:
    return hashlib.sha1(normalize(password).encode("utf-8")).hexdigest().upper()


def _parse_range_response(text: str) -> dict[str, int]:
    """Parsea una respuesta de `/range/{prefix}` a `{suffix35: count}`.

    Cada línea tiene el formato `SUFFIX:COUNT` donde:
    - `SUFFIX` son 35 chars hex uppercase (sufijo del SHA-1).
    - `COUNT` es entero >= 0 (número de apariciones; los registros de
      padding inyectados por `Add-Padding: true` traen `count=0`).

    Líneas vacías y mal formadas se ignoran de forma silenciosa para
    tolerar variaciones del servidor (p. ej. `\r\n` vs `\n`).
    """
    out: dict[str, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        suffix, _, count_str = line.partition(":")
        if not suffix or not count_str:
            continue
        try:
            count = int(count_str)
        except ValueError:
            continue
        out[suffix.upper()] = count
    return out


def check_hibp(
    password: str,
    timeout: float = 10.0,
    cache: dict[str, dict[str, int]] | None = None,
    user_agent: str = "passeval-tfg/0.1",
    add_padding: bool = True,
    api_url: str = "https://api.pwnedpasswords.com/range",
    session: requests.Session | None = None,
) -> tuple[bool, int]:
    """Devuelve `(encontrada, count_apariciones)` consultando HIBP.

    Si la contraseña no aparece en HIBP devuelve `(False, 0)`. Si aparece
    devuelve `(True, count)` con el número de apariciones reportado.

    Detalle de la implementación:
    1. Calcula `SHA-1(NFC(password))`, prefijo de 5 hex y sufijo de 35.
    2. Si `cache` contiene el prefijo, no hace red.
    3. Si no, `GET {api_url}/{prefix}` con `Add-Padding` y `User-Agent`.
    4. Parsea la respuesta a `{suffix: count}` y la guarda en cache.
    5. Devuelve la entrada para el sufijo concreto (o `(False, 0)`).

    Cualquier error de red o respuesta inválida se envuelve en `HIBPError`.
    """
    digest_hex = _password_sha1_hex_upper(password)
    prefix, suffix = digest_hex[:5], digest_hex[5:]

    if cache is not None and prefix in cache:
        suffixes = cache[prefix]
    else:
        headers = {"User-Agent": user_agent}
        if add_padding:
            headers["Add-Padding"] = "true"
        url = f"{api_url}/{prefix}"
        try:
            client = session or requests
            resp = client.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            raise HIBPError(f"fallo de red al consultar HIBP: {exc}") from exc
        if resp.status_code != 200:
            raise HIBPError(
                f"HIBP devolvió status {resp.status_code} (esperado 200)"
            )
        try:
            suffixes = _parse_range_response(resp.text)
        except Exception as exc:  # pragma: no cover - defensa frente a parsing pathológico
            raise HIBPError(f"respuesta HIBP malformada: {exc}") from exc
        if cache is not None:
            cache[prefix] = suffixes

    count = suffixes.get(suffix, 0)
    return (count > 0, count)
