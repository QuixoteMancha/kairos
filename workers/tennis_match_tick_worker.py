"""Worker periódico: para cada partido de tenis registrado y todavía
abierto (`database/historical/tennis/tennisscannedmatch.py`), guarda un
tick con el precio REAL de mercado de Kalshi y el `fair_a` que calcula
`TennisFModel` en ese mismo instante -- ver
`TennisMatchProbabilityTick` para el razonamiento completo (pedido
explícito del usuario 2026-07-31: calibrar el modelo de volatilidad
comparando la serie temporal de nuestra probabilidad estimada contra la
serie temporal real observada, sobre TODOS los partidos escaneados, no
solo los que tuvieron edge).

Cuando un mercado ya resolvió (`raw.result` real de Kalshi, 'yes'/'no'),
este worker completa `winner`/`settled_at` en `TennisScannedMatch` y dejar
de tickearlo (sale de `get_open_scanned_matches()` automáticamente).

Batching: UNA sola llamada `get_markets_by_tickers` para TODOS los
partidos abiertos en el ciclo (mismo patrón que
`workers/limitarb_monitor_worker.py`, ver memoria
kairos-limitarb-monitor-worker-unthrottled-burst-risk-2026-07-26) -- con
docenas de partidos abiertos simultáneos, evita 1 request por partido."""

import os
import time

from outer_apis.kalshi.client import KalshiAPI
from database.historical.tennis.matchtracking import (
    get_open_scanned_matches, save_probability_tick, mark_match_settled,
)
from models.fairprice.sports.tennis.tennisfmodel import TennisFModel

_REFRESH_INTERVAL_S = os.getenv("TENNIS_TICK_INTERVAL_SECONDS")
_DEFAULT_INTERVAL_S = 60  # "minuto a minuto", pedido explícito del usuario

_fmodel = TennisFModel()


def _rebuild_market_dict(scanned: dict, market_a: dict | None, market_b: dict | None) -> dict:
    """Reconstruye el dict shape que `TennisFModel.calculate_fair_price`
    espera, a partir de la fila registrada + las cotizaciones en vivo
    recién fetcheadas (no hace falta volver a llamar a build_tennis_markets)."""
    return {
        "current_a_price": (market_a or {}).get("current_a_price"),
        "current_b_price": (market_b or {}).get("current_b_price"),
        "player_a_rank": scanned["player_a_rank"],
        "player_b_rank": scanned["player_b_rank"],
        "player_a_name": scanned["player_a_name"],
        "player_b_name": scanned["player_b_name"],
        "tour": scanned["tour"],
        "surface": scanned["surface"],
        "tournament": scanned.get("tournament"),
        "best_of": scanned["best_of"],
    }


def _settlement_result(market: dict | None) -> str | None:
    """'yes' | 'no' | None -- lee `raw.result` real de Kalshi, mismo
    patrón que `limitarb_monitor_worker.py::_fetch_settlement_result`."""
    if market is None:
        return None
    result = (market.get("raw") or {}).get("result")
    return result if result in ("yes", "no") else None


def run_once() -> dict:
    open_matches = get_open_scanned_matches()
    if not open_matches:
        return {"open": 0, "ticked": 0, "settled": 0, "errors": 0}

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
            # Cross-ticket: alcanza con que UNA de las 2 piernas ya
            # reporte 'result' real -- ambas deberían coincidir (yes en
            # una <=> no en la otra), pero se lee la que esté disponible.
            settlement = result_a or result_b
            if settlement in ("yes", "no"):
                # ticker_a resuelto 'yes' -> ganó player_a; ticker_a
                # resuelto 'no' (o ticker_b 'yes') -> ganó player_b.
                if result_a == "yes" or result_b == "no":
                    winner = "a"
                else:
                    winner = "b"
                mark_match_settled(scanned["market_id"], winner)
                settled += 1
                continue  # ya resolvió, no hace falta tick de probabilidad

            market_price_a = (market_a or {}).get("current_a_price")

            model_fair_a = None
            try:
                rebuilt = _rebuild_market_dict(scanned, market_a, market_b)
                fair = _fmodel.calculate_fair_price(rebuilt)
                model_fair_a = fair.get("fair_a_price")
            except Exception:
                pass  # fail-closed por campo: el tick de precio real igual se guarda

            save_probability_tick(scanned["market_id"], market_price_a, model_fair_a)
            ticked += 1
        except Exception as e:
            errors += 1
            print(f"[tennis_match_tick_worker] error en {scanned.get('market_id')}: {e}")

    result = {"open": len(open_matches), "ticked": ticked, "settled": settled, "errors": errors}
    print(f"[tennis_match_tick_worker] {result}")
    return result


if __name__ == "__main__":
    if _REFRESH_INTERVAL_S:
        interval = int(_REFRESH_INTERVAL_S) if _REFRESH_INTERVAL_S.strip() else _DEFAULT_INTERVAL_S
        print(f"[tennis_match_tick_worker] loop mode, refresh_interval={interval}s")
        while True:
            try:
                run_once()
            except Exception as e:
                print(f"[tennis_match_tick_worker] ciclo con error: {e}")
            time.sleep(interval)
    else:
        run_once()
