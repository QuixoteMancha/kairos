"""Registro/lectura de `TennisScannedMatch`/`TennisMatchProbabilityTick`
(2026-07-31, pedido explícito del usuario -- ver esas 2 clases para el
razonamiento completo). Separado de `writer.py` (que ya es grande y cubre
un dominio distinto: datos de calibración FETCHEADOS de fuentes externas)
porque esto es tracking de eventos PROPIOS del scan, un dominio distinto.

Fail-closed por diseño, mismo criterio que el resto del paquete: cualquier
excepción de DB se traga (logueada, no relanzada) -- el registro/tracking
es un side-effect, nunca debe tumbar el scan real."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.db import init_db_historical
from database.historical.tennis.tennisscannedmatch import TennisScannedMatch
from database.historical.tennis.tennismatchprobabilitytick import TennisMatchProbabilityTick


def _now():
    return datetime.now(timezone.utc)


def register_scanned_match(market: dict[str, Any]) -> None:
    """Registra UN partido de tenis detectado en el scan, sin importar si
    tiene edge -- idempotente por `market_id` (si ya existe, no hace
    nada; un partido se registra una sola vez, la primera vez que
    aparece). Espera el shape que produce
    `outer_apis/kalshi/markets/tennis.py::build_tennis_markets`."""
    market_id = market.get("id")
    ticker_a = market.get("ticker_a")
    ticker_b = market.get("ticker_b")
    tour = market.get("tour")
    name_a = market.get("player_a_name")
    name_b = market.get("player_b_name")
    best_of = market.get("best_of")
    if not (market_id and ticker_a and ticker_b and tour and name_a and name_b and best_of):
        return  # fail-closed: dato mínimo indispensable ausente, no se registra a medias

    try:
        init_db_historical()
        exists = TennisScannedMatch.select().where(TennisScannedMatch.market_id == market_id).exists()
        if exists:
            return
        TennisScannedMatch.create(
            market_id=market_id, ticker_a=ticker_a, ticker_b=ticker_b, tour=tour,
            surface=market.get("surface"), tournament=market.get("tournament"),
            player_a_name=name_a, player_b_name=name_b,
            player_a_rank_at_match=market.get("player_a_rank"),
            player_b_rank_at_match=market.get("player_b_rank"),
            best_of=best_of, close_time=market.get("close_time"), detected_at=_now(),
        )
    except Exception:
        pass


def register_scanned_matches(markets: list[dict[str, Any]]) -> None:
    for market in markets:
        register_scanned_match(market)


def get_open_scanned_matches(max_age_days: float = 3.0) -> list[dict[str, Any]]:
    """Partidos registrados que todavía no resolvieron (`winner` NULL),
    para que `workers/tennis_match_tick_worker.py` los recorra.

    `max_age_days`: partidos detectados hace más de esto se EXCLUYEN de
    la lista (no se siguen tickeando indefinidamente si por lo que sea
    nunca se observó una resolución real -- ver
    `close_stale_unresolved_matches` para el cierre explícito de esos
    casos). Fail-closed: [] si falla la query."""
    try:
        init_db_historical()
        cutoff = _now()
        from datetime import timedelta
        cutoff = cutoff - timedelta(days=max_age_days)
        rows = (
            TennisScannedMatch
            .select()
            .where((TennisScannedMatch.winner.is_null()) & (TennisScannedMatch.detected_at >= cutoff))
        )
        return [
            {
                "market_id": r.market_id, "ticker_a": r.ticker_a, "ticker_b": r.ticker_b,
                "tour": r.tour, "surface": r.surface,
                "player_a_name": r.player_a_name, "player_b_name": r.player_b_name,
                "player_a_rank": r.player_a_rank_at_match, "player_b_rank": r.player_b_rank_at_match,
                "best_of": r.best_of, "close_time": r.close_time,
            }
            for r in rows
        ]
    except Exception:
        return []


def save_probability_tick(market_id: str, market_price_a: float | None, model_fair_a: float | None) -> None:
    """Fail-closed: no hace nada si `market_id` no está registrado (nunca
    fabrica un TennisScannedMatch a partir de un tick suelto) o si falla
    la escritura."""
    try:
        init_db_historical()
        match = TennisScannedMatch.get_or_none(TennisScannedMatch.market_id == market_id)
        if match is None:
            return
        TennisMatchProbabilityTick.create(
            match=match, observed_at=_now(), market_price_a=market_price_a, model_fair_a=model_fair_a,
        )
    except Exception:
        pass


def mark_match_settled(market_id: str, winner: str) -> None:
    """`winner`: 'a' | 'b'. Fail-closed: no hace nada si `market_id` no
    está registrado, `winner` no es 'a'/'b', o falla la escritura --
    nunca fabrica un ganador."""
    if winner not in ("a", "b"):
        return
    try:
        init_db_historical()
        match = TennisScannedMatch.get_or_none(TennisScannedMatch.market_id == market_id)
        if match is None:
            return
        match.winner = winner
        match.settled_at = _now()
        match.save()
    except Exception:
        pass
