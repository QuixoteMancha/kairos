"""Análisis exploratorio: ¿el mercado (Kalshi) reacciona lo suficiente/
rápido frente a ventajas grandes que ya se ven en las stats acumuladas +
el punto a punto en vivo? -- 2026-08-23, pedido explícito del usuario
("analizar los cambios de tendencia en las stats y como se refleja en
punto a punto + la reacción del mercado, para ver si el mercado
reacciona lento o no lo suficiente").

CONFIRMADO antes de construir esto: `model_fair_a` (guardado en cada
`ProbabilityTick` por `tennis_match_tick_worker.py`) es un fair price
PRE-PARTIDO puro (rank/tour/surface/best_of vía `_rebuild_market_dict`)
-- NO incorpora score en vivo ni stats. No hay ningún cálculo existente
de "fair price en-vivo" contra el cual comparar `market_price_a` --
este script construye la señal desde cero, directo sobre los deltas de
`LiveStatsSnapshot` (stats acumuladas) y su tick de mercado enlazado.

Metodología (event-study simple, ventana = intervalo real entre 2
snapshots consecutivos del mismo partido, ~5 min salvo huecos por dedupe
de tick -- ver limitación de `persist_live_snapshots` documentada en
`live_tracking.py`):

  1. Por cada par de snapshots consecutivos (t-1, t) del mismo partido,
     con AMBOS stats (`total_points_won`, `break_points_won`) no-NULL:
     - `swing_towards_a`: cambio en el dominio de puntos ganados
       (delta_points_a - delta_points_b) + peso extra por quiebres
       ganados en la ventana (`break_points_won` delta) -- un swing
       positivo grande = A dominó claramente esa ventana.
  2. Precio de mercado ANTES (`price_before`, tick de snapshot t-1) y
     DESPUÉS (`price_after`, tick de snapshot t) -- `market_price_a`
     real de Kalshi, no el fair pre-partido.
  3. `contemporaneous_move` = price_after - price_before (¿el mercado
     ya se movió EN esa misma ventana?).
  4. `lagged_move` = price en el tick de snapshot t+1 (si existe) menos
     price_after -- ¿el mercado sigue moviéndose DESPUÉS, en la ventana
     SIGUIENTE, en la MISMA dirección del swing? Eso es la señal de
     "reacción lenta/insuficiente" que pidió el usuario: swing grande
     con contemporaneous_move chico pero lagged_move grande y en la
     misma dirección.

Fail-closed: partidos/ventanas sin stats o sin tick enlazado se
excluyen, nunca se interpola un precio o una stat faltante.

LIMITACIÓN HONESTA (poca muestra, ver docstring de `run_analysis`):
solo hay partidos con snapshots desde que `tennis_live_data_worker`
está corriendo -- la muestra crece sola con el tiempo, esto es un
PRIMER corte exploratorio, no una conclusión estadística robusta
todavía."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from database.db import init_db_historical
from database.historical.tennis.models import LiveStatsSnapshot, Match, ProbabilityTick

MIN_SNAPSHOTS_PER_MATCH = 3


@dataclass
class WindowEvent:
    match_id: int
    swing_towards_a: float
    price_before: float
    price_after: float
    contemporaneous_move: float
    lagged_move: float | None  # None si no hay snapshot t+1


def _stat_delta(curr: LiveStatsSnapshot, prev: LiveStatsSnapshot, field_a: str, field_b: str) -> float | None:
    a1, a0 = getattr(curr, field_a), getattr(prev, field_a)
    b1, b0 = getattr(curr, field_b), getattr(prev, field_b)
    if None in (a1, a0, b1, b0):
        return None
    return (a1 - a0) - (b1 - b0)


def _swing_towards_a(curr: LiveStatsSnapshot, prev: LiveStatsSnapshot) -> float | None:
    """Puntos ganados (peso 1) + quiebres ganados (peso 3, un quiebre
    pesa más que un punto normal para el resultado del partido) --
    ambos point-in-time, sin lookahead (solo compara 2 snapshots ya
    escritos)."""
    points = _stat_delta(curr, prev, "total_points_won_a", "total_points_won_b")
    breaks = _stat_delta(curr, prev, "break_points_won_a", "break_points_won_b")
    if points is None or breaks is None:
        return None
    return points + 3.0 * breaks


def _price_at_tick(tick_id: int | None, price_cache: dict[int, float]) -> float | None:
    if tick_id is None:
        return None
    return price_cache.get(tick_id)


def collect_events() -> list[WindowEvent]:
    init_db_historical()
    events: list[WindowEvent] = []

    match_ids = [
        row.match_id for row in (
            LiveStatsSnapshot.select(LiveStatsSnapshot.match)
            .group_by(LiveStatsSnapshot.match)
            .having(__import__("peewee").fn.COUNT(LiveStatsSnapshot.id) >= MIN_SNAPSHOTS_PER_MATCH)
        )
    ]
    if not match_ids:
        return events

    ticks = ProbabilityTick.select(ProbabilityTick.id, ProbabilityTick.market_price_a).where(
        ProbabilityTick.match.in_(match_ids)
    )
    price_cache = {t.id: t.market_price_a for t in ticks if t.market_price_a is not None}

    for match_id in match_ids:
        snaps = list(
            LiveStatsSnapshot.select()
            .where(LiveStatsSnapshot.match == match_id)
            .order_by(LiveStatsSnapshot.observed_at.asc(), LiveStatsSnapshot.id.asc())
        )
        prices = [_price_at_tick(s.probability_tick_id, price_cache) for s in snaps]

        for i in range(1, len(snaps)):
            swing = _swing_towards_a(snaps[i], snaps[i - 1])
            price_before, price_after = prices[i - 1], prices[i]
            if swing is None or price_before is None or price_after is None:
                continue
            lagged_move = None
            if i + 1 < len(snaps) and prices[i + 1] is not None:
                lagged_move = prices[i + 1] - price_after
            events.append(WindowEvent(
                match_id=match_id, swing_towards_a=swing,
                price_before=price_before, price_after=price_after,
                contemporaneous_move=price_after - price_before,
                lagged_move=lagged_move,
            ))
    return events


def run_analysis(swing_threshold: float = 4.0) -> dict[str, Any]:
    """`swing_threshold`: umbral (en la escala puntos+3*quiebres, ver
    `_swing_towards_a`) para considerar una ventana "ventaja grande" --
    4.0 equivale aprox. a 1 quiebre + 1 punto, o ~4 puntos sin quiebre,
    en una sola ventana de ~5 min. No validado por sweep todavía, punto
    de partida razonable para el primer corte exploratorio.

    Reporta, separado por ventanas con swing grande hacia CUALQUIER lado
    (a favor de A o de B, siempre en términos de "el lado que dominó"):
      - contemporaneous_move promedio EN LA DIRECCIÓN del swing (mercado
        ya reaccionó en esa misma ventana)
      - lagged_move promedio EN LA DIRECCIÓN del swing (mercado sigue
        moviéndose recién en la ventana siguiente -- reacción tardía)
      - cuántas ventanas de swing grande tuvieron contemporaneous_move
        prácticamente nulo (|move| < 0.01, "el mercado no reaccionó
        nada" en esa ventana) pero lagged_move real en la misma
        dirección (reacción tardía confirmada, no ausencia total)."""
    events = collect_events()
    n_matches = len({e.match_id for e in events})

    big = [e for e in events if abs(e.swing_towards_a) >= swing_threshold]

    def _signed(e: WindowEvent, move: float | None) -> float | None:
        if move is None:
            return None
        sign = 1.0 if e.swing_towards_a > 0 else -1.0
        return move * sign

    contemp_signed = [v for e in big if (v := _signed(e, e.contemporaneous_move)) is not None]
    lagged_signed = [v for e in big if (v := _signed(e, e.lagged_move)) is not None]

    slow_reaction = [
        e for e in big
        if abs(e.contemporaneous_move) < 0.01
        and e.lagged_move is not None
        and (_signed(e, e.lagged_move) or 0) > 0.01
    ]

    return {
        "n_matches_con_snapshots": n_matches,
        "n_ventanas_totales": len(events),
        "n_ventanas_swing_grande": len(big),
        "swing_threshold": swing_threshold,
        "contemporaneous_move_promedio_direccion_swing": (
            round(sum(contemp_signed) / len(contemp_signed), 4) if contemp_signed else None
        ),
        "lagged_move_promedio_direccion_swing": (
            round(sum(lagged_signed) / len(lagged_signed), 4) if lagged_signed else None
        ),
        "n_reaccion_tardia_confirmada": len(slow_reaction),
        "pct_reaccion_tardia_sobre_swing_grande": (
            round(100 * len(slow_reaction) / len(big), 1) if big else None
        ),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run_analysis(), indent=2, ensure_ascii=False))
