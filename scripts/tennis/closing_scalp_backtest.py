"""Backtest de retorno simulado para la estrategia Closing Scalp -- 2026-08-28,
especificación cerrada tras varias rondas de discusión con el usuario y
contrastada contra el apunte real de Obsidian (`Closing Scalping
(Tennis).md`). Reusa la detección de Break+Hold ya construida en
`live_stats_market_reaction.py` (Fase 2) y le agrega las capas de filtro
de entrada + stop-loss + simulación de retorno real que pidió el usuario.

ESPECIFICACIÓN (todas las condiciones de entrada deben cumplirse, fail-
closed -- si falta cualquier dato necesario, el evento se descarta, nunca
se asume ni se interpola):

  1. Entrada justo antes de que el candidato saque para ganar el partido.
     Se aceptan dos secuencias observables en el punto-a-punto real:
     (a) break y saque inmediato para partido; (b) break, hold confirmado,
     juego de saque del rival y nuevo saque del candidato para partido.
     No hay verificación empírica
     todavía de la notación de tiebreak en el feed de Flashscore (0 TBs
     observados en producción al 2026-08-27) -- el detector de
     break/hold vía stats acumuladas (`break_points_won`) no debería
     dispararse dentro de un TB de todos modos, pero esto NO está
     verificado contra un caso real. V1 explícitamente NO intenta
     operar tiebreaks (decisión del usuario, más restrictiva que la
     nota original que sí los permite condicionalmente).
  2. SPW derivado (Opción A: `first_serve_pct/100 * first_serve_won_pct
     + (1 - first_serve_pct/100) * second_serve_won_pct`, misma escala
     0-100 que el resto de las columnas de porcentaje) con ventaja
     >= `MIN_SPW_EDGE_PCT` puntos porcentuales a favor del que cierra.
  3. Segundo servicio ganado del que cierra >= `MIN_SECOND_SERVE_PCT`
     (si ya está crítico en el momento de la consolidación, no se entra).
     Las condiciones 2 y 3 deben cumplirse tanto en el acumulado del
     partido como en la sección Flashscore del set de entrada: un set
     anterior fuerte no puede ocultar deterioro en el set decisivo.
  4. Ventaja estructural pre-match: ranking MEJOR (número más bajo) Y
     Elo MAYOR (superficie si está disponible para AMBOS jugadores,
     si no cae a Elo general -- nunca se mezclan escalas, superficie
     para uno y general para el otro) a favor del que cierra. Si falta
     cualquiera de los dos (ranking o Elo en la escala elegida) para
     cualquiera de los dos jugadores, el evento se descarta.
  5. Además, la entrada debe ocurrir en un set que, de ganarlo, CIERRA
     el partido --
     `_is_match_closing_set()` cubre dos casos, ambos calculables en vivo
     sin sesgo de anticipación:
       (a) el set de la entrada es el que matemáticamente TIENE que ser
           el último posible (`current_set == match.best_of`, ej. set 3
           de un partido a mejor-de-3), o
       (b) el que cierra YA ganó suficientes sets ANTERIORES a este como
           para que ganar el actual cierre el partido (ej. va 1-0 en
           sets en un partido a mejor-de-3, entra al set 2) -- derivado
           del punto-a-punto crudo de sets YA TERMINADOS antes de la
           entrada (`_sets_won_before_entry`), nunca del resultado final.
     Ver `ClosingScalpEvent.is_final_set_of_match` para la versión
     retrospectiva (con sesgo de anticipación) que se descartó como
     filtro -- queda solo como curiosidad de análisis post-hoc.

RE-CHEQUEO al momento de evaluar el cierre (por separado para set y para
partido): la ventaja de SPW sigue >= `MIN_SPW_EDGE_PCT` en el snapshot
más cercano al momento de cierre.

STOP-LOSS -- 2026-08-28, pedido explícito del usuario: se ELIMINA el
stop-loss de degradación de 2do servicio que existía en la versión
anterior -- el 2do servicio queda SOLO como filtro de ENTRADA (condición
3 de arriba), no como disparador de salida. No hay ningún stop-loss
implementado en esta versión: toda posición se liquida contra el
resultado real (ganó/perdió). El stop-loss por MARCADOR DE PUNTOS
(0:30/30:15 en el juego de cierre) que se había discutido tampoco se
implementó nunca -- requiere parsear la notación cruda de puntos
("X:Y" -- servidor:restador vs. A:B fijo), que no está verificada contra
ningún caso real todavía (mismo problema no resuelto que la detección de
tiebreak).

UNIDAD DE ANÁLISIS -- dos niveles, con significado financiero DISTINTO:
  - Por PARTIDO: Kalshi sí tiene mercado tradeable ("quién gana el
    partido") -- acá se simula un retorno real: comprar YES al precio
    de entrada (`market_price_a`/`market_price_b` del lado que cierra),
    liquidar a 1.0 si gana el partido o a 0.0 si pierde. La simulación de
    bankroll aplica el fee taker compartido de Kalshi en la entrada.
  - Por SET: Kalshi NO tiene mercado de "quién gana el set" -- no hay
    precio real contra el cual simular un retorno en dólares. Este nivel
    reporta solo PRECISIÓN de la señal (¿el que cierra efectivamente
    ganó el set?), no ROI. No se inventa un "precio de set" ficticio.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from database.db import init_db_historical
from database.historical.tennis.matchstats_parser import typed_columns_from_stats
from database.historical.tennis.models import (
    LivePointSnapshot, LiveStatsSnapshot, Match, MatchHistory, ProbabilityTick,
)
from outer_apis.kalshi.fees import order_fee
from outer_apis.sports.tennis.set_sequence_stats import parse_set_scores
from scripts.reports import risk_metrics
from scripts.tennis.live_stats_market_reaction import (
    MIN_SNAPSHOTS_PER_MATCH,
)

MIN_SPW_EDGE_PCT = 8.0  # 2026-08-29, subido de 2.0 -- pedido explícito del
# usuario tras ver que las 9 derrotas de los primeros 123 eventos tenían
# SPW edge notablemente más débil que las ganadoras (mediana 7.0 vs 13.8).
MIN_SECOND_SERVE_PCT = 40.0


def _other(side: str) -> str:
    return "b" if side == "a" else "a"


def _spw(snapshot: LiveStatsSnapshot, side: str) -> float | None:
    first_pct = getattr(snapshot, f"first_serve_pct_{side}")
    first_won = getattr(snapshot, f"first_serve_points_won_pct_{side}")
    second_won = getattr(snapshot, f"second_serve_points_won_pct_{side}")
    if None in (first_pct, first_won, second_won):
        return None
    first_share = first_pct / 100.0
    return first_share * first_won + (1.0 - first_share) * second_won


def _second_serve_pct(snapshot: LiveStatsSnapshot, side: str) -> float | None:
    return getattr(snapshot, f"second_serve_points_won_pct_{side}")


def _spw_edge(snapshot: LiveStatsSnapshot, side: str) -> float | None:
    spw_side = _spw(snapshot, side)
    spw_other = _spw(snapshot, _other(side))
    if spw_side is None or spw_other is None:
        return None
    return spw_side - spw_other


def _set_serve_conditions(
    snapshot: LiveStatsSnapshot, side: str, set_number: int,
) -> tuple[float, float] | None:
    """SPW edge and second-serve percentage for the entry set only.

    Typed snapshot columns are match-cumulative. The raw Flashscore payload
    preserves independent ``set_N`` sections, so use that point-in-time data
    to ensure earlier sets cannot mask deterioration in the closing set.
    """
    import json
    try:
        raw = json.loads(snapshot.raw_stats_json or "{}")
    except (TypeError, ValueError):
        return None
    columns = typed_columns_from_stats(raw.get(f"set_{set_number}") or {})

    def set_spw(for_side: str) -> float | None:
        first_pct = columns.get(f"first_serve_pct_{for_side}")
        first_won = columns.get(f"first_serve_points_won_pct_{for_side}")
        second_won = columns.get(f"second_serve_points_won_pct_{for_side}")
        if None in (first_pct, first_won, second_won):
            return None
        first_share = first_pct / 100.0
        return first_share * first_won + (1.0 - first_share) * second_won

    side_spw = set_spw(side)
    other_spw = set_spw(_other(side))
    second_serve = columns.get(f"second_serve_points_won_pct_{side}")
    if None in (side_spw, other_spw, second_serve):
        return None
    return side_spw - other_spw, second_serve


def _structural_advantage(history: MatchHistory | None, side: str) -> bool | None:
    """True si `side` tiene ranking mejor Y Elo mayor (misma fuente para
    ambos jugadores -- superficie si ambos la tienen, si no general para
    ambos) que el rival. None (fail-closed) si falta cualquier dato."""
    if history is None:
        return None
    other = _other(side)
    rank_side = getattr(history, f"player_{side}_ranking")
    rank_other = getattr(history, f"player_{other}_ranking")
    if rank_side is None or rank_other is None:
        return None

    elo_side = getattr(history, f"player_{side}_surface_elo")
    elo_other = getattr(history, f"player_{other}_surface_elo")
    if elo_side is None or elo_other is None:
        elo_side = getattr(history, f"player_{side}_elo")
        elo_other = getattr(history, f"player_{other}_elo")
    if elo_side is None or elo_other is None:
        return None

    return rank_side < rank_other and elo_side > elo_other


@dataclass
class ClosingScalpEvent:
    match_id: int
    side: str
    entry_price: float
    entry_observed_at: datetime
    spw_edge_at_entry: float
    second_serve_pct_at_entry: float
    current_set_at_entry: int | None
    entry_sequence: str | None = None
    settled_at: datetime | None = None
    set_spw_edge_at_entry: float | None = None
    set_second_serve_pct_at_entry: float | None = None
    # Re-chequeo/resultado, completados por `_resolve_event`:
    spw_edge_still_valid: bool | None = None
    set_outcome_known: bool | None = None
    closer_won_set: bool | None = None
    match_resolved: bool = False
    closer_won_match: bool | None = None
    exit_price: float | None = None
    match_return_pct: float | None = None
    # 2026-08-29, pedido explícito del usuario ("tiene que ser cuando
    # sirvan para el match", de su experiencia apostando manual). DOS
    # campos relacionados pero NO intercambiables:
    #   - `is_final_set_of_match` (RETROSPECTIVA, con sesgo de
    #     anticipación -- usa el resultado FINAL del partido). Solo
    #     curiosidad de análisis post-hoc, NUNCA gate de entrada.
    #   - `is_forced_final_set_at_entry` (CALCULABLE EN VIVO): True si el
    #     set de la entrada es el que matemáticamente TIENE que ser el
    #     último posible (ej. set 3 de mejor-de-3) -- es SOLO una de las
    #     dos formas de estar en un set que puede cerrar el partido, ver
    #     `_is_match_closing_set`
    #     para la otra (ya tener suficientes sets ganados antes de este).
    #     El gate real de entrada es `_is_match_closing_set`, que cubre
    #     ambos casos -- este campo queda como diagnóstico de CUÁL de los
    #     dos casos aplicó.
    is_final_set_of_match: bool | None = None
    is_forced_final_set_at_entry: bool | None = None


def _price_for_side(tick_id: int | None, side: str,
                     price_cache: dict[int, tuple[float | None, float | None]]) -> float | None:
    """Precio del lado que efectivamente se opera -- 2026-08-28, bug real
    encontrado en la primera corrida contra prod: `market_price_a` y
    `market_price_b` NO son necesariamente complementarios exactos (dos
    columnas separadas en `ProbabilityTick`), y el código original usaba
    SIEMPRE `market_price_a` sin importar qué lado cerraba. Cuando B era
    el que cerraba y A estaba fuertemente favorito (precio de A bajo),
    el retorno simulado quedaba inflado de forma absurda (~1488% promedio
    en el primer corte). Fail-closed: si el precio del lado que
    corresponde no está disponible, no se cae al otro lado ni se
    complementa (`1 - precio`) -- se descarta el evento."""
    if tick_id is None:
        return None
    prices = price_cache.get(tick_id)
    if prices is None:
        return None
    price_a, price_b = prices
    return price_a if side == "a" else price_b


def _collect_candidate_snapshots(
    match_id: int,
) -> tuple[list[LiveStatsSnapshot], dict[int, tuple[float | None, float | None]]]:
    ticks = ProbabilityTick.select(
        ProbabilityTick.id, ProbabilityTick.market_price_a, ProbabilityTick.market_price_b,
    ).where(ProbabilityTick.match == match_id)
    price_cache = {t.id: (t.market_price_a, t.market_price_b) for t in ticks}
    snaps = list(
        LiveStatsSnapshot.select()
        .where(LiveStatsSnapshot.match == match_id)
        .order_by(LiveStatsSnapshot.observed_at.asc(), LiveStatsSnapshot.id.asc())
    )
    return snaps, price_cache


def _point_snapshot_at(match_id: int, probability_tick_id: int | None) -> LivePointSnapshot | None:
    if probability_tick_id is None:
        return None
    return LivePointSnapshot.get_or_none(
        (LivePointSnapshot.match == match_id)
        & (LivePointSnapshot.probability_tick == probability_tick_id)
    )


def _games_by_set(point: LivePointSnapshot) -> dict[int, tuple[int, int]]:
    """Juegos ganados por A/B en CADA set, a partir del punto-a-punto
    crudo -- mismo mapeo de orientación que `strategies/live_limit_arb.py
    ::_games_by_side` (reusado por consistencia, pero acá separado por
    set en vez de sumado sobre todo el partido)."""
    import json as _json
    try:
        raw = _json.loads(point.raw_point_json or "{}")
    except (TypeError, ValueError):
        return {}
    result: dict[int, tuple[int, int]] = {}
    for key, section in raw.items():
        if not isinstance(section, dict) or not key.startswith("set_"):
            continue
        try:
            set_number = int(key.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        a_games = b_games = 0
        for game in section.get("games") or []:
            side = game.get("winner_side")
            if side not in (1, 2):
                continue
            winner_is_a = (side == 1) == point.player_a_is_flashscore_p1
            a_games += int(winner_is_a)
            b_games += int(not winner_is_a)
        result[set_number] = (a_games, b_games)
    return result


def _sets_won_before_entry(point: LivePointSnapshot | None, side: str, current_set: int) -> int | None:
    """Cuántos sets YA ganó `side` en los sets ANTERIORES al de la
    entrada -- calculable en vivo, sin sesgo de anticipación (los sets
    previos al actual ya terminaron de verdad al momento de la entrada,
    no es información del futuro). Fail-closed: None si no hay punto-a-
    punto crudo disponible, o si algún set previo no tiene un ganador
    claro (empate de juegos -- no debería pasar, pero no se asume)."""
    if point is None:
        return None
    by_set = _games_by_set(point)
    won = 0
    for set_number in range(1, current_set):
        games = by_set.get(set_number)
        if games is None:
            return None
        games_a, games_b = games
        if games_a == games_b:
            return None
        winner = "a" if games_a > games_b else "b"
        won += int(winner == side)
    return won


def _is_match_closing_set(match: Match, side: str, current_set: int | None,
                          point: LivePointSnapshot | None) -> bool | None:
    """True si ganar el set ACTUAL alcanzaría para cerrar el partido.

    Esto describe la etapa del partido; deliberadamente no afirma quién
    está sirviendo. Tras confirmar Break+Hold el turno de servicio ya acabó,
    y el feed histórico no guarda un campo fiable de servidor actual.
    Se acepta el set decisivo forzoso
    (`current_set == best_of`) O porque `side` ya tiene suficientes sets
    ganados en los anteriores como para que este sea el que cierra
    (ej. va 1-0 en un partido a mejor-de-3, entra al set 2). Ambas
    variantes son calculables en vivo -- ningún caso usa el resultado
    final del partido."""
    if current_set is None:
        return None
    sets_to_win = (match.best_of // 2) + 1
    if current_set == match.best_of:
        return True
    won_before = _sets_won_before_entry(point, side, current_set)
    if won_before is None:
        return None
    return won_before >= sets_to_win - 1


def _flashscore_side(point: LivePointSnapshot, side: str) -> int:
    """Map stable Kairos side a/b to Flashscore's point-feed side 1/2."""
    return 1 if (side == "a") == point.player_a_is_flashscore_p1 else 2


