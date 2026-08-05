"""Backtest con PRECIO DE MERCADO REAL (cuotas de TennisExplorer, no el
motor base como proxy) -- 2026-08-03, pedido explícito del usuario
("sugerencia #2/#3 aplicada en código, backtest real pendiente hasta
tener toda la data en base" tras el backfill masivo top-200 ATP/WTA).

FUENTE: `TennisMatchHistory` JOIN `TennisHistoricalOdds` (por
player_name+opponent_name+match_date, ver
`scripts/wta_top200_historical_backfill.py`).

LIMITACIÓN IMPORTANTE DE ALCANCE (por qué esto NO reemplaza el backtest
completo de 8 estrategias): las cuotas de TennisExplorer son SOLO
pre-match -- no hay cuota condicional al resultado del set1/set2, así
que esto puede recalibrar limpiamente la ESTRATEGIA 1 (apostar y
sostener) y la PATA PRE-MATCH de las demás, pero NO las patas
reactivas (doblez/retiro) de las estrategias 2-8, que seguirían
necesitando el precio del motor base como proxy para esos momentos
intermedios del partido.

FAVORITO POR CUOTA, no por ranking (2026-08-03, mejora real sobre el
criterio usado en `favorite_by_historical_rank_backtest.py`): acá el
"favorito" es quien tiene la cuota más baja (implícitamente más
probable según el mercado real), no quien tiene mejor ranking -- esto
es MÁS correcto que el proxy de ranking usado en TML-Database (ver
memoria kairos-favorite-definition-uses-ranking-not-market-price,
caso real Nakashima/Fritz donde ranking y precio de mercado
discrepaban).

DE-VIG: una cuota decimal cruda invertida (1/cuota) NO es la
probabilidad real -- las 2 cuotas de un partido suman más de 100%
(margen/vig de la casa). `_devig_prob` normaliza ambos lados para que
sumen 100% antes de tratarlos como probabilidad."""

from __future__ import annotations

import json
import statistics
from typing import Any

from database.db import init_db_historical, db_historical
from database.historical.tennis.tennismatchhistory import TennisMatchHistory
from database.historical.tennis.tennishistoricalodds import TennisHistoricalOdds
from outer_apis.kalshi.fees import leg_fee_fraction


def _devig_prob(odds_self: float, odds_opponent: float) -> float | None:
    """Probabilidad implícita REAL del propio jugador/a, con el margen
    de la casa removido (normalizada a que ambos lados sumen 100%).
    Fail-closed: None si alguna cuota es inválida (<=1.0, que
    matemáticamente no puede ser una cuota decimal real)."""
    if odds_self is None or odds_opponent is None:
        return None
    if odds_self <= 1.0 or odds_opponent <= 1.0:
        return None
    raw_self = 1.0 / odds_self
    raw_opp = 1.0 / odds_opponent
    total = raw_self + raw_opp
    if total <= 0:
        return None
    return raw_self / total


def _load_priced_matches(
    tour_players: list[str] | None = None, start_year: int | None = None, end_year: int | None = None,
) -> list[dict[str, Any]]:
    """Une `TennisMatchHistory` con `TennisHistoricalOdds` por
    (player_name, opponent_name, match_date) -- solo filas con cuota
    real disponible en AMBOS lados (fail-closed, nunca fabrica una
    cuota faltante).

    `tour_players`: lista opcional de nombres para acotar la consulta
    (ej. el top-200 de un tour específico, ya que esta tabla mezcla
    jugadores de cualquier tour que se haya backfilleado)."""
    init_db_historical()
    result: list[dict[str, Any]] = []
    with db_historical.connection_context():
        query = TennisMatchHistory.select().where(TennisMatchHistory.result.in_(["W", "L"]))
        if tour_players:
            query = query.where(TennisMatchHistory.player_name.in_(tour_players))
        if start_year:
            query = query.where(TennisMatchHistory.match_date >= f"{start_year}-01-01")
        if end_year:
            query = query.where(TennisMatchHistory.match_date <= f"{end_year}-12-31")

        for row in query:
            odds_row = TennisHistoricalOdds.get_or_none(
                (TennisHistoricalOdds.player_name == row.player_name)
                & (TennisHistoricalOdds.opponent_name == row.opponent_name)
                & (TennisHistoricalOdds.match_date == row.match_date)
            )
            if odds_row is None or odds_row.odds_self is None or odds_row.odds_opponent is None:
                continue

            fair_self = _devig_prob(odds_row.odds_self, odds_row.odds_opponent)
            if fair_self is None:
                continue

            # favorito = quien tiene la cuota MÁS BAJA (más probable
            # según el mercado real) -- no por ranking.
            self_is_favorite = odds_row.odds_self < odds_row.odds_opponent
            favorite_fair_price = fair_self if self_is_favorite else (1.0 - fair_self)
            favorite_won = (row.result == "W") == self_is_favorite

            result.append({
                "player_name": row.player_name, "opponent_name": row.opponent_name,
                "match_date": row.match_date, "tournament": row.tournament,
                "favorite_fair_price": favorite_fair_price, "favorite_won": favorite_won,
                "odds_self": odds_row.odds_self, "odds_opponent": odds_row.odds_opponent,
            })
    return result


