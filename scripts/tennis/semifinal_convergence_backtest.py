"""
Backtest de la hipótesis del usuario (2026-08-01, caso real que la
motivó: Nakashima vs Fritz, semifinal ATP Washington, forzando el
segundo set con la probabilidad convergiendo hacia 50%):

    "En semifinales y finales de torneos ATP/WTA regulares (250+, dado
    que los niveles tienden a ser más parejos), la probabilidad del
    underdog infravalorado tiende a subir `raw_edge + avg_volatility/2`
    desde su precio pre-match -- PERO SOLO se evalúa cuando (a) el
    precio pre-match del underdog está por debajo o igual a 40% Y (b) el
    target resultante (precio pre-match + edge + vol/2) es <= 50% Y (c)
    el underdog está en MEJOR condición física/etarea que el favorito:
    0 lesiones/medical timeouts recientes, más descansado (índice de
    fatiga menor, ver `outer_apis/sports/tennis/fatigue.py`), y más
    joven (2026-08-01, tercer refinamiento explícito del usuario:
    'el armador de esta estrategia considere la fatiga acumulada de
    ambos players... 0 lesiones... + menor edad del underdog frente al
    favorito')."

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
  2. Se descarta si `market_leg_price_at_signal > 0.40` (el underdog
     tiene que arrancar por debajo o igual a 40% -- pedido explícito del
     usuario, relajado desde el 25% original que dejaba 0 muestras).
  3. `target = market_leg_price_at_signal + raw_edge + avg_volatility/2`.
     Se descarta si `target > 0.5` (fuera de alcance de la hipótesis,
     implicaría underdog favorito).
  4. GATE DE VALIDEZ (2026-08-01, pedido explícito del usuario -- "el
     armador de esta estrategia considere la fatiga acumulada de ambos
     players"), se descarta el caso si CUALQUIERA de estas 3 no se
     cumple:
       a. `injury_risk.get_injury_risk_flag(underdog) == {"flagged": False}`
          -- 0 lesiones/medical timeouts recientes del underdog. Si no
          hay dato real (`None`), se descarta (fail-closed, nunca se
          asume "sin lesión" por ausencia de info).
       b. `fatigue.calculate_fatigue_index(underdog) <
          fatigue.calculate_fatigue_index(favorito)` -- el underdog debe
          estar MÁS DESCANSADO. Si no se puede calcular el índice para
          cualquiera de los 2 (sin datos reales de duración de partido
          para el torneo actual), se descarta.
       c. `player_age.get_age(underdog) < player_age.get_age(favorito)`
          -- el underdog debe ser más joven. Si no hay edad real de
          cualquiera de los 2, se descarta.
  5. Se busca en `TennisMatchProbabilityTick` (ticks reales POST-señal,
     antes del settlement) el precio MÁXIMO que alcanzó el underdog.
     CAVEAT conocido: el tick solo guarda `market_price_a` (precio real
     de la pata A) -- si el underdog es la pata B, se aproxima como
     `1 - market_price_a` (no es exacto, cada pata tiene su propio
     spread bid/ask independiente, ver `outer_apis/kalshi/markets/
     tennis.py` docstring). Esto introduce ruido en esos casos,
     documentado, no oculto.
  6. "Convergió" (hit) si el máximo alcanzado >= target.

LIMITACIÓN DE DATOS (2026-08-01, honesta, no ocultada): `round` es una
columna NUEVA -- partidos trackeados ANTES de hoy no la tienen a menos
que se corra `backfill_missing_rounds` primero (ver
`database/historical/tennis/matchtracking.py`). Tampoco existe hoy una
fuente de "categoría de torneo" (250/500/1000) -- el filtro "250 en
adelante" NO se aplica todavía (equivale a "todo lo que aparece en las
series ATP/WTA regulares de Kalshi", que ya excluye Slams por diseño
de `outer_apis/kalshi/markets/tennis.py`). El gate de fatiga/edad/lesión
es NUEVO y hand-reasoned, no calibrado (ver docstring de `fatigue.py`) --
va a excluir MUCHOS casos reales por simple falta de dato (duración de
partido/fecha de nacimiento no siempre disponibles, sobre todo en
torneos challenger/ITF de bajo perfil). Con pocos días de tracking real,
es MUY probable que el sample size sea chico o cero al principio -- el
script reporta el N real, nunca fabrica una conclusión con 2-3 casos.
"""

from __future__ import annotations