def _closing_service_sequence(
    match: Match, point: LivePointSnapshot | None, side: str,
) -> str | None:
    """Identify an executable service game for the match.

    Accepted paths are deliberately explicit:
      * ``break_then_serve``: the last completed game was a break by the
        candidate, who now serves for the match.
      * ``break_hold_then_serve``: break, consolidated hold, one opponent
        service game, then the candidate serves again for the match.

    HC/HE are the post-game score and HG is the server of each completed
    game in Flashscore's feed. Missing or malformed fields fail closed.
    """
    if point is None or point.current_set is None:
        return None
    if _is_match_closing_set(match, side, point.current_set, point) is not True:
        return None
    try:
        import json
        raw = json.loads(point.raw_point_json or "{}")
        games = (raw.get(f"set_{point.current_set}") or {}).get("games") or []
        last = games[-1]
        score_p1 = int(last.get("raw", {}).get("HC"))
        score_p2 = int(last.get("raw", {}).get("HE"))
        last_server = int(last.get("raw", {}).get("HG"))
    except (IndexError, TypeError, ValueError):
        return None

    candidate = _flashscore_side(point, side)
    opponent = 2 if candidate == 1 else 1
    # Service alternates after every normal game. Tiebreak entry (6-6) is
    # intentionally excluded because its service rotation is different.
    if last_server != opponent:
        return None
    side_games = score_p1 if candidate == 1 else score_p2
    opponent_games = score_p2 if candidate == 1 else score_p1
    if not ((side_games == 5 and opponent_games <= 4)
            or (side_games == 6 and opponent_games == 5)):
        return None

    if last.get("winner_side") == candidate:
        return "break_then_serve"

    if len(games) < 3:
        return None
    break_game, hold_game, opponent_game = games[-3:]
    try:
        break_server = int(break_game.get("raw", {}).get("HG"))
        hold_server = int(hold_game.get("raw", {}).get("HG"))
        opponent_server = int(opponent_game.get("raw", {}).get("HG"))
    except (TypeError, ValueError):
        return None
    if (
        break_server == opponent
        and break_game.get("winner_side") == candidate
        and hold_server == candidate
        and hold_game.get("winner_side") == candidate
        and opponent_server == opponent
    ):
        return "break_hold_then_serve"
    return None


