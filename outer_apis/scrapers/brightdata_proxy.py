"""
BrightData residential-proxy helper — rescued from the pre-"extreme
makeover" architecture (`src/core/brightdata_utils.py`, deleted commit
`02d7f35`), simplified and adapted. NOT tennis-specific (lives at
`outer_apis/scrapers/` root, not `outer_apis/scrapers/tennis/`) so any future scraper
that needs a residential proxy (e.g. other sports) can reuse it.

Reads credentials from `.env` (already present in this repo, real
values, not placeholders):
    BRIGHTDATA_PROXY        e.g. http://brd-customer-XXX-zone-ZONE:PASS@brd.superproxy.io:PORT
    BRIGHTDATA_API_TOKEN    for the zone-whitelist management API
    BRIGHTDATA_ZONE         optional, auto-detected from BRIGHTDATA_PROXY if unset

Simplification vs. the rescued version (documented honestly, not hidden):
the original had a full IP-whitelist auto-management system (add/remove
IPs from BrightData's zone whitelist on 407, with a module-level cache
and lock). This version keeps the CORE retry behavior (on a proxy-auth
failure, verify+refresh the whitelist, retry once with a fresh session)
but drops the old IP-removal bookkeeping (kept ADD-only, never removes a
previous IP) since that was mostly a whitelist-hygiene optimization, not
correctness-critical for a single-process scraper.
"""

import os
import threading
import time
from urllib.parse import urlparse

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_API_BASE = "https://api.brightdata.com"
_TIMEOUT_S = 12
_IP_SOURCES = [
    "https://api.ipify.org?format=json",
    "https://api4.my-ip.io/v2/ip.json",
    "https://ifconfig.me/ip",
]

# Status codes that suggest "the proxy's outbound IP isn't whitelisted for
# this zone" as opposed to 403, which is the DESTINATION site's own bot
# challenge -- never worth retrying via whitelist refresh.
_IP_AUTH_STATUS_CODES = (407,)

# 2026-08-11, pedido explícito del usuario: "los scrapers deben tener la
# característica de detectar cuando los sitios bloquean posiblemente por
# IP y en ese caso entrar al fallback de lanzar el request proxeado" --
# a diferencia de `_IP_AUTH_STATUS_CODES` (el PROXY diciendo "tu IP de
# salida no está whitelisteada"), esto es el SITIO DESTINO posiblemente
# bloqueando la IP directa del contenedor (403/429 típicos de rate-limit
# o bot-challenge por IP; 503 incluido porque varios WAF lo usan para lo
# mismo). No es una lista exhaustiva ni 100% certera -- un 403 real
# también podría ser "contenido no existe" en algunos sitios, pero el
# costo de un fallback de más (reintentar vía proxy sin necesidad) es
# bajo comparado con el de fallar silenciosamente cuando SÍ era un
# bloqueo real por IP.
_POSSIBLE_IP_BLOCK_STATUS_CODES = (403, 429, 503)

_lock = threading.Lock()
_whitelist_cache: dict[str, tuple[float, str]] = {}
_CACHE_TTL_S = 120.0


def get_brightdata_config() -> dict[str, str]:
    """Fail-closed: returns {} (not a dict of Nones) if BRIGHTDATA_PROXY
    isn't configured, so callers can cleanly skip proxy-dependent
    scraping instead of crashing on a missing credential."""
    proxy_url = os.getenv("BRIGHTDATA_PROXY", "")
    if not proxy_url:
        return {}
    return {
        "proxy_url": proxy_url,
        "api_token": os.getenv("BRIGHTDATA_API_TOKEN", ""),
        "zone": os.getenv("BRIGHTDATA_ZONE", "") or _zone_from_proxy_url(proxy_url),
    }


def make_proxy_session(impersonate: str = "chrome124"):
    """Returns a curl_cffi Session configured with a freshly-randomized
    BrightData residential session ID (rotates the exit IP within the
    zone's residential pool) and browser TLS/HTTP2 impersonation."""
    from curl_cffi import requests as curl_requests

    config = get_brightdata_config()
    if not config:
        raise RuntimeError("BRIGHTDATA_PROXY not configured in .env")

    parsed = urlparse(config["proxy_url"])
    session_id = os.urandom(4).hex()
    rotated_proxy = (
        f"{parsed.scheme}://{parsed.username}-session-{session_id}:{parsed.password}"
        f"@{parsed.hostname}:{parsed.port}"
    )
    session = curl_requests.Session(impersonate=impersonate)
    session.proxies = {"http": rotated_proxy, "https": rotated_proxy}
    return session


