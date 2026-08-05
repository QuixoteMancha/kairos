"""Registro/lectura de `MlbScannedMatch`/`MlbMatchProbabilityTick`
(2026-08-05, pedido explícito del usuario). Mismo patrón que
`database/historical/tennis/matchtracking.py` -- ver ese módulo para el
razonamiento completo (tracking de eventos PROPIOS del scan, separado de
`writer.py` que cubre datos fetcheados de fuentes externas).

Fail-closed por diseño, mismo criterio: cualquier excepción de DB se
traga (el registro/tracking es un side-effect, nunca debe tumbar el scan
real)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from database.db import init_db_historical
from database.historical.team_sports.mlbscannedmatch import MlbScannedMatch
from database.historical.team_sports.mlbmatchprobabilitytick import MlbMatchProbabilityTick


def _now():
    return datetime.now(timezone.utc)


def filter_new_mlb_markets(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Subconjunto de `markets` (shape de `build_team_sport_markets`,
    `type == 'baseball'`) cuyo `id` NO está todavía en `MlbScannedMatch`.
    Fail-closed: si la query falla, devuelve `markets` completo (todos
    "nuevos")."""
    try:
        init_db_historical()
        market_ids = [m.get("id") for m in markets if m.get("id")]
        if not market_ids:
            return []
        existing_ids = {
            r.market_id for r in
            MlbScannedMatch.select(MlbScannedMatch.market_id)
            .where(MlbScannedMatch.market_id.in_(market_ids))
        }
        return [m for m in markets if m.get("id") not in existing_ids]
    except Exception:
        return markets


def register_scanned_mlb_match(market: dict[str, Any]) -> None:
    """Registra UN partido de MLB detectado en el scan, idempotente por
    `market_id`. Espera el shape de
    `outer_apis/kalshi/markets/team_sport.py::build_team_sport_markets`."""
    market_id = market.get("id")
    ticker_a, ticker_b = market.get("ticker_a"), market.get("ticker_b")
    name_a, name_b = market.get("team_a_name"), market.get("team_b_name")
    if not (market_id and ticker_a and ticker_b and name_a and name_b):
        return  # fail-closed: dato mínimo indispensable ausente

    try:
        init_db_historical()
        exists = MlbScannedMatch.select().where(MlbScannedMatch.market_id == market_id).exists()
        if exists:
            return
        MlbScannedMatch.create(
            market_id=market_id, ticker_a=ticker_a, ticker_b=ticker_b,
            team_a_name=name_a, team_b_name=name_b,
            team_a_rating_at_match=market.get("team_a_rating"),
            team_b_rating_at_match=market.get("team_b_rating"),
            close_time=market.get("close_time"), detected_at=_now(),
        )
    except Exception:
        pass


def register_scanned_mlb_matches(markets: list[dict[str, Any]]) -> None:
    for market in markets:
        register_scanned_mlb_match(market)


def get_open_scanned_mlb_matches(max_age_days: float = 3.0) -> list[dict[str, Any]]:
    """Partidos MLB registrados que todavía no resolvieron (`winner`
    NULL), para que `workers/mlb_match_tick_worker.py` los recorra.
    Fail-closed: [] si falla la query."""
    try:
        init_db_historical()
        cutoff = _now() - timedelta(days=max_age_days)
        rows = (
            MlbScannedMatch
            .select()
            .where((MlbScannedMatch.winner.is_null()) & (MlbScannedMatch.detected_at >= cutoff))
        )
        return [
            {
                "market_id": r.market_id, "ticker_a": r.ticker_a, "ticker_b": r.ticker_b,
                "team_a_name": r.team_a_name, "team_b_name": r.team_b_name,
                "team_a_rating": r.team_a_rating_at_match, "team_b_rating": r.team_b_rating_at_match,
                "close_time": r.close_time,
            }
            for r in rows
        ]
    except Exception:
        return []


def save_mlb_probability_tick(market_id: str, market_price_a: float | None, model_fair_a: float | None) -> None:
    """Fail-closed: no hace nada si `market_id` no está registrado o si
    falla la escritura."""
    try:
        init_db_historical()
        match = MlbScannedMatch.get_or_none(MlbScannedMatch.market_id == market_id)
        if match is None:
            return
        MlbMatchProbabilityTick.create(
            match=match, observed_at=_now(), market_price_a=market_price_a, model_fair_a=model_fair_a,
        )
    except Exception:
        pass


def mark_mlb_match_settled(market_id: str, winner: str) -> None:
    """`winner`: 'a' | 'b'. Fail-closed."""
    if winner not in ("a", "b"):
        return
    try:
        init_db_historical()
        match = MlbScannedMatch.get_or_none(MlbScannedMatch.market_id == market_id)
        if match is None:
            return
        match.winner = winner
        match.settled_at = _now()
        match.save()
    except Exception:
        pass
