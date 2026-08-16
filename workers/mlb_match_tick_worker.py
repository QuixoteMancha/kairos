"""Worker periódico: para cada partido de MLB registrado y todavía
abierto (`database/historical/team_sports/mlbscannedmatch.py`), guarda
un tick con el precio REAL de mercado de Kalshi y el `fair_a` que
calcula `BaseballFModel` en ese mismo instante -- 2026-08-05, pedido
explícito del usuario: "añade a la captura de probabilidades tick by
tick, las probabilidades de MLB en el worker que tenemos" (mismo
mecanismo que `workers/tennis_match_tick_worker.py`, adaptado -- MLB no
tiene equivalente de "set1 reaction", así que ese bloque no aplica acá).

Por qué esto no existía antes: Kairos YA descubre y escanea mercados
reales de MLB (`outer_apis/kalshi/markets/team_sport.py`, series
`KXMLBGAME`, usados por `LimitArb`/`MarketArb` en cada ciclo de
`engine.py`), pero nunca se REGISTRABAN en una tabla de tracking propia
-- a diferencia de tenis (`TennisScannedMatch`, pedido explícito
2026-07-31). Este worker completa esa pieza: registro + ticks
periódicos, mismo propósito real -- construir la serie temporal real
para poder comparar después (ver la conversación de esta sesión sobre
si el mercado de Kalshi subvalora una ventaja de 3+ carreras en la 7ma
entrada, algo que SOLO se puede responder con datos reales de precio en
vivo, no con datos históricos gratuitos -- investigado y descartado,
ver Obsidian "Backtest Favorito MLB - Kairos.md").

Batching: UNA sola llamada `get_markets_by_tickers` para TODOS los
partidos abiertos en el ciclo (mismo patrón que
`workers/tennis_match_tick_worker.py`/`workers/limitarb_monitor_worker.py`)."""

import os
import time

from outer_apis.kalshi.client import KalshiAPI
from outer_apis.kalshi.markets.team_sport import build_team_sport_markets
from database.historical.team_sports.mlbmatchtracking import (
    filter_new_mlb_markets, register_scanned_mlb_matches,
    get_open_scanned_mlb_matches, save_mlb_probability_tick, mark_mlb_match_settled,
)
from models.fairprice.sports.baseball.baseballfmodel import BaseballFModel
from database.db import init_db, init_db_historical
from database.accounting.systemhaltstate import is_halted

_REFRESH_INTERVAL_S = os.getenv("MLB_TICK_INTERVAL_SECONDS")
_DEFAULT_INTERVAL_S = 60  # mismo criterio que tenis -- "minuto a minuto"
_MLB_SERIES = "KXMLBGAME"

_fmodel = BaseballFModel()


def _register_new_mlb_matches() -> int:
    """Descubre mercados MLB actuales de Kalshi y registra los que
    todavía no estén en `MlbScannedMatch` -- este worker es
    autosuficiente (no depende de que `engine.py` ya los haya
    registrado en su propio ciclo de scan, aunque también podría
    llamarse desde ahí en el futuro)."""
    api = KalshiAPI()
    api.initialize()
    legs = api.get_markets(series_ticker=_MLB_SERIES)
    markets = build_team_sport_markets(legs, _MLB_SERIES)
    new_markets = filter_new_mlb_markets(markets)
    register_scanned_mlb_matches(new_markets)
    return len(new_markets)


def _rebuild_market_dict(scanned: dict, market_a: dict | None, market_b: dict | None) -> dict:
    return {
        "current_a_price": (market_a or {}).get("current_a_price"),
        "current_b_price": (market_b or {}).get("current_b_price"),
        "team_a_rating": scanned["team_a_rating"], "team_b_rating": scanned["team_b_rating"],
    }


def _settlement_result(market: dict | None) -> str | None:
    """'yes' | 'no' | None -- lee `raw.result` real de Kalshi, mismo
    patrón que `tennis_match_tick_worker.py::_settlement_result`."""
    if market is None:
        return None
    result = (market.get("raw") or {}).get("result")
    return result if result in ("yes", "no") else None


def run_once() -> dict:
    # 2026-08-11, pedido explícito del usuario ("interrumpir todas las
    # operaciones" si se confirma falta de fondos) -- mismo criterio que
    # tennis_match_tick_worker.py.
    init_db()
    init_db_historical()
    halted, reason = is_halted()
    if halted:
        print(f"[mlb_match_tick_worker] OPERACIONES DETENIDAS (motivo: {reason}) -- ciclo omitido")
        return {"halted": True, "reason": reason}

    registered = 0
    try:
        registered = _register_new_mlb_matches()
    except Exception as e:
        print(f"[mlb_match_tick_worker] error registrando mercados nuevos: {e}")

    open_matches = get_open_scanned_mlb_matches()
    if not open_matches:
        return {"registered": registered, "open": 0, "ticked": 0, "settled": 0, "errors": 0}

    api = KalshiAPI()
    api.initialize()

    all_tickers = [t for m in open_matches for t in (m["ticker_a"], m["ticker_b"])]
    market_cache = api.get_markets_by_tickers(all_tickers)

    ticked = settled = errors = 0
    for scanned in open_matches:
        try:
            market_a = market_cache.get(scanned["ticker_a"])
            market_b = market_cache.get(scanned["ticker_b"])

            result_a = _settlement_result(market_a)
            result_b = _settlement_result(market_b)
            settlement = result_a or result_b
            if settlement in ("yes", "no"):
                winner = "a" if (result_a == "yes" or result_b == "no") else "b"
                mark_mlb_match_settled(scanned["market_id"], winner)
                settled += 1
                continue

            market_price_a = (market_a or {}).get("current_a_price")

            model_fair_a = None
            try:
                rebuilt = _rebuild_market_dict(scanned, market_a, market_b)
                fair = _fmodel.calculate_fair_price(rebuilt)
                model_fair_a = fair.get("fair_a_price")
            except Exception:
                pass  # fail-closed por campo: el tick de precio real igual se guarda

            save_mlb_probability_tick(scanned["market_id"], market_price_a, model_fair_a)
            ticked += 1
        except Exception as e:
            errors += 1
            print(f"[mlb_match_tick_worker] error en {scanned.get('market_id')}: {e}")

    result = {"registered": registered, "open": len(open_matches), "ticked": ticked, "settled": settled, "errors": errors}
    print(f"[mlb_match_tick_worker] {result}")
    return result


if __name__ == "__main__":
    if _REFRESH_INTERVAL_S:
        interval = int(_REFRESH_INTERVAL_S) if _REFRESH_INTERVAL_S.strip() else _DEFAULT_INTERVAL_S
        print(f"[mlb_match_tick_worker] loop mode, refresh_interval={interval}s")
        while True:
            try:
                run_once()
            except Exception as e:
                print(f"[mlb_match_tick_worker] ciclo con error: {e}")
            time.sleep(interval)
    else:
        run_once()
