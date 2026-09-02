"""Builds "paired" tennis SET-winner markets (player A vs player B for a
single set, not the whole match) from Kalshi's `KXATPSETWINNER`/
`KXWTASETWINNER` series — 2026-09-01, pedido explícito del usuario:
Closing Scalp hoy solo entra cuando el candidato saca para GANAR EL
PARTIDO; se quiere ampliar a "saca para ganar UN SET" (no
necesariamente el que cierra el partido), y se confirmó en vivo contra
la API pública de Kalshi que existe mercado real y tradeable para eso.

STRUCTURAL FINDING (verified live 2026-09-01, same pairing shape as
`outer_apis/kalshi/markets/tennis.py::build_tennis_markets` for match
winners, but ONE EVENT PER SET instead of one event per match):
  - `event_ticker` example: `KXATPSETWINNER-26SEP01VALMON-4` — the
    trailing `-4` is the set number.
  - Two per-player leg tickers per event, same suffix convention as the
    match series: `...-VAL` / `...-MON`.
  - `title`: "Will {player} win set {N} in {A} vs {B} match".
  - `yes_sub_title`/`rules_secondary` carry the same fields as the match
    series (player name, tournament/round).
  - When a set ends, that event/market settles and a NEW event appears
    for the next set number — a best-of-3 match can produce up to 3
    KXATPSETWINNER events over its life, one active at a time.

This module ONLY pairs+enriches raw market data (mirrors
`build_tennis_markets`'s structure deliberately, including its
fail-closed discipline) — no arb/strategy logic here.
"""

import re
import unicodedata
from typing import Any

from outer_apis.scrapers.tennis.tournament_surface import surface_from_rules_secondary, extract_tournament_name

# Kalshi series tickers for ATP/WTA per-set winner markets (verified live
# 2026-09-01 against `KXATPSETWINNER-26SEP01VALMON-4`). Structurally
# analogous to `KXATPMATCH`/`KXWTAMATCH` (see tennis.py) but one event
# per SET, not per match.
_SERIES_TOUR = {
    "KXATPSETWINNER": "ATP",
    "KXWTASETWINNER": "WTA",
}

_TITLE_RE = re.compile(r"^Will (.+?) win set \d+ in .+ match\?$")
_TITLE_SET_NUMBER_RE = re.compile(r"win set (\d+) in", re.IGNORECASE)
# event_ticker shape: `{SERIES}-{DATE}{PLAYERS}-{SET_NUMBER}`, ej.
# `KXATPSETWINNER-26SEP01VALMON-4`. The set number is the trailing
# numeric segment after the last '-'.
_EVENT_SET_NUMBER_RE = re.compile(r"-(\d+)$")

_ROUND_RE = re.compile(r":\s*([\w\s]+?)\s*match\?\s*$")
_ROUND_FALLBACK_RE = re.compile(
    r"\b(Final|Semifinal|Quarterfinal|Round of \d+)\b", re.IGNORECASE,
)


def _series_for_event(event_ticker: str | None) -> str | None:
    if not event_ticker:
        return None
    for series in _SERIES_TOUR:
        if event_ticker.startswith(series):
            return series
    return None


def _extract_set_number_from_event(event_ticker: str) -> int | None:
    """Fail-closed: None if the event ticker doesn't end in a numeric
    segment (never guesses the set number)."""
    m = _EVENT_SET_NUMBER_RE.search(event_ticker)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _extract_set_number_from_title(title: str) -> int | None:
    m = _TITLE_SET_NUMBER_RE.search(title or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _extract_player_name(raw_market: dict) -> str | None:
    """Same fail-closed extraction as `tennis.py::_extract_player_name`:
    `yes_sub_title` first, title-regex fallback."""
    sub_title = raw_market.get("yes_sub_title")
    if sub_title:
        return sub_title.strip()
    title = raw_market.get("title") or ""
    m = _TITLE_RE.match(title.strip())
    return m.group(1).strip() if m else None


def _extract_round(raw_market: dict) -> str | None:
    title = (raw_market.get("title") or "").strip()
    m = _ROUND_RE.search(title)
    if m:
        return m.group(1).strip()
    rules_secondary = raw_market.get("rules_secondary") or ""
    m = _ROUND_FALLBACK_RE.search(rules_secondary)
    return m.group(1).strip() if m else None


def norm_player_name(name: str) -> str:
    """Normalización de nombre reusable por el worker que resuelve el
    evento de set activo contra un `Match` ya trackeado (mismo criterio
    de `tennis.py::_norm`, expuesto acá porque este módulo es el punto
    de entrada natural para ese matching)."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]", " ", ascii_str.lower()).strip()


def build_tennis_set_markets(raw_markets: list[dict]) -> list[dict[str, Any]]:
    """Pairs per-player legs into combined tennis SET-winner markets.

    Args:
        raw_markets: normalized markets from `KalshiAPI.get_markets`/
            `get_markets_by_tickers` (must include `raw` with
            `event_ticker`, `title`, `yes_sub_title`) for the
            `KXATPSETWINNER`/`KXWTASETWINNER` series.

    Returns:
        List of dicts: {id (event_ticker), set_number, tour, tournament,
        round, surface, player_a_name, player_b_name, ticker_a, ticker_b,
        current_a_price, current_b_price, close_time}.

        Fail-closed at every step, same discipline as
        `build_tennis_markets`: an event without exactly 2 legs, without
        both player names, or without a resolvable set number is
        SKIPPED, never guessed.
    """
    by_event: dict[str, list[dict]] = {}
    for market in raw_markets:
        raw = market.get("raw", {})
        event_ticker = raw.get("event_ticker")
        series = _series_for_event(event_ticker)
        if series is None or event_ticker is None:
            continue
        by_event.setdefault(event_ticker, []).append(market)

    result: list[dict[str, Any]] = []
    for event_ticker, legs in by_event.items():
        if len(legs) != 2:
            continue  # fail-closed: only pair complete 2-leg events.

        series = _series_for_event(event_ticker)
        tour = _SERIES_TOUR[series]

        names = [_extract_player_name(leg["raw"]) for leg in legs]
        if not all(names):
            continue  # fail-closed: can't pair without both names.

        set_number = _extract_set_number_from_event(event_ticker)
        if set_number is None:
            # Fallback: derive it from the title, same info repeated
            # there ("win set {N} in ..."), never guessed if both fail.
            set_number = _extract_set_number_from_title(legs[0]["raw"].get("title") or "")
        if set_number is None:
            continue

        rules_secondary = legs[0]["raw"].get("rules_secondary")
        surface = surface_from_rules_secondary(rules_secondary)
        tournament = extract_tournament_name(rules_secondary)
        round_name = _extract_round(legs[0]["raw"])

        result.append({
            "id": event_ticker,
            "type": "tennis_set_winner",
            "set_number": set_number,
            "tournament": tournament,
            "round": round_name,
            "current_a_price": legs[0]["current_a_price"],
            "current_b_price": legs[1]["current_a_price"],
            "ticker_a": legs[0].get("ticker", legs[0].get("id")),
            "ticker_b": legs[1].get("ticker", legs[1].get("id")),
            "player_a_name": names[0],
            "player_b_name": names[1],
            "tour": tour,
            "surface": surface,
            "close_time": legs[0].get("close_time"),
        })

    return result