def _resolve_event(event: ClosingScalpEvent, match: Match, snaps: list[LiveStatsSnapshot],
                    price_cache: dict[int, tuple[float | None, float | None]], entry_index: int) -> None:
    """Completa el re-chequeo y el resultado real, mirando snapshots
    POSTERIORES a la entrada (esto es retrospectivo -- el partido ya
    terminó al momento de correr el backtest)."""
    side = event.side

    # Re-chequeo de SPW: se evalúa sobre el ÚLTIMO snapshot disponible del
    # partido (aproximación al "momento de cierre" -- limitación de
    # resolución conocida, no hay snapshot exactamente en el instante del
    # último punto).
    last_snap = snaps[-1]
    edge_at_close = _spw_edge(last_snap, side)
    event.spw_edge_still_valid = (edge_at_close is not None and edge_at_close >= MIN_SPW_EDGE_PCT)

    # Resultado real del SET en el que ocurrió la entrada -- solo
    # precisión de señal, Kalshi no tiene mercado de set (ver docstring).
    if event.current_set_at_entry is not None and match.match_history_id:
        sets = parse_set_scores(match.match_history.score)
        idx = event.current_set_at_entry - 1
        if sets is not None and 0 <= idx < len(sets):
            games_a, games_b = sets[idx]
            games_side = games_a if side == "a" else games_b
            games_other = games_b if side == "a" else games_a
            if games_side != games_other:
                event.set_outcome_known = True
                event.closer_won_set = games_side > games_other
            # proxy de "sacando para el partido": el set de la entrada
            # fue el ÚLTIMO set jugado (idx es 0-based, len(sets) es la
            # cantidad real de sets del partido completo).
            event.is_final_set_of_match = (event.current_set_at_entry == len(sets))

    # Resultado real del PARTIDO -- acá sí hay retorno simulable. Sin
    # stop-loss (eliminado 2026-08-28): toda posición se liquida contra
    # el resultado real, ganó (1.0) o perdió (0.0).
    if match.winner in ("a", "b"):
        event.match_resolved = True
        event.closer_won_match = (match.winner == side)
        event.exit_price = 1.0 if event.closer_won_match else 0.0

        if event.entry_price not in (None, 0.0):
            if event.closer_won_match:
                event.match_return_pct = round(
                    100.0 * (1.0 - event.entry_price) / event.entry_price, 2,
                )
            else:
                event.match_return_pct = -100.0


