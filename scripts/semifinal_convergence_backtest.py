"""
Backtest de la hipótesis del usuario (2026-08-01, caso real que la
motivó: Nakashima vs Fritz, semifinal ATP Washington, forzando el
segundo set con la probabilidad convergiendo hacia 50%):

    "En semifinales y finales de torneos ATP/WTA regulares (250+, dado
    que los niveles tienden a ser más parejos), la probabilidad del
    underdog infravalorado tiende a alcanzar `raw_edge +
    avg_volatility/2` -- SIENDO ese target <= 50%, es decir, la
    hipótesis solo se evalúa cuando el edge+volatilidad no implica que
    el underdog pase a ser favorito (2026-08-01, refinamiento explícito
    del usuario tras la primera corrida: la versión anterior sumaba el
    precio de señal, contaminando el resultado con casos donde el
    underdog simplemente terminó GANANDO el partido completo, no
    'convergiendo')."

SOLO INFORME, NO AUTO-APLICA NADA (mismo criterio que
`calibration_report.py`) -- si el resultado confirma la hipótesis, es
una decisión del usuario/Sara si conviene ajustar `price_adjustment` o
el TP sugerido para señales de SF/F específicamente, no algo que este
script decida solo.

CÓMO SE CALCULA
----------------
Para cada `TennisScannedMatch` SETTLED (`winner` no NULL) con
`round` en {'Semifinal', 'Final'} (case-insensitive):

  1. Se busca la señal MÁS TEMPRANA de `LimitArbSignal` para ese
     `market_id` con `case='favorite_overvalued'` (el caso donde el
     underdog está infravalorado -- ver `strategies/limit_arb.py`,
     `market_leg` es el underdog comprado a mercado).
  2. `target = raw_edge + avg_volatility/2` -- el precio ABSOLUTO al que
     la hipótesis dice que el underdog debería converger (NO se suma al
     precio de señal -- eso fue la versión anterior, descartada por
     contaminar el resultado con victorias completas del underdog en
     vez de convergencia real). Solo se evalúan casos con `target <=
     0.5` -- si el target implica que el underdog ya pasaría a ser
     favorito, el caso se excluye (no es lo que la hipótesis dice).
  3. Se busca en `TennisMatchProbabilityTick` (ticks reales POST-señal,
     antes del settlement) el precio MÁXIMO que alcanzó el underdog.
     CAVEAT conocido: el tick solo guarda `market_price_a` (precio real
     de la pata A) -- si el underdog es la pata B, se aproxima como
     `1 - market_price_a` (no es exacto, cada pata tiene su propio
     spread bid/ask independiente, ver `outer_apis/kalshi/markets/
     tennis.py` docstring). Esto introduce ruido en esos casos,
     documentado, no oculto.
  4. "Convergió" (hit) si el máximo alcanzado >= target.

LIMITACIÓN DE DATOS (2026-08-01, honesta, no ocultada): `round` es una
columna NUEVA -- partidos trackeados ANTES de hoy no la tienen a menos
que se corra `backfill_missing_rounds` primero (ver
`database/historical/tennis/matchtracking.py`). Tampoco existe hoy una
fuente de "categoría de torneo" (250/500/1000) -- el filtro "250 en
adelante" NO se aplica todavía (equivale a "todo lo que aparece en las
series ATP/WTA regulares de Kalshi", que ya excluye Slams por diseño
de `outer_apis/kalshi/markets/tennis.py`). Con pocos días de tracking
real, es MUY probable que el sample size sea chico o cero al principio
-- el script reporta el N real, nunca fabrica una conclusión con 2-3
casos.
"""

from __future__ import annotations

from database.db import init_db_historical
from database.accounting.limitarbsignal import LimitArbSignal
from database.historical.tennis.tennisscannedmatch import TennisScannedMatch
from database.historical.tennis.tennismatchprobabilitytick import TennisMatchProbabilityTick

_TARGET_ROUNDS = {"semifinal", "final"}


def run_backtest() -> dict:
    init_db_historical()

    settled_matches = list(
        TennisScannedMatch.select().where(TennisScannedMatch.winner.is_null(False))
    )
    sf_final_matches = [
        m for m in settled_matches
        if m.round and m.round.strip().lower() in _TARGET_ROUNDS
    ]

    cases = []
    excluded_target_above_50 = 0
    for match in sf_final_matches:
        signal = (
            LimitArbSignal.select()
            .where(
                (LimitArbSignal.market_id == match.market_id)
                & (LimitArbSignal.case == "favorite_overvalued")
            )
            .order_by(LimitArbSignal.created_at.asc())
            .first()
        )
        if signal is None:
            continue  # este partido nunca tuvo una señal de underdog infravalorado

        target = signal.raw_edge + signal.avg_volatility / 2.0
        if target > 0.5:
            excluded_target_above_50 += 1
            continue  # fuera de alcance de la hipótesis (implicaría underdog favorito)

        ticks = list(
            TennisMatchProbabilityTick.select()
            .where(
                (TennisMatchProbabilityTick.match == match)
                & (TennisMatchProbabilityTick.observed_at >= signal.created_at)
            )
        )
        if not ticks:
            continue  # sin ticks post-señal, no se puede evaluar convergencia

        dog_side = signal.market_leg_side
        if dog_side == "a":
            dog_prices = [t.market_price_a for t in ticks if t.market_price_a is not None]
        else:
            # Aproximación documentada arriba -- el tick no guarda market_price_b.
            dog_prices = [1.0 - t.market_price_a for t in ticks if t.market_price_a is not None]

        if not dog_prices:
            continue

        max_reached = max(dog_prices)
        hit = max_reached >= target

        cases.append({
            "market_id": match.market_id,
            "round": match.round,
            "tournament": match.tournament,
            "players": f"{match.player_a_name} vs {match.player_b_name}",
            "signal_price": signal.market_leg_price_at_signal,
            "raw_edge": signal.raw_edge,
            "avg_volatility": signal.avg_volatility,
            "target": round(target, 4),
            "max_reached": round(max_reached, 4),
            "hit": hit,
        })

    n = len(cases)
    hits = sum(1 for c in cases if c["hit"])

    return {
        "total_settled_sf_final_matches": len(sf_final_matches),
        "excluded_target_above_50pct": excluded_target_above_50,
        "evaluable_cases": n,
        "hits": hits,
        "hit_rate": round(hits / n, 4) if n else None,
        "cases": cases,
        "caveat": (
            "Sample chico esperado -- round-tracking recién implementado "
            "2026-08-01, y filtro de categoría de torneo (250+) no aplicado "
            "por falta de fuente de datos. NO tratar hit_rate como "
            "concluyente con N < ~20."
        ),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run_backtest(), indent=2, ensure_ascii=False))