_FAIR_PRICE_BUCKETS = [
    (0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 0.75),
    (0.75, 0.80), (0.80, 0.85), (0.85, 0.90), (0.90, 0.95), (0.95, 1.01),
]


def calibration_by_real_odds(matches: list[dict[str, Any]]) -> dict[str, Any]:
    """Análogo a `_model_fair_price_calibration` pero con precio de
    MERCADO real (de-vigged) en vez del motor base -- responde una
    pregunta más importante todavía: ¿el mercado real está bien
    calibrado, o también sub/sobre-estima sistemáticamente?"""
    buckets: dict[str, dict[str, Any]] = {
        f"{lo:.2f}-{min(hi, 1.0):.2f}": {"n": 0, "hits": 0, "price_sum": 0.0}
        for lo, hi in _FAIR_PRICE_BUCKETS
    }
    for m in matches:
        price = m["favorite_fair_price"]
        for lo, hi in _FAIR_PRICE_BUCKETS:
            if lo <= price < hi:
                label = f"{lo:.2f}-{min(hi, 1.0):.2f}"
                buckets[label]["n"] += 1
                buckets[label]["hits"] += 1 if m["favorite_won"] else 0
                buckets[label]["price_sum"] += price
                break
    result = {}
    for label, b in buckets.items():
        n = b["n"]
        result[label] = {
            "n": n,
            "avg_market_price": round(b["price_sum"] / n, 4) if n else None,
            "actual_hit_rate": round(b["hits"] / n, 4) if n else None,
        }
    return result


def run_strategy1_with_real_odds(
    matches: list[dict[str, Any]], initial_capital: float = 50.0, stake_pct: float = 0.01,
) -> dict[str, Any]:
    """Estrategia 1 (apostar al favorito y sostener) con precio de
    mercado REAL de-vigged, neta de fees reales de Kalshi -- mismas
    métricas que `_simulate_realistic_all_strategies` para comparar
    directo contra la versión con precio del motor base.

    Secuencial en el orden en que vienen `matches` -- el CALLER es
    responsable de ordenarlos cronológicamente si eso importa (acá no
    se ordena por fecha para no asumir que `match_date` siempre viene
    poblado/parseable, a diferencia del backtest de TML-Database)."""
    capital = initial_capital
    busted = False
    trade_returns: list[float] = []
    hits = 0
    equity_curve = [initial_capital]

    for m in matches:
        if busted:
            break
        price = m["favorite_fair_price"]
        if price <= 0 or price >= 1:
            continue
        stake = capital * stake_pct
        fee_frac = (leg_fee_fraction(price) or 0.0) / price
        raw_pct = (1 - price) / price if m["favorite_won"] else -1.0
        net_pct = raw_pct - fee_frac

        capital += stake * net_pct
        trade_returns.append(net_pct)
        hits += 1 if m["favorite_won"] else 0
        if capital <= 0:
            capital = 0.0
            busted = True
        equity_curve.append(capital)

    n = len(trade_returns)
    avg_roi = statistics.mean(trade_returns) if trade_returns else None
    sharpe = None
    if n >= 2:
        stdev = statistics.stdev(trade_returns)
        if stdev > 0:
            sharpe = round(avg_roi / stdev, 4)

    peak = initial_capital
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)

    return {
        "n_trades": n, "winrate": round(hits / n, 4) if n else None,
        "final_capital_usdc": round(capital, 2),
        "roi_total_pct": round((capital / initial_capital - 1) * 100, 2),
        "avg_roi_per_trade_pct": round(avg_roi * 100, 4) if avg_roi is not None else None,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe_ratio_per_trade": sharpe,
        "busted": busted,
        "caveat": (
            "Precio de mercado REAL de-vigged (TennisExplorer), neto de "
            "fees reales de Kalshi. Sin cupo de concurrencia ni techo de "
            "liquidez (a diferencia de _simulate_realistic_all_strategies) "
            "-- agregar si se necesita comparar apples-to-apples."
        ),
    }


if __name__ == "__main__":
    matches = _load_priced_matches()
    print(f"partidos con cuota real en ambos lados: {len(matches)}")
    print(json.dumps(calibration_by_real_odds(matches), indent=2, ensure_ascii=False))
    print(json.dumps(run_strategy1_with_real_odds(matches), indent=2, ensure_ascii=False))