def collect_closing_scalp_events() -> list[ClosingScalpEvent]:
    init_db_historical()
    events: list[ClosingScalpEvent] = []

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
        snaps, price_cache = _collect_candidate_snapshots(match_id)

        for i, entry_snap in enumerate(snaps):
            point = _point_snapshot_at(match_id, entry_snap.probability_tick_id)
            current_set = point.current_set if point else None
            for side in ("a", "b"):
                sequence = _closing_service_sequence(match, point, side)
                if sequence is None:
                    continue
                entry_price = _price_for_side(entry_snap.probability_tick_id, side, price_cache)
                if entry_price is None or not 0.0 < entry_price < 1.0:
                    continue
                spw_edge = _spw_edge(entry_snap, side)
                if spw_edge is None or spw_edge < MIN_SPW_EDGE_PCT:
                    continue
                second_serve_pct = _second_serve_pct(entry_snap, side)
                if second_serve_pct is None or second_serve_pct < MIN_SECOND_SERVE_PCT:
                    continue
                if _structural_advantage(history, side) is not True:
                    continue
                set_conditions = _set_serve_conditions(entry_snap, side, current_set)
                if set_conditions is None:
                    continue
                set_spw_edge, set_second_serve_pct = set_conditions
                if set_spw_edge < MIN_SPW_EDGE_PCT \
                        or set_second_serve_pct < MIN_SECOND_SERVE_PCT:
                    continue

                event = ClosingScalpEvent(
                    match_id=match_id, side=side, entry_price=entry_price,
                    entry_observed_at=entry_snap.observed_at,
                    settled_at=match.settled_at or snaps[-1].observed_at,
                    spw_edge_at_entry=round(spw_edge, 2),
                    second_serve_pct_at_entry=second_serve_pct,
                    current_set_at_entry=current_set,
                    entry_sequence=sequence,
                    set_spw_edge_at_entry=round(set_spw_edge, 2),
                    set_second_serve_pct_at_entry=set_second_serve_pct,
                    is_forced_final_set_at_entry=(current_set == match.best_of),
                )
                _resolve_event(event, match, snaps, price_cache, i)
                events.append(event)
                break
            if events and events[-1].match_id == match_id:
                # One position per market; repeated snapshots of the same
                # pre-service score are not independent opportunities.
                break
    return events


