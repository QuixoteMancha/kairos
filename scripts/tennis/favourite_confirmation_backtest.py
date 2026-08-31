"""Backtest de "Favorito con confirmación de set 1" -- 2026-08-30, pedido
explícito del usuario: mejorar `BetOnFavourite` (que apuesta al favorito
pre-partido, sin usar datos en vivo) agregando un chequeo de confirmación
al cierre del set 1, reusando los mismos filtros de stats en vivo que
Closing Scalp.

ESPECIFICACIÓN (confirmada explícitamente por el usuario):

  1. Definición de "favorito": EXACTAMENTE la misma que ya usa
     `BetOnFavourite` (`strategies/bet_on_favourite.py`, reusado tal
     cual, no reinventado) -- gap de ranking por tour (ATP >= 20, WTA >=
     30, `_rank_difference_ok`), Y win% en superficie del favorito >
     underdog (`_player_surface_winpct`), Y forma reciente del favorito >
     underdog (`_player_recent_winpct`). Verificado que estas 3 funciones
     son 100% DB-only (`database.historical.tennis.queries.get_player_matches`,
     sin fallback de scraping en vivo pese a lo que sugiere un docstring
     desactualizado en `surface_form.py`) -- seguro llamarlas en un loop
     de backtest retrospectivo.
  2. Entrada: al precio de mercado más temprano disponible (primer
     `ProbabilityTick` real del partido) del lado favorito -- proxy de
     "apuesta pre-partido".
  3. Confirmación al CIERRE del set 1 -- CUALQUIERA de las 3 señales
     siguientes es suficiente para RETIRAR la apuesta (el usuario fue
     explícito: "cualquiera de las señales es suficiente", no hace falta
     que fallen todas):
       (a) El favorito NO ganó el set 1.
       (b) El gap de SPW del favorito al cierre del set 1 es < 8%.
       (c) El % de puntos ganados con 2do servicio del favorito al
           cierre del set 1 es <= 40%.
     Si NINGUNA de las 3 señales de alarma se dispara, se mantiene la
     apuesta hasta el resultado real. Si CUALQUIERA se dispara, se
     retira ahí mismo, al precio de mercado disponible en ese momento
     (el usuario: "si se retira la apuesta ya que ni sabemos qué pasará,
     aumenta la incertidumbre").

RE-CHEQUEO / BANKROLL: reusa `simulate_bankroll` de
`closing_scalp_backtest.py` tal cual (mismo shape de evento).

LIMITACIÓN HONESTA: el universo de este backtest es el MISMO ~179
partidos con punto-a-punto en vivo que Closing Scalp/Buy the Dip -- NO
se puede aprovechar el histórico de años de `BetOnFavourite` (que corre
sobre `TennisHistoricalOdds`/`MatchHistory`, sin datos en vivo), porque
la confirmación al cierre del set 1 requiere `LiveStatsSnapshot`, que
solo existe desde hace ~11 días. Muestra chica esperable, mismo
problema de siempre."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from database.db import init_db_historical
from database.historical.tennis.models import LiveStatsSnapshot, Match, MatchHistory
from outer_apis.sports.tennis.set_sequence_stats import parse_set_scores
from scripts.tennis.closing_scalp_backtest import (
    _collect_candidate_snapshots, _other, _point_snapshot_at, _price_for_side,
    _second_serve_pct, _spw_edge, simulate_bankroll,
)
from scripts.tennis.live_stats_market_reaction import MIN_SNAPSHOTS_PER_MATCH
from strategies.bet_on_favourite import (
    _player_recent_winpct, _player_surface_winpct, _rank_difference_ok,
)

MIN_SPW_EDGE_PCT = 8.0
WEAK_SECOND_SERVE_PCT = 40.0


def _is_favourite(history: MatchHistory | None, tour: str, surface: str | None,
                   side: str) -> bool | None:
    """Reusa EXACTAMENTE la definición de favorito de `BetOnFavourite` --
    gap de ranking por tour + win% en superficie + forma reciente, todas
    a favor de `side`. Fail-closed si falta cualquier dato."""
    if history is None:
        return None
    other = _other(side)
    rank_side = getattr(history, f"player_{side}_ranking")
    rank_other = getattr(history, f"player_{other}_ranking")
    if not _rank_difference_ok(rank_side, rank_other, tour):
        return False

    name_side = history.player_a.canonical_name if side == "a" else history.player_b.canonical_name
    name_other = history.player_b.canonical_name if side == "a" else history.player_a.canonical_name

    if not surface:
        return None
    win_side = _player_surface_winpct(name_side, surface, tour)
    win_other = _player_surface_winpct(name_other, surface, tour)
    if win_side is None or win_other is None or win_side <= win_other:
        return None if (win_side is None or win_other is None) else False

    recent_side = _player_recent_winpct(name_side, tour)
    recent_other = _player_recent_winpct(name_other, tour)
    if recent_side is None or recent_other is None:
        return None
    return recent_side > recent_other


@dataclass
class FavouriteConfirmationEvent:
    match_id: int
    side: str  # el favorito -- el que se apuesta
    entry_price: float
    entry_observed_at: datetime
    settled_at: datetime | None = None
    withdrawn: bool = False
    withdrawal_reason: str | None = None
    match_resolved: bool = False
    closer_won_match: bool | None = None  # compatible con simulate_bankroll
    exit_price: float | None = None
    match_return_pct: float | None = None


def _resolve_event(event: FavouriteConfirmationEvent, match: Match,
                    snaps: list[LiveStatsSnapshot], price_cache) -> None:
    side = event.side
    if not match.match_history_id:
        return
    sets = parse_set_scores(match.match_history.score)

    # Encuentra la transición al set 2 (cierre real del set 1).
    set1_end_snap = None
    for idx in range(1, len(snaps)):
        point_prev = _point_snapshot_at(match.id, snaps[idx - 1].probability_tick_id)
        point_curr = _point_snapshot_at(match.id, snaps[idx].probability_tick_id)
        set_prev = point_prev.current_set if point_prev else None
        set_curr = point_curr.current_set if point_curr else None
        if set_prev == 1 and set_curr is not None and set_curr > 1:
            set1_end_snap = snaps[idx]
            break
    if set1_end_snap is None:
        return  # no se pudo observar el cierre del set 1 -- fail-closed, no se resuelve

    reasons: list[str] = []
    if sets is not None and len(sets) >= 1:
        games_a, games_b = sets[0]
        games_side = games_a if side == "a" else games_b
        games_other = games_b if side == "a" else games_a
        if games_side <= games_other:
            reasons.append("perdio_set1")
    else:
        return  # sin score real parseable, fail-closed

    spw_edge_now = _spw_edge(set1_end_snap, side)
    if spw_edge_now is None or spw_edge_now < MIN_SPW_EDGE_PCT:
        reasons.append("spw_edge_debil")

    second_serve_now = _second_serve_pct(set1_end_snap, side)
    if second_serve_now is None or second_serve_now <= WEAK_SECOND_SERVE_PCT:
        reasons.append("segundo_servicio_debil")

    if reasons:
        event.withdrawn = True
        event.withdrawal_reason = ",".join(reasons)
        exit_price = _price_for_side(set1_end_snap.probability_tick_id, side, price_cache)
        if exit_price is None or event.entry_price in (None, 0.0):
            return
        event.match_resolved = True
        event.exit_price = exit_price
        event.match_return_pct = round(
            100.0 * (exit_price - event.entry_price) / event.entry_price, 2,
        )
        event.closer_won_match = event.match_return_pct > 0
        return

    if match.winner in ("a", "b"):
        event.match_resolved = True
        event.closer_won_match = (match.winner == side)
        event.exit_price = 1.0 if event.closer_won_match else 0.0
        if event.entry_price:
            if event.closer_won_match:
                event.match_return_pct = round(
                    100.0 * (1.0 - event.entry_price) / event.entry_price, 2,
                )
            else:
                event.match_return_pct = -100.0


def collect_favourite_confirmation_events() -> list[FavouriteConfirmationEvent]:
    init_db_historical()
    events: list[FavouriteConfirmationEvent] = []

    match_ids = [
        row.match_id for row in (
            LiveStatsSnapshot.select(LiveStatsSnapshot.match)
            .group_by(LiveStatsSnapshot.match)
            .having(__import__("peewee").fn.COUNT(LiveStatsSnapshot.id) >= MIN_SNAPSHOTS_PER_MATCH)
        )
    ]
    if not match_ids:
        return events

    for match_id in match_ids:
        match = Match.get_by_id(match_id)
        history = match.match_history if match.match_history_id else None
        if history is None:
            continue

        favourite_side = None
        for side in ("a", "b"):
            if _is_favourite(history, match.tour, match.surface, side) is True:
                favourite_side = side
                break
        if favourite_side is None:
            continue

        snaps, price_cache = _collect_candidate_snapshots(match_id)
        if not snaps:
            continue
        entry_price = _price_for_side(snaps[0].probability_tick_id, favourite_side, price_cache)
        if entry_price is None:
            continue

        event = FavouriteConfirmationEvent(
            match_id=match_id, side=favourite_side, entry_price=entry_price,
            entry_observed_at=snaps[0].observed_at,
            settled_at=match.settled_at or snaps[-1].observed_at,
        )
        _resolve_event(event, match, snaps, price_cache)
        events.append(event)
    return events


def run_favourite_confirmation_backtest() -> dict[str, Any]:
    events = collect_favourite_confirmation_events()
    resolved = [e for e in events if e.match_resolved]
    with_return = [e for e in resolved if e.match_return_pct is not None]
    withdrawn = [e for e in events if e.withdrawn]

    return {
        "n_eventos_favorito_identificado": len(events),
        "n_retiros_por_confirmacion_fallida": len(withdrawn),
        "n_resueltos": len(resolved),
        "win_rate_pct": (
            round(100 * sum(e.closer_won_match for e in resolved) / len(resolved), 1)
            if resolved else None
        ),
        "retorno_promedio_pct": (
            round(sum(e.match_return_pct for e in with_return) / len(with_return), 2)
            if with_return else None
        ),
    }


if __name__ == "__main__":
    import json
    events = collect_favourite_confirmation_events()
    print(json.dumps({
        "reporte_general": run_favourite_confirmation_backtest(),
        "simulacion_bankroll": simulate_bankroll(events),
    }, indent=2, ensure_ascii=False))
