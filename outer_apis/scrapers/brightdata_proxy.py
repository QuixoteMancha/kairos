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

    _ensure_ip_whitelisted(config["api_token"], config["zone"], verbose=verbose)

    session2 = make_session_fn()
    try:
        return request_fn(session2)
    except Exception:
        return None


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


def _ensure_ip_whitelisted(api_token: str, zone: str, *, verbose: bool = False) -> bool:
    """Fail-closed: any failure (can't get public IP, API error) returns
    False (no change made) rather than raising -- a whitelist refresh
    failure shouldn't crash the scraper, it just means the retry above
    won't help this time."""
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