def run_closing_scalp_backtest() -> dict[str, Any]:
    events = collect_closing_scalp_events()
    n_matches = len({e.match_id for e in events})

    resolved_matches = [e for e in events if e.match_resolved]
    with_return = [e for e in resolved_matches if e.match_return_pct is not None]
    with_set_outcome = [e for e in events if e.set_outcome_known]
    still_valid_at_close = [e for e in events if e.spw_edge_still_valid]

    return {
        "n_matches_con_snapshots_suficientes": n_matches,
        "n_eventos_entrada_filtrados": len(events),
        "n_con_ventaja_spw_vigente_al_cierre": len(still_valid_at_close),
        "pct_ventaja_spw_vigente_al_cierre": (
            round(100 * len(still_valid_at_close) / len(events), 1) if events else None
        ),
        "nivel_set__n_con_resultado_conocido": len(with_set_outcome),
        "nivel_set__win_rate_pct": (
            round(100 * sum(e.closer_won_set for e in with_set_outcome) / len(with_set_outcome), 1)
            if with_set_outcome else None
        ),
        "nivel_partido__n_resueltos": len(resolved_matches),
        "nivel_partido__win_rate_pct": (
            round(100 * sum(e.closer_won_match for e in resolved_matches) / len(resolved_matches), 1)
            if resolved_matches else None
        ),
        "nivel_partido__n_con_retorno_calculado": len(with_return),
        "nivel_partido__retorno_promedio_pct": (
            round(sum(e.match_return_pct for e in with_return) / len(with_return), 2)
            if with_return else None
        ),
        "nota_sin_stop_loss": (
            "2026-08-28, pedido explicito del usuario: se elimino el "
            "stop-loss de 2do servicio -- ahora es SOLO filtro de "
            "entrada. No hay ningun stop-loss activo en esta version; "
            "toda posicion se liquida contra el resultado real."
        ),
        "nota_filtro_sirviendo_para_el_partido": (
            "Solo se opera justo antes de que el candidato saque para "
            "ganar el partido. Se aceptan break->saque para partido y "
            "break->hold->saque rival->nuevo saque para partido, "
            "reconstruidos con HC/HE/HG del punto-a-punto real."
        ),
        "nota_retorno_bruto_vs_neto": (
            "'nivel_partido__retorno_promedio_pct' de arriba es BRUTO "
            "(retorno aislado por trade, 100% del capital en cada uno) -- "
            "el retorno NETO de fees reales, con gestion de bankroll, "
            "esta en `simulate_bankroll()` / 'simulacion_bankroll_nivel_partido'."
        ),
    }


