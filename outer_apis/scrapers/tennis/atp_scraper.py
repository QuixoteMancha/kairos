"""
ATP Tour official-site scraper (rankings HTML page).

Rescued from the pre-"extreme makeover" architecture
(`src/scrapers/atp_wta_scraper.py`, deleted commit `02d7f35`; original
archive commit `17d71b8`), re-verified live 2026-07-20 and adapted:

- The rankings page (https://www.atptour.com/en/rankings/singles) is
  behind Cloudflare bot-detection. CONFIRMED via direct testing during
  this rebuild that it is NOT a header-string problem: plain `requests`
  (or `httpx` even with HTTP/2 negotiated) gets a 403 EVEN with a full set
  of browser-like headers (`User-Agent`/`Accept`/`Accept-Language`/
  `Sec-Fetch-Mode`) — Cloudflare is fingerprinting the TLS
  ClientHello/HTTP2-frame ordering (JA3/JA4-style), not just headers,
  which `requests`/`httpx`'s stock TLS stack doesn't replicate. Using
  `curl_cffi` (`impersonate="chrome"`, which reproduces a real Chrome TLS
  fingerprint) gets a real 200 with the actual rankings table — this is
  the actual fix, requires the `curl_cffi` dependency (already present in
  this environment; `h2` was also installed for httpx's HTTP2 support
  during investigation but curl_cffi turned out to be the fix that works,
  h2 alone was insufficient).
- The old AJAX player-search fallback endpoint
  (`/en/-/ajax/playersearch/{query}`) is DEAD as of this rebuild (returns
  an "Object moved" 500 redirect regardless of client) — kept in the code
  path as a best-effort fallback (fails closed to None, doesn't raise) but
  documented as unreliable; the primary path (parsing the rankings HTML
  table, which covers the top ~200) is what should be relied on.

This module ONLY does raw HTTP + HTML parsing (no business logic, no
caching policy) — the public call surface lives in `outer_apis/tennis_rankings.py`
per the project's separation: scrapers/ = raw source access,
outer_apis/<facade>.py = call methods used by the rest of Kairos.
"""

import difflib
import re
from datetime import date
from typing import Any

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

_RANKINGS_URL = "https://www.atptour.com/en/rankings/singles"
# Dead as of 2026-07-20 (returns a 500 redirect) — kept as a best-effort
# fallback only, never relied upon as the primary path.
_SEARCH_URL = "https://www.atptour.com/en/-/ajax/playersearch/{query}"

_JSON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": _RANKINGS_URL,
}

_REQUEST_TIMEOUT_S = 15
_IMPERSONATE = "chrome"  # TLS/HTTP2 fingerprint to reproduce, see module docstring.


def _get(url: str, headers: dict | None = None) -> "curl_requests.Response | None":
    """Fail-closed: any network error or non-200 status -> None, never
    raises, never returns partial/garbage content as if it were valid.

    Uses curl_cffi (Chrome TLS/HTTP2 fingerprint impersonation) instead of
    plain `requests` — required to get past Cloudflare on atptour.com, see
    module docstring."""
    try:
        resp = curl_requests.get(
            url, headers=headers or {}, timeout=_REQUEST_TIMEOUT_S,
            allow_redirects=True, impersonate=_IMPERSONATE,
        )
    except curl_requests.RequestsError:
        return None
    if resp.status_code != 200:
        return None
    return resp


def _norm(name: str) -> str:
    import unicodedata

    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]", " ", ascii_str.lower()).strip()


def _fuzzy_match(target: str, candidates: list[str], cutoff: float = 0.72) -> str | None:
    t = _norm(target)
    normed = {_norm(c): c for c in candidates}
    matches = difflib.get_close_matches(t, list(normed.keys()), n=1, cutoff=cutoff)
    return normed[matches[0]] if matches else None


