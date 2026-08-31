"""Backtest de retorno simulado para la estrategia "Buy the Dip" --
2026-08-30, pedido explícito del usuario: contraparte simétrica de
Closing Scalp. En vez de confirmar dominancia estructural + de stats a
favor del que va GANANDO, esto confirma AUSENCIA de dominancia real del
que va ganando (paridad estructural pre-match + ventaja de stats débil)
y compra al que va PERDIENDO, esperando reversión a la media.

Reusa toda la infraestructura ya construida y verificada en
`closing_scalp_backtest.py` (detección de quiebre, SPW derivado, sets
ganados antes de la entrada, precio real por lado, motor de bankroll con
fees) -- no se duplica lógica ya probada, solo se invierte el criterio.

ESPECIFICACIÓN (todas las condiciones deben cumplirse, fail-closed):

  1. 2026-08-30, revisado (Lectura B confirmada por el usuario, NO es
     paridad simétrica): el que va PERDIENDO debe estar rankeado MÁS de
     `MIN_RANK_GAP_FAVORING_TRAILING` puestos MEJOR que el líder -- la
     señal es "el mejor jugador en el papel va perdiendo", no "están
     parejos". Depende de qué lado está perdiendo (`_rank_favors_trailing`),
     ya no es un chequeo simétrico a nivel de partido. Por separado, gap
     de Elo relativo < `MAX_ELO_GAP_RELATIVE` SIGUE siendo simétrico
     (superficie con fallback a general) -- "Elo similar" = proxy de
     forma reciente confirmado por el usuario, no cambia con el ranking.
  2. Disparador de "va perdiendo" -- dos casos alternativos, NINGUNO
     espera confirmación del rival (a diferencia de Closing Scalp que sí
     espera el "hold"):
       (a) Quiebre reciente en contra -- dispara con el quiebre en sí
           (`_break_delta_side`), sin esperar que el que rompió sostenga.
       (b) Ya perdió el set anterior completo -- se evalúa en la
           transición al set siguiente (`sets_at_entry - 1` set(s) ya
           perdidos), calculable en vivo desde sets YA TERMINADOS
           (`_sets_won_before_entry`, mismo helper que Closing Scalp).
  3. NO se opera en el set decisivo/final (`current_set == match.best_of`)
     -- lo inverso exacto del filtro de Closing Scalp: acá se necesita
     que quede partido por jugar para el comeback, no que se esté
     cerrando.
  4. Ventaja débil del que va ganando (evidencia de que su dominio no es
     real): 2do servicio del que gana < `WEAK_LEADER_SECOND_SERVE_PCT` Y
     su SPW es menor o igual al del que va perdiendo (SPW edge del líder
     <= `MAX_LEADER_SPW_EDGE`, puede ser negativo si el que pierde
     domina más en stats pese al marcador).
  5. Precio de entrada (comprando al que va perdiendo) >= `MIN_ENTRY_PRICE`
     -- no se compra por debajo de ese piso.

TAKE PROFIT -- 2026-08-30, pedido explícito del usuario: NO es un TP fijo
que dispara apenas se toca +10pp. Se deja correr la posición (puede subir
más), y solo se asegura la ganancia cuando, EN EL RETROCESO desde el
pico alcanzado, el precio vuelve a `entry_price + TAKE_PROFIT_PP` Y las
stats en ese momento CONFIRMAN el retroceso (el líder amplió su SPW edge
por encima del que tenía al momento de la entrada, o su 2do servicio dejó
de ser "débil" -- ya no cumple la condición 4 de arriba). Si el precio
retrocede a +10pp SIN esa confirmación de stats, no se toma profit, se
sigue sosteniendo la posición hasta el resultado real del partido.

RE-CHEQUEO / RESULTADO / BANKROLL: mismo criterio que Closing Scalp --
nivel SET solo reporta precisión de señal (Kalshi no tiene mercado de
set), nivel PARTIDO simula retorno real. `simulate_bankroll` de
`closing_scalp_backtest.py` se reusa TAL CUAL (mismo shape de evento,
campos compatibles) -- no se reimplementa el motor de bankroll/fees.

LIMITACIÓN HEREDADA: mismo problema de tiebreaks no verificados
(0 casos observados en producción) que Closing Scalp -- v1 excluye TBs
por el mismo mecanismo indirecto (detección de quiebre vía stats
acumuladas, no vía notación de puntos cruda)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from database.db import init_db_historical
from database.historical.tennis.models import LiveStatsSnapshot, Match, MatchHistory
from outer_apis.sports.tennis.set_sequence_stats import parse_set_scores
from scripts.tennis.closing_scalp_backtest import (
    _collect_candidate_snapshots, _games_by_set, _other, _point_snapshot_at,
    _price_for_side, _second_serve_pct, _sets_won_before_entry, _spw_edge,
    simulate_bankroll,
)
from scripts.tennis.live_stats_market_reaction import MIN_SNAPSHOTS_PER_MATCH, _break_delta_side

MIN_RANK_GAP_FAVORING_TRAILING = 20  # 2026-08-30, pedido explícito del
# usuario -- YA NO es un tope simétrico ("gap chico sin importar quién es
# mejor"). Ahora es un PISO direccional: el que va PERDIENDO tiene que
# estar rankeado MÁS DE 20 puestos mejor que el líder (Lectura B
# confirmada: "el mejor jugador en el papel va perdiendo" es la señal,
# no "están parejos"). Depende de qué lado está perdiendo -- ya no es un
# chequeo simétrico a nivel de partido, se evalúa por lado candidato.
MAX_ELO_GAP_RELATIVE = 0.10
WEAK_LEADER_SECOND_SERVE_PCT = 40.0
MAX_LEADER_SPW_EDGE = 0.0  # el líder NO debe tener ventaja de SPW real
MIN_ENTRY_PRICE = 0.20
TAKE_PROFIT_PP = 0.10  # 2026-08-30, pedido explícito del usuario


def _elo_parity(history: MatchHistory | None) -> bool | None:
    """True si el gap de Elo relativo es chico ("forma reciente similar",
    confirmado por el usuario) -- SIMÉTRICO, no depende de quién va
    ganando/perdiendo, a diferencia del ranking. Fail-closed si falta
    cualquier dato."""
    if history is None:
        return None
    ea = history.player_a_surface_elo or history.player_a_elo
    eb = history.player_b_surface_elo or history.player_b_elo
    if ea is None or eb is None or (ea + eb) <= 0:
        return None
    relative_gap = abs(ea - eb) / ((ea + eb) / 2)
    return relative_gap < MAX_ELO_GAP_RELATIVE


def _rank_favors_trailing(history: MatchHistory | None, trailing_side: str) -> bool | None:
    """True si el que va PERDIENDO (`trailing_side`) está rankeado MÁS DE
    `MIN_RANK_GAP_FAVORING_TRAILING` puestos MEJOR (número más bajo) que
    el líder -- 2026-08-30, Lectura B confirmada por el usuario: la señal
    es que el mejor jugador en el papel va perdiendo, no que estén
    parejos. Fail-closed si falta el ranking de cualquiera de los dos."""
    if history is None:
        return None
    leader_side = _other(trailing_side)
    r_trailing = getattr(history, f"player_{trailing_side}_ranking")
    r_leader = getattr(history, f"player_{leader_side}_ranking")
    if r_trailing is None or r_leader is None:
        return None
    return (r_leader - r_trailing) > MIN_RANK_GAP_FAVORING_TRAILING


@dataclass
class BuyTheDipEvent:
    match_id: int
    side: str  # el que va PERDIENDO -- el que se compra
    trigger: str  # "break" o "lost_previous_set"
    entry_price: float
    entry_observed_at: datetime
    leader_spw_edge: float
    leader_second_serve_pct: float
    current_set_at_entry: int | None
    settled_at: datetime | None = None
    set_outcome_known: bool | None = None
    dip_won_set: bool | None = None
    match_resolved: bool = False
    closer_won_match: bool | None = None  # nombre compatible con simulate_bankroll
    exit_price: float | None = None
    match_return_pct: float | None = None
    take_profit_triggered: bool = False


def _resolve_event(
    event: BuyTheDipEvent, match: Match, snaps: list[LiveStatsSnapshot],
    price_cache: dict[int, tuple[float | None, float | None]], entry_index: int,
) -> None:
    side = event.side
    leader_side = _other(side)

    # 2026-08-30, pedido explícito del usuario -- TP de +10pp de
    # probabilidad, PERO con confirmación de stats (Lectura B: no dispara
    # apenas se toca +10pp, deja correr hasta un pico y solo asegura la
    # ganancia cuando, EN EL RETROCESO desde ese pico, el precio vuelve a
    # +10pp Y las stats confirman que el líder amplió su ventaja o dejó
    # de ser "débil" -- si el retroceso ocurre SIN esa confirmación de
    # stats, no se toma profit, se sigue sosteniendo la posición).
    peak_price = event.entry_price
    for snap in snaps[entry_index + 1:]:
        price = _price_for_side(snap.probability_tick_id, side, price_cache)
        if price is not None:
            peak_price = max(peak_price, price)
        retraced_from_peak = (
            price is not None
            and (peak_price - event.entry_price) >= TAKE_PROFIT_PP
            and price <= event.entry_price + TAKE_PROFIT_PP
        )
        if not retraced_from_peak:
            continue
        leader_edge_now = _spw_edge(snap, leader_side)
        leader_second_serve_now = _second_serve_pct(snap, leader_side)
        stats_confirm = (
            (leader_edge_now is not None and leader_edge_now > event.leader_spw_edge)
            or (leader_second_serve_now is not None
                and leader_second_serve_now >= WEAK_LEADER_SECOND_SERVE_PCT)
        )
        if stats_confirm:
            event.take_profit_triggered = True
            event.match_resolved = True
            event.closer_won_match = True
            event.exit_price = event.entry_price + TAKE_PROFIT_PP
            event.match_return_pct = round(
                100.0 * TAKE_PROFIT_PP / event.entry_price, 2,
            )
            break

    if event.take_profit_triggered:
        return  # ya se resolvió con el TP, no se pisa con el resultado real

    if event.current_set_at_entry is not None and match.match_history_id:
        sets = parse_set_scores(match.match_history.score)
        idx = event.current_set_at_entry - 1
        if sets is not None and 0 <= idx < len(sets):
            games_a, games_b = sets[idx]
            games_side = games_a if side == "a" else games_b
            games_other = games_b if side == "a" else games_a
            if games_side != games_other:
                event.set_outcome_known = True
                event.dip_won_set = games_side > games_other

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


def collect_buy_the_dip_events() -> list[BuyTheDipEvent]:
    init_db_historical()
    events: list[BuyTheDipEvent] = []

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
        if _elo_parity(history) is not True:
            continue
        snaps, price_cache = _collect_candidate_snapshots(match_id)

        for i in range(1, len(snaps)):
            candidates: list[tuple[str, str]] = []  # (side_perdiendo, trigger)

            breaker = _break_delta_side(snaps[i], snaps[i - 1])
            if breaker is not None:
                candidates.append((_other(breaker), "break"))

            point_i = _point_snapshot_at(match_id, snaps[i].probability_tick_id)
            point_prev = _point_snapshot_at(match_id, snaps[i - 1].probability_tick_id)
            set_i = point_i.current_set if point_i else None
            set_prev = point_prev.current_set if point_prev else None
            if set_i is not None and set_prev is not None and set_i > set_prev and point_i is not None:
                for side in ("a", "b"):
                    won_before = _sets_won_before_entry(point_i, side, set_i)
                    other_won_before = _sets_won_before_entry(point_i, _other(side), set_i)
                    if won_before == 0 and other_won_before is not None and other_won_before > 0:
                        candidates.append((side, "lost_previous_set"))

            for trailing_side, trigger in candidates:
                leader_side = _other(trailing_side)
                if _rank_favors_trailing(history, trailing_side) is not True:
                    continue

                current_set = set_i
                if current_set is None or current_set == match.best_of:
                    continue  # no se opera en el set decisivo

                entry_price = _price_for_side(snaps[i].probability_tick_id, trailing_side, price_cache)
                if entry_price is None or entry_price < MIN_ENTRY_PRICE:
                    continue

                leader_second_serve = _second_serve_pct(snaps[i], leader_side)
                if leader_second_serve is None or leader_second_serve >= WEAK_LEADER_SECOND_SERVE_PCT:
                    continue
                leader_spw_edge = _spw_edge(snaps[i], leader_side)
                if leader_spw_edge is None or leader_spw_edge > MAX_LEADER_SPW_EDGE:
                    continue

                event = BuyTheDipEvent(
                    match_id=match_id, side=trailing_side, trigger=trigger,
                    entry_price=entry_price, entry_observed_at=snaps[i].observed_at,
                    leader_spw_edge=round(leader_spw_edge, 2),
                    leader_second_serve_pct=leader_second_serve,
                    current_set_at_entry=current_set,
                    settled_at=match.settled_at or snaps[-1].observed_at,
                )
                _resolve_event(event, match, snaps, price_cache, i)
                events.append(event)
    return events


def run_buy_the_dip_backtest() -> dict[str, Any]:
    events = collect_buy_the_dip_events()
    n_matches = len({e.match_id for e in events})

    resolved = [e for e in events if e.match_resolved]
    with_return = [e for e in resolved if e.match_return_pct is not None]
    with_set_outcome = [e for e in events if e.set_outcome_known]
    take_profit_hits = [e for e in events if e.take_profit_triggered]
    by_trigger: dict[str, int] = {}
    for e in events:
        by_trigger[e.trigger] = by_trigger.get(e.trigger, 0) + 1

    return {
        "n_matches_con_snapshots_suficientes": n_matches,
        "n_eventos_entrada_filtrados": len(events),
        "eventos_por_disparador": by_trigger,
        "n_con_take_profit_disparado": len(take_profit_hits),
        "nivel_set__n_con_resultado_conocido": len(with_set_outcome),
        "nivel_set__win_rate_pct": (
            round(100 * sum(e.dip_won_set for e in with_set_outcome) / len(with_set_outcome), 1)
            if with_set_outcome else None
        ),
        "nivel_partido__n_resueltos": len(resolved),
        "nivel_partido__win_rate_pct": (
            round(100 * sum(e.closer_won_match for e in resolved) / len(resolved), 1)
            if resolved else None
        ),
        "nivel_partido__n_con_retorno_calculado": len(with_return),
        "nivel_partido__retorno_promedio_pct": (
            round(sum(e.match_return_pct for e in with_return) / len(with_return), 2)
            if with_return else None
        ),
    }


if __name__ == "__main__":
    import json
    events = collect_buy_the_dip_events()
    print(json.dumps({
        "reporte_general": run_buy_the_dip_backtest(),
        "simulacion_bankroll_nivel_partido": simulate_bankroll(events),
    }, indent=2, ensure_ascii=False))
