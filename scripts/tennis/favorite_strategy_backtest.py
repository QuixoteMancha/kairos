"""
Backtest de la estrategia más simple posible: "apostar siempre al
favorito" (por ranking ACTUAL, ver caveat abajo), en TODAS las rondas
disponibles de Kalshi (no solo semifinales/finales) -- pedido explícito
del usuario 2026-08-02.

DEFINICIÓN DE "FAVORITO": el jugador con MEJOR (menor) ranking. Se usa
ranking en vez de precio de mercado (que sería el "favorito real" que el
libro cotiza) para evitar tener que traer el trade tape completo de
cada partido (costoso en tiempo -- cientos de tickers bajo rate-limit
de Kalshi) -- esto es una limitación real: el ranking no siempre
coincide con quién el mercado cotiza como favorito (ver el propio caso
Nakashima/Fritz de esta sesión, donde el mercado tenía a Nakashima como
favorito de precio pese a su ranking peor).

FUENTE: TODOS los mercados `status=settled` de `KXATPMATCH`+`KXWTAMATCH`
(mismo alcance de retención ~2 meses ya confirmado en
`scripts/sf_final_scan.py`), SIN filtrar por ronda esta vez.

Fail-closed: partidos sin ranking real de alguno de los 2 jugadores se
excluyen (nunca se asume un favorito por default)."""

from __future__ import annotations

import json
from typing import Any

from outer_apis.kalshi.client import KalshiAPI
from outer_apis.kalshi.markets.tennis import _extract_round, _extract_player_name
from scripts.tennis.sf_final_hypothesis_validation import _rank_lookup, _lookup_rank

_SERIES = ["KXATPMATCH", "KXWTAMATCH"]
_SERIES_TOUR = {"KXATPMATCH": "ATP", "KXWTAMATCH": "WTA"}


def _group_by_event(markets: list[dict]) -> dict[str, list[dict]]:
    by_event: dict[str, list[dict]] = {}
    for m in markets:
        event_ticker = (m.get("raw") or {}).get("event_ticker")
        if not event_ticker:
            continue
        by_event.setdefault(event_ticker, []).append(m)
    return by_event


def run_favorite_strategy_backtest() -> dict[str, Any]:
    api = KalshiAPI()
    api.initialize()

    rank_cache: dict[str, dict[str, int]] = {}
    cases = []
    excluded_no_rank = 0
    excluded_incomplete = 0
    excluded_no_winner = 0

    for series in _SERIES:
        markets = api.get_markets(status="settled", series_ticker=series, max_pages=50)
        by_event = _group_by_event(markets)
        tour = _SERIES_TOUR[series]
        if tour not in rank_cache:
            rank_cache[tour] = _rank_lookup(tour)

        for event_ticker, legs in by_event.items():
            if len(legs) != 2:
                excluded_incomplete += 1
                continue

            names = [_extract_player_name(leg["raw"]) for leg in legs]
            if not all(names):
                excluded_incomplete += 1
                continue

            result_a = (legs[0].get("raw") or {}).get("result")
            result_b = (legs[1].get("raw") or {}).get("result")
            winner = None
            if result_a == "yes" or result_b == "no":
                winner = names[0]
            elif result_b == "yes" or result_a == "no":
                winner = names[1]
            if winner is None:
                excluded_no_winner += 1
                continue

            rank_a = _lookup_rank(names[0], rank_cache[tour])
            rank_b = _lookup_rank(names[1], rank_cache[tour])
            if rank_a is None or rank_b is None:
                excluded_no_rank += 1
                continue

            favorite = names[0] if rank_a < rank_b else names[1]
            round_name = _extract_round(legs[0]["raw"]) or "unknown"

            cases.append({
                "event_ticker": event_ticker, "series": series, "round": round_name,
                "favorite": favorite, "favorite_rank": min(rank_a, rank_b),
                "underdog_rank": max(rank_a, rank_b), "winner": winner,
                "hit": winner == favorite,
            })

    n = len(cases)
    hits = sum(1 for c in cases if c["hit"])

    by_round: dict[str, dict[str, Any]] = {}
    for round_name, group in _group_by_key(cases, "round").items():
        g_n = len(group)
        g_hits = sum(1 for c in group if c["hit"])
        by_round[round_name] = {"n": g_n, "hits": g_hits, "hit_rate": round(g_hits / g_n, 4) if g_n else None}

    return {
        "total_evaluable": n,
        "excluded_no_rank": excluded_no_rank,
        "excluded_incomplete_event": excluded_incomplete,
        "excluded_no_winner": excluded_no_winner,
        "overall_hits": hits,
        "overall_hit_rate": round(hits / n, 4) if n else None,
        "by_round": by_round,
        "caveat": (
            "'Favorito' definido por RANKING actual, no por precio de "
            "mercado real -- distinto del favorito que el libro cotiza "
            "(ver caso real Nakashima/Fritz de esta sesión). Ranking "
            "actual de hoy, no histórico-al-momento del partido."
        ),
    }


def _group_by_key(items: list[dict], key: str) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(item[key], []).append(item)
    return groups


if __name__ == "__main__":
    result = run_favorite_strategy_backtest()
    print(json.dumps(result, indent=2, ensure_ascii=False))