def request_via_proxy_with_ip_retry(make_session_fn, request_fn, *, verbose: bool = False):
    """Executes `request_fn(session)` against a session from
    `make_session_fn()`; if the response looks like an IP-whitelist auth
    failure (407, or a proxy-level connection exception -- never a 403,
    which is the destination site's own challenge and not fixable by
    whitelisting), refreshes the whitelist and retries ONCE with a fresh
    session.

    Returns whatever `request_fn(session)` returns (typically a curl_cffi
    Response), or None if unrecoverable / BrightData isn't configured.
    """
    config = get_brightdata_config()
    if not config:
        return None

    def _looks_like_ip_auth_issue(resp, exc) -> bool:
        if exc is not None:
            return True
        status = getattr(resp, "status_code", None)
        return status in _IP_AUTH_STATUS_CODES

    session = make_session_fn()
    resp, exc = None, None
    try:
        resp = request_fn(session)
    except Exception as e:
        exc = e

    if not _looks_like_ip_auth_issue(resp, exc):
        return resp

    if not config["api_token"] or not config["zone"]:
        if verbose:
            print("[brightdata_proxy] possible IP-auth issue but no api_token/zone configured, not retrying")
        return resp

    ensure_ip_whitelisted(config["api_token"], config["zone"], verbose=verbose)

    session2 = make_session_fn()
    try:
        return request_fn(session2)
    except Exception:
        return None


def fetch_with_ip_block_fallback(
    url: str, *, headers: dict | None = None, timeout: float = 15, verbose: bool = False,
):
    """Request DIRECTA primero (barata, sin proxy) -- si la respuesta
    parece un posible bloqueo por IP del SITIO destino
    (`_POSSIBLE_IP_BLOCK_STATUS_CODES`) o la request explota con una
    excepción de red, cae en fallback a `request_via_proxy_with_ip_retry`
    (que a su vez, si el PROXY responde con auth de IP no whitelisteada,
    whitelistea y reintenta -- 2 capas de fallback reales, cada una para
    un motivo de fallo distinto: sitio bloqueando la IP directa vs.
    BrightData rechazando la IP del proxy).

    2026-08-11, pedido explícito del usuario: "los scrapers deben tener
    la característica de detectar cuando los sitios bloquean posiblemente
    por IP y en ese caso entrar al fallback de lanzar el request
    proxeado... si es que el proxy responde unauthorized... se necesita
    whitelistear la IP". Genérico, sport-agnostic (mismo criterio que el
    resto de este módulo) -- cualquier scraper que hoy hace `requests.get`
    directo puede llamar esto en su lugar sin cambiar su propio manejo de
    error (mismo shape de respuesta: objeto con `.status_code`/`.text`,
    sea `requests.Response` de la request directa o `curl_cffi` Response
    del fallback proxeado).

    Returns: el objeto de respuesta (directo o proxeado) si CUALQUIERA de
    los 2 caminos tuvo éxito, o `None` si la request directa no pareció
    bloqueo por IP (se devuelve tal cual, incluso si es un 404/500 real
    -- eso NO es motivo de fallback a proxy) Y no hubo excepción; o si
    ambos caminos (directo bloqueado + proxy) fallan. Fail-closed: nunca
    inventa una respuesta."""
    headers = headers or {}
    resp, exc = None, None
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        exc = e

    if exc is None and resp.status_code not in _POSSIBLE_IP_BLOCK_STATUS_CODES:
        return resp  # ni bloqueo ni excepción -- respuesta real, se devuelve tal cual (200, 404, 500, lo que sea)

    if verbose:
        reason = f"status={resp.status_code}" if resp is not None else f"exception={exc}"
        print(f"[brightdata_proxy] posible bloqueo por IP en request directa ({reason}), fallback a proxy: {url}")

    def _do_request(session):
        return session.get(url, headers=headers, timeout=timeout, verify=False)

    proxy_resp = request_via_proxy_with_ip_retry(make_proxy_session, _do_request, verbose=verbose)

    # 2026-08-11, pedido explícito del usuario ("si no funciona con el
    # proxy... enviarme un correo") -- el sitio bloqueó la request
    # directa Y el fallback proxeado TAMBIÉN falló: chequeo reactivo del
    # estado de cuenta acá mismo (además del proactivo del watcher cada
    # 5 min), para no esperar hasta el próximo ciclo si el problema es
    # real (sin saldo/cuenta suspendida). `alert_if_account_broken` ya
    # tiene su propio cooldown (email_alert.py) -- no satura la bandeja
    # aunque esta rama se dispare en cada request fallida.
    if proxy_resp is None:
        config = get_brightdata_config()
        if config.get("api_token"):
            alert_if_account_broken(config["api_token"], verbose=verbose)

    return proxy_resp