def simulate_bankroll(
    events: list[ClosingScalpEvent] | None = None,
    *,
    initial_capital: float = 50.0,
    stake_pct: float = 0.01,
    max_daily_exposure_pct: float = 0.5,
) -> dict[str, Any]:
    """Simulación de bankroll real -- 2026-08-28, pedido explícito del
    usuario ("¿se está usando el 1% del bankroll?"): el reporte de
    `run_closing_scalp_backtest` solo daba el retorno % promedio por
    trade AISLADO (100% del capital en cada uno), no cómo evoluciona un
    bankroll real operando 123 veces con gestión de riesgo. Mismo patrón
    EXACTO que `bet_on_favourite_backtest.py::simulate()` (capital
    inicial $50, 1% de stake por trade, tope de exposición diaria 50% del
    capital, agrupado por día -- reusa `scripts/reports/risk_metrics.py`
    para max drawdown/Sharpe/CAGR/CVaR, no reinventa la métrica).

    Solo entra el nivel PARTIDO (único con mercado tradeable real, ver
    docstring del módulo) -- el nivel set no tiene precio real, no se
    puede simular bankroll sobre algo que no es operable.

    COMPOUNDING -- 2026-08-29, pedido explícito del usuario ("el
    compounding se aplica tras cada trade"): el 1% se recalcula sobre el
    capital ACTUALIZADO después de CADA trade individual, no solo al
    cierre del día -- antes, todos los trades del mismo día usaban el
    mismo capital de referencia (solo componía entre días distintos).
    `start_capital` sigue existiendo por día, pero únicamente como techo
    de exposición (no más del 50% del capital de arranque del día en
    stakes simultáneos), no como base fija de cálculo del stake.

    FEES -- 2026-08-29, pedido explícito del usuario: se aplica
    `outer_apis.kalshi.fees.order_fee` (fórmula real ya usada por
    LimitArb/MarketArb en este mismo proyecto, no inventada acá):
    `fee = ceil_al_centavo(0.07 * contratos * precio * (1-precio))`, por
    orden. Se cobra SOLO en la entrada (orden taker real) -- el cierre es
    liquidación automática al vencer el contrato, no una orden nueva, no
    tiene fee. Los contratos se redondean hacia abajo a enteros. Si el
    presupuesto no alcanza para uno, la operación se omite; con $50 y
    riesgo de 1%, por ejemplo, no se fabrica una fracción de contrato."""
    if events is None:
        events = collect_closing_scalp_events()
    resolved = [e for e in events if e.match_resolved and e.match_return_pct is not None]

    cash = initial_capital
    equity = [initial_capital]
    returns: list[float] = []
    daily_returns: list[float] = []
    hits = 0
    skipped_daily_cap = 0
    skipped_min_contract = 0
    skipped_insufficient_cash = 0
    total_fees = 0.0

    # Replay entries and settlements in real timestamp order. Entry cash is
    # reserved until settlement, so a future win cannot size an earlier or
    # overlapping trade. Kalshi contracts are indivisible.
    actions = []
    for seq, event in enumerate(resolved):
        settled_at = getattr(event, "settled_at", None) or event.entry_observed_at
        if settled_at < event.entry_observed_at:
            continue
        actions.append((event.entry_observed_at, 0, seq, event))
        actions.append((settled_at, 1, seq, event))
    actions.sort(key=lambda item: (item[0], item[1], item[2]))

    positions: dict[int, tuple[int, float]] = {}
    daily_exposure: dict[str, float] = defaultdict(float)
    daily_start_equity: dict[str, float] = {}
    daily_end_equity: dict[str, float] = {}
    for when, action_kind, seq, event in actions:
        date_key = when.date().isoformat()
        reserved = sum(cost for _, cost in positions.values())
        current_equity = cash + reserved
        daily_start_equity.setdefault(date_key, current_equity)
        if action_kind == 0:
            budget = current_equity * stake_pct
            contracts = int(budget // event.entry_price) if event.entry_price else 0
            if contracts <= 0:
                skipped_min_contract += 1
                daily_end_equity[date_key] = current_equity
                continue
            cost = contracts * event.entry_price
            cap = daily_start_equity[date_key] * max_daily_exposure_pct
            if daily_exposure[date_key] + cost > cap:
                skipped_daily_cap += 1
                daily_end_equity[date_key] = current_equity
                continue
            fee = order_fee(event.entry_price, contracts=contracts) or 0.0
            if cost + fee > cash:
                skipped_insufficient_cash += 1
                daily_end_equity[date_key] = current_equity
                continue
            cash -= cost + fee
            positions[seq] = (contracts, cost)
            daily_exposure[date_key] += cost
            total_fees += fee
        else:
            position = positions.pop(seq, None)
            if position is None:
                daily_end_equity[date_key] = cash + sum(cost for _, cost in positions.values())
                continue
            contracts, _cost = position
            cash += contracts * (1.0 if event.closer_won_match else 0.0)
            returns.append(event.match_return_pct / 100.0)
            hits += int(event.closer_won_match)
        daily_end_equity[date_key] = cash + sum(cost for _, cost in positions.values())

    dates = sorted(daily_end_equity)
    for date_key in dates:
        start = daily_start_equity[date_key]
        end = daily_end_equity[date_key]
        daily_returns.append((end - start) / start if start > 0 else 0.0)
        equity.append(end)
    capital = cash + sum(cost for _, cost in positions.values())
    trading_days = len(dates)
    elapsed_years = (
        max(1 / 365.25, (
            datetime.fromisoformat(dates[-1]).date() - datetime.fromisoformat(dates[0]).date()
        ).days / 365.25)
        if dates else 1.0
    )
    tdpy = trading_days / elapsed_years if elapsed_years else 0.0
    max_dd = risk_metrics.max_drawdown_pct(equity)
    cagr = risk_metrics.cagr_pct(equity, initial_capital, tdpy) if trading_days else None

    return {
        "initial_capital": initial_capital,
        "stake_pct": stake_pct,
        "n_trades": len(returns),
        "skipped_daily_cap": skipped_daily_cap,
        "skipped_min_contract": skipped_min_contract,
        "skipped_insufficient_cash": skipped_insufficient_cash,
        "hit_rate": round(hits / len(returns), 4) if returns else None,
        "final_capital": round(capital, 2),
        "roi_pct": round((capital / initial_capital - 1) * 100, 2),
        "total_fees_pagados": round(total_fees, 4),
        "max_drawdown_pct": max_dd,
        "annualized_sharpe": risk_metrics.annualized_sharpe(equity, initial_capital, tdpy) if trading_days else None,
        "trade_cvar_95_pct": risk_metrics.cvar_95_pct(returns),
        "daily_capital_cvar_95_pct": risk_metrics.cvar_95_pct(daily_returns),
        "cagr_pct": cagr,
        "nota_fees": (
            "2026-08-29: retorno NETO, con fees reales de Kalshi "
            "aplicados via outer_apis.kalshi.fees.order_fee (0.07 x "
            "contratos x precio x (1-precio), redondeado al centavo por "
            "orden) -- cobrado solo en la entrada, el cierre es "
            "liquidacion automatica sin fee. 'total_fees_pagados' es la "
            "suma en dolares sobre todo el periodo simulado."
        ),
    }


if __name__ == "__main__":
    import json
    events = collect_closing_scalp_events()
    print(json.dumps({
        "reporte_general": run_closing_scalp_backtest(),
        "simulacion_bankroll_nivel_partido": simulate_bankroll(events),
    }, indent=2, ensure_ascii=False))