from database.db import init_db_historical
from database.accounting.limitarbsignal import LimitArbSignal
from database.historical.models import Match as TennisScannedMatch
from database.historical.models import ProbabilityTick as TennisMatchProbabilityTick
from outer_apis.sports.tennis.injury_risk import get_injury_risk_flag
from outer_apis.sports.tennis.fatigue import calculate_fatigue_index
from outer_apis.sports.tennis.player_age import get_age

_TARGET_ROUNDS = {"semifinal", "final"}
_MAX_SIGNAL_PRICE = 0.40
_MAX_TARGET = 0.50


def _check_validity_gate(underdog_name, favorite_name, tournament, match_date) -> dict:
    """3 criterios pedidos explícitamente por el usuario, TODOS fail-
    closed (sin dato real -> inválido, nunca se asume a favor)."""
    injury = get_injury_risk_flag(underdog_name)
    injury_ok = injury is not None and injury.get("flagged") is False

    dog_fatigue = calculate_fatigue_index(underdog_name, tournament, match_date)
    fav_fatigue = calculate_fatigue_index(favorite_name, tournament, match_date)
    fatigue_ok = (
        dog_fatigue is not None and fav_fatigue is not None
        and dog_fatigue["fatigue_index"] < fav_fatigue["fatigue_index"]
    )

    dog_age = get_age(underdog_name, as_of=match_date)
    fav_age = get_age(favorite_name, as_of=match_date)
    age_ok = dog_age is not None and fav_age is not None and dog_age < fav_age

    return {
        "valid": injury_ok and fatigue_ok and age_ok,
        "injury_ok": injury_ok,
        "fatigue_ok": fatigue_ok,
        "age_ok": age_ok,
        "underdog_fatigue_index": dog_fatigue["fatigue_index"] if dog_fatigue else None,
        "favorite_fatigue_index": fav_fatigue["fatigue_index"] if fav_fatigue else None,
        "underdog_age": dog_age,
        "favorite_age": fav_age,
    }


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
    excluded_signal_price_above_40 = 0
    excluded_target_above_50 = 0
    excluded_validity_gate = 0
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

        if signal.market_leg_price_at_signal > _MAX_SIGNAL_PRICE:
            excluded_signal_price_above_40 += 1
            continue  # underdog no arrancaba lo bastante infravalorado (pedido del usuario)

        target = signal.market_leg_price_at_signal + signal.raw_edge + signal.avg_volatility / 2.0
        if target > _MAX_TARGET:
            excluded_target_above_50 += 1
            continue  # fuera de alcance de la hipótesis (implicaría underdog favorito)

        underdog_name = match.player_a_name if signal.market_leg_side == "a" else match.player_b_name
        favorite_name = match.player_b_name if signal.market_leg_side == "a" else match.player_a_name

        validity = _check_validity_gate(underdog_name, favorite_name, match.tournament, match.detected_at.date())
        if not validity["valid"]:
            excluded_validity_gate += 1
            continue

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
            "underdog": underdog_name,
            "favorite": favorite_name,
            "signal_price": signal.market_leg_price_at_signal,
            "raw_edge": signal.raw_edge,
            "avg_volatility": signal.avg_volatility,
            "target": round(target, 4),
            "max_reached": round(max_reached, 4),
            "hit": hit,
            "underdog_fatigue_index": validity["underdog_fatigue_index"],
            "favorite_fatigue_index": validity["favorite_fatigue_index"],
            "underdog_age": validity["underdog_age"],
            "favorite_age": validity["favorite_age"],
        })

    n = len(cases)
    hits = sum(1 for c in cases if c["hit"])

    return {
        "total_settled_sf_final_matches": len(sf_final_matches),
        "excluded_signal_price_above_40pct": excluded_signal_price_above_40,
        "excluded_target_above_50pct": excluded_target_above_50,
        "excluded_validity_gate": excluded_validity_gate,
        "evaluable_cases": n,
        "hits": hits,
        "hit_rate": round(hits / n, 4) if n else None,
        "cases": cases,
        "caveat": (
            "Sample chico esperado -- round-tracking recién implementado "
            "2026-08-01, y filtro de categoría de torneo (250+) no aplicado "
            "por falta de fuente de datos. Gate de fatiga/edad/lesión "
            "hand-reasoned sin calibrar, y excluye casos por simple falta "
            "de dato (duración/fecha de nacimiento no siempre disponibles). "
            "NO tratar hit_rate como concluyente con N < ~20."
        ),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run_backtest(), indent=2, ensure_ascii=False))