def _zone_from_proxy_url(proxy_url: str) -> str:
    try:
        user_part = proxy_url.split("@")[0].split("//")[-1].split(":")[0]
        idx = user_part.find("-zone-")
        if idx != -1:
            return user_part[idx + len("-zone-"):]
    except Exception:
        pass
    return ""


def _get_public_ip() -> str | None:
    for url in _IP_SOURCES:
        try:
            resp = requests.get(url, timeout=_TIMEOUT_S)
            resp.raise_for_status()
        except requests.RequestException:
            continue
        try:
            data = resp.json()
            ip = data.get("ip") or data.get("ipAddress") or data.get("IP")
            if ip:
                return ip.strip()
        except ValueError:
            pass
        text = resp.text.strip()
        if text and len(text) <= 45:
            return text
    return None


def check_account_status(api_token: str, *, verbose: bool = False) -> dict | None:
    """`GET https://api.brightdata.com/status` -- 2026-08-11, pedido
    explícito del usuario ("si no funciona con el proxy o no hay dinero
    en el proxy, enviarme un correo"). Endpoint oficial real de
    BrightData (no adivinado) para chequear el estado de la CUENTA (no
    de una zona puntual) -- devuelve `can_make_requests` (bool) +
    `auth_fail_reason` (string, ej. "insufficient_balance",
    "account_suspended", "zone_not_found") cuando `can_make_requests` es
    `False`.

    Más confiable que inferir "sin saldo" a partir de un código HTTP
    genérico de un request de scraping cualquiera -- éste es el estado
    real reportado por BrightData mismo. Coincide con el incidente real
    ya documentado 2026-07-30 (`GET /zone` devolviendo "Customer has
    invalid status", 422 -- un problema de cuenta/facturación, no de
    whitelist) que en ese momento no disparaba ninguna alerta.

    Fail-closed: `None` si la request falla o la respuesta no es JSON
    válido -- nunca fabrica un estado."""
    try:
        resp = requests.get(
            f"{_API_BASE}/status",
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=_TIMEOUT_S,
        )
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        if verbose:
            print(f"[brightdata_proxy] check_account_status falló: {e}")
        return None


# 2026-08-11, pedido explícito del usuario: "si efectivamente se
# comprueba que no hay fondos, se deben interrumpir todas las
# operaciones". BrightData NO documenta públicamente el set completo de
# valores de `auth_fail_reason` (verificado -- su doc oficial solo
# muestra "zone_not_found" como ejemplo) -- este heurístico por
# palabras clave es lo mejor disponible sin esa referencia, NO una
# lista exhaustiva confirmada. Fail-SAFE hacia el lado correcto: un
# `auth_fail_reason` que NO matchea ninguna de estas palabras (ej. un
# problema de configuración de zona, no de plata) dispara el correo
# igual, pero NO frena las operaciones -- el freno global es una
# medida grave, reservada para cuando de verdad parece un problema de
# fondos/cuenta suspendida.
_NO_FUNDS_KEYWORDS = ("balance", "fund", "payment", "suspend", "billing", "credit", "insufficient")


def _looks_like_no_funds(reason: str) -> bool:
    reason_lower = reason.lower()
    return any(kw in reason_lower for kw in _NO_FUNDS_KEYWORDS)