def _parse_rankings_html(html: str) -> list[dict]:
    """Parses the ATP rankings table. Structure verified live 2026-07-20:
    `<tr class="lower-row">` rows, player link `/en/players/{slug}/...`,
    country flag via `<svg><use href="#flag-xxx">`."""
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[dict] = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        rank_text = cells[0].get_text(strip=True)
        if not re.match(r"^\d+$", rank_text.strip()):
            continue
        link = row.select_one('a[href*="/en/players/"]')
        if not link:
            continue
        href = link.get("href", "")
        slug_m = re.search(r"/en/players/([^/]+)/", href)
        if not slug_m:
            continue
        slug = slug_m.group(1)
        full_name = " ".join(w.capitalize() for w in slug.split("-"))
        flag_el = row.select_one("svg use[href*='#flag-']")
        country = None
        if flag_el:
            flag_href = flag_el.get("href", "")
            country_m = re.search(r"#flag-([a-z]{3})", flag_href)
            if country_m:
                country = country_m.group(1).upper()
        candidates.append({"name": full_name, "ranking": int(rank_text), "pais": country})
    return candidates


def get_top_rankings(n: int = 200) -> list[dict[str, Any]]:
    """Top-N ATP singles players from the official rankings page.
    Fail-closed: any fetch/parse failure returns []."""
    url = f"{_RANKINGS_URL}?rankRange=1-{n}"
    resp = _get(url)
    if not resp:
        resp = _get(_RANKINGS_URL)
    if not resp:
        return []

    candidates = _parse_rankings_html(resp.text)
    result = []
    for c in candidates:
        if c["ranking"] > n:
            break
        result.append({
            "nombre": c["name"],
            "ranking": c["ranking"],
            "ranking_type": "ATP",
            "pais": c.get("pais"),
        })
        if len(result) >= n:
            break
    return result


def get_player_data(name: str, depth: int = 500) -> dict[str, Any] | None:
    """Fail-closed: returns None if the player isn't found in the top-`depth`
    rankings table or on any fetch/parse failure. Never fabricates a match.

    BUG FOUND AND FIXED 2026-07-20: this used to fetch `_RANKINGS_URL`
    without the `rankRange` query param, which silently returns only
    ~top-100 (confirmed live: 200 candidate rows parsed but max rank was
    100, i.e. duplicated rows within that same top-100 window) — any
    player ranked 101+ would incorrectly resolve to None here even though
    they're really ranked and findable with the right query param (as
    `get_top_rankings(n=...)` already correctly does). Now requests the
    same `rankRange=1-{depth}` the top-rankings path uses."""
    resp = _get(f"{_RANKINGS_URL}?rankRange=1-{depth}")
    if resp:
        candidates = _parse_rankings_html(resp.text)
        if candidates:
            match = _fuzzy_match(name, [c["name"] for c in candidates])
            if match:
                c = next(c for c in candidates if c["name"] == match)
                return {
                    "nombre": match,
                    "ranking": c["ranking"],
                    "ranking_type": "ATP",
                    "pais": c.get("pais"),
                    "_source": "atp_rankings_html",
                }

    # Best-effort fallback only — confirmed dead (500) as of this rebuild,
    # kept fail-closed (returns None on failure, never raises) in case it
    # comes back online.
    return _search_via_ajax(name)


def _search_via_ajax(name: str) -> dict[str, Any] | None:
    url = _SEARCH_URL.format(query=name.replace(" ", "+"))
    resp = _get(url, _JSON_HEADERS)
    if not resp:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    players = data if isinstance(data, list) else data.get("players", [])
    if not players:
        return None
    names = [p.get("FullName", p.get("name", "")) for p in players]
    match = _fuzzy_match(name, names)
    if not match:
        return None
    p = next(p for p in players if _norm(p.get("FullName", p.get("name", ""))) == _norm(match))
    return {
        "nombre": match,
        "ranking": _to_int(str(p.get("Ranking", p.get("ranking", "")))),
        "ranking_type": "ATP",
        "pais": p.get("CountryCode", p.get("country", None)),
        "edad": _age_from_dob(p.get("DateOfBirth", p.get("dob", ""))),
        "_source": "atp_ajax",
    }


def _to_int(s: str) -> int | None:
    m = re.search(r"\d+", str(s))
    return int(m.group()) if m else None


def _age_from_dob(dob: str) -> int | None:
    if not dob:
        return None
    m = re.search(r"(\d{4})", dob)
    if not m:
        return None
    return date.today().year - int(m.group(1))