def alert_if_account_broken(api_token: str, *, verbose: bool = False) -> bool:
    """Chequea `check_account_status` y envía un correo real
    (`outer_apis.email_alert.send_email_alert`) si la cuenta NO puede
    hacer requests -- 2026-08-11, pedido explícito del usuario. Si
    ADEMÁS el motivo real reportado (`auth_fail_reason`) parece de
    fondos/facturación (`_looks_like_no_funds`), activa el freno global
    de operaciones (`database.accounting.systemhaltstate.halt_operations`)
    -- "se deben interrumpir todas las operaciones, para evitar errores
    por trabajar con datos desactualizados". Si la cuenta vuelve a
    funcionar, levanta el freno automáticamente (`clear_halt`, solo si
    el motivo activo es exactamente este).

    `True` si se detectó un problema real (independientemente de si el
    correo se pudo enviar o si se frenó, ver esas funciones para su
    propio fail-closed). `False` si la cuenta está bien o no se pudo
    determinar su estado (fail-closed: sin certeza de que está roto, no
    se alarma NI se frena nada)."""
    status = check_account_status(api_token, verbose=verbose)
    if status is None:
        return False

    from database.accounting.systemhaltstate import clear_halt, halt_operations
    from outer_apis.email_alert import send_email_alert

    _HALT_REASON = "brightdata_account_broken"

    if status.get("can_make_requests", True):
        clear_halt(_HALT_REASON, verbose=verbose)
        return False

    reason = status.get("auth_fail_reason", "desconocida")
    no_funds = _looks_like_no_funds(reason)

    if no_funds:
        halt_operations(_HALT_REASON, verbose=verbose)

    send_email_alert(
        subject="BrightData proxy no disponible" + (" -- OPERACIONES DETENIDAS" if no_funds else ""),
        body=(
            f"El proxy de BrightData reporta que la cuenta NO puede hacer requests.\n\n"
            f"Motivo real reportado por BrightData (auth_fail_reason): {reason}\n\n"
            + (
                "El motivo parece de fondos/facturación -- se activó el FRENO GLOBAL "
                "de operaciones (el motor de scan no va a generar recomendaciones "
                "nuevas hasta que esto se resuelva y la cuenta vuelva a responder "
                "can_make_requests=true).\n\n"
                if no_funds else
                "El motivo NO parece de fondos (podría ser config de zona u otro "
                "problema) -- las operaciones NO se frenaron automáticamente, pero "
                "conviene revisar el dashboard de BrightData de todas formas.\n\n"
            )
            + "Revisar el dashboard de BrightData para más detalle y, si corresponde, recargar la cuenta."
        ),
        reason_key=_HALT_REASON,
        verbose=verbose,
    )
    return True


def ensure_ip_whitelisted(api_token: str, zone: str, *, verbose: bool = False) -> bool:
    """Fail-closed: any failure (can't get public IP, API error) returns
    False (no change made) rather than raising -- a whitelist refresh
    failure shouldn't crash the scraper, it just means the retry above
    won't help this time.

    Made PUBLIC (2026-07-30, was `_ensure_ip_whitelisted`) so
    `workers/brightdata_ip_watcher_worker.py` can call it proactively on a
    schedule, not just reactively from `request_via_proxy_with_ip_retry`
    on a failed request -- same rationale as the pre-rebuild
    `run_brightdata_ip_watcher_worker` (see commit `4c2ccd7`, rescued and
    adapted to this repo's current `workers/`-as-separate-container
    pattern instead of the old in-process-thread pattern)."""
    with _lock:
        current_ip = _get_public_ip()
        if not current_ip:
            return False

        cached = _whitelist_cache.get(zone)
        if cached and (time.time() - cached[0]) < _CACHE_TTL_S and cached[1] == current_ip:
            return False

        try:
            resp = requests.get(
                f"{_API_BASE}/zone",
                params={"zone": zone},
                headers={"Authorization": f"Bearer {api_token}"},
                timeout=_TIMEOUT_S,
            )
            zone_ips = resp.json().get("ips", []) if resp.status_code == 200 else []
        except (requests.RequestException, ValueError):
            zone_ips = []

        if current_ip in zone_ips:
            _whitelist_cache[zone] = (time.time(), current_ip)
            return False

        try:
            resp = requests.post(
                f"{_API_BASE}/zone/whitelist",
                params={"zone": zone},
                json={"ip": current_ip},
                headers={"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"},
                timeout=_TIMEOUT_S,
            )
            if resp.status_code in (200, 201, 204):
                _whitelist_cache[zone] = (time.time(), current_ip)
                if verbose:
                    print(f"[brightdata_proxy] whitelisted IP {current_ip} for zone={zone}")
                return True
        except requests.RequestException:
            pass
        return False
