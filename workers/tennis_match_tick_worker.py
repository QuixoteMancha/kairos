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
docenas de partidos abiertos simultáneos, evita 1 request por partido.

FUSIONADO 2026-08-02 (pedido explícito del usuario -- evitar "nave
espacial con tantos módulos separados en contenedores diferentes" para
tareas livianas): este ciclo también llama a
`workers.set1_favorite_reaction_worker.check_and_save_set1_signal` por
cada partido abierto -- mismo dato (`get_open_scanned_matches`), misma
cadencia de 60s, sin necesidad de un contenedor propio. Aislado en SU
PROPIO try/except (ver más abajo) para que un fallo de TennisExplorer
(scraping no oficial, sin SLA) nunca bloquee ni degrade el tick de
precio real de Kalshi, que es más crítico."""

import os
import random
import threading
import time

from outer_apis.kalshi.client import KalshiAPI
from database.historical.tennis.matchtracking import (
    get_open_scanned_matches, save_probability_tick, mark_match_settled,
)
from models.fairprice.sports.tennis.tennisfmodel import TennisFModel
from outer_apis.sports.tennis.match_history import set_fetch_allowlist
from workers.set1_favorite_reaction_worker import check_and_save_set1_signal
from database.historical.tennis import save_next_match, has_real_match_detail_between

_REFRESH_INTERVAL_S = os.getenv("TENNIS_TICK_INTERVAL_SECONDS")
_DEFAULT_INTERVAL_S = 60  # "minuto a minuto", pedido explícito del usuario

_fmodel = TennisFModel()

# BUG REAL encontrado y corregido 2026-08-01 (reportado por el usuario:
# "siguen habiendo muchos requests al proxy"): `match_history._fetch_allowlist`
# es una variable global A NIVEL DE PROCESO -- `engine.py` la fija en cada
# ciclo de scan (contenedor `kairos`), pero este worker corre en un
# contenedor SEPARADO con su propia copia en None (default = sin
# restricción). Como este worker llama `TennisFModel.calculate_fair_price`
# para los ~55 partidos abiertos cada 60s, terminaba disparando fetch en
# vivo para CUALQUIER jugador con caché stale (post 00:00 GMT), sin
# importar si su partido era nuevo -- anulando por completo el filtro de
# "solo fetch para partidos nuevos" pedido explícitamente por el usuario
# (ver database/historical/tennis/matchtracking.py::filter_new_markets).
# Fijar la allowlist vacía UNA VEZ al arrancar el proceso: este worker
# nunca debe autorizar fetch en vivo -- su fair_a es "best effort" con lo
# que ya esté cacheado, el fetch real es responsabilidad exclusiva del
# ciclo de engine.py.
set_fetch_allowlist(set())


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
        return {"open": 0, "ticked": 0, "settled": 0, "errors": 0, "set1_processed": 0, "set1_errors": 0}

    api = KalshiAPI()
    api.initialize()

    all_tickers = [t for m in open_matches for t in (m["ticker_a"], m["ticker_b"])]
    market_cache = api.get_markets_by_tickers(all_tickers)

    # Compartidos entre TODOS los partidos del ciclo (ver
    # check_and_save_set1_signal) -- evita pedir el ranking/score en
    # vivo repetido por cada match del mismo tour.
    set1_rank_cache: dict = {}
    set1_live_cache: dict = {}
    set1_processed = set1_errors_count = 0

    ticked = settled = errors = 0
    for scanned in open_matches:
        try:
            if check_and_save_set1_signal(scanned, set1_rank_cache, set1_live_cache) == "processed":
                set1_processed += 1
        except Exception as e:
            set1_errors_count += 1
            print(f"[tennis_match_tick_worker] error set1_reaction en {scanned.get('market_id')}: {e}")

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
                # 2026-08-08, pedido explícito del usuario -- el fetch de
                # stats vía Flashscore ya NO vive acá (removido: este
                # contenedor solo debe tickear probabilidades reales de
                # Kalshi). Esa responsabilidad pasó a
                # `workers/tennis_match_history_sync_worker.py`
                # (`fill_missing_stats_for_player`, vinculado a
                # `MatchHistory` en vez de `market_id`).
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

    result = {
        "open": len(open_matches), "ticked": ticked, "settled": settled, "errors": errors,
        "set1_processed": set1_processed, "set1_errors": set1_errors_count,
    }
    print(f"[tennis_match_tick_worker] {result}")
    return result


# 2026-08-09, pedido explícito del usuario: "como los matches se
# fetchean antes que empiecen desde Kalshi puedes poner delays para los
# requests de tennis explorer de manera asíncrona, por un lado el
# guardado de los ticks, por otro el fill del match con la data de
# tennis explorer, pero todo en el mismo worker" -- separa el fill de
# identidad/tour/level vía TennisExplorer (con delay real, red no
# oficial, sin SLA) del tick de precio real de Kalshi (`run_once`,
# rápido, síncrono, cada 60s, NUNCA debe esperar a TennisExplorer).
# Corre en un hilo daemon aparte, con su propio ciclo e intervalo --
# NO es el script de backfill masivo (`tennis_match_history_unified_
# backfill.py`, que ataca el backlog histórico completo de 300K+
# candidatos); acá el alcance es acotado y real: solo los jugadores de
# partidos que Kalshi YA tiene abiertos (`get_open_scanned_matches`,
# probablemente unas pocas decenas), la mayoría de los cuales YA se
# resuelven en 1-2 ciclos y dejan de pedirse de nuevo (`has_real_match_
# detail_between`, chequeo sin red).
_ENRICHMENT_CYCLE_INTERVAL_S = 90.0
_ENRICHMENT_DELAY_MIN_S = 1.0
_ENRICHMENT_DELAY_MAX_S = 3.5


def run_enrichment_once() -> dict:
    """1 pasada: para cada partido abierto cuyo par de jugadores AÚN no
    tiene una fila `MatchHistory` con identidad real resuelta, intenta
    `save_next_match` (cubre el caso real señalado por el usuario: el
    partido detectado por Kalshi todavía no aparece en la tabla de
    resultados de TennisExplorer porque no se jugó, pero SÍ puede estar
    en su sección "Next match"). Delay real entre cada request -- nunca
    bloquea ni compite con el tick de precio de Kalshi (hilo separado)."""
    open_matches = get_open_scanned_matches()
    pending = [
        m for m in open_matches
        if not has_real_match_detail_between(m["player_a_name"], m["player_b_name"])
    ]
    resolved = failed = 0
    for i, m in enumerate(pending):
        try:
            ok = save_next_match(m["player_a_name"], expected_opponent_hint=m["player_b_name"])
        except Exception:
            ok = False
        resolved += 1 if ok else 0
        failed += 0 if ok else 1
        if i < len(pending) - 1:
            time.sleep(random.uniform(_ENRICHMENT_DELAY_MIN_S, _ENRICHMENT_DELAY_MAX_S))
    result = {"open": len(open_matches), "pending": len(pending), "resolved": resolved, "failed": failed}
    print(f"[tennis_match_tick_worker] enrichment {result}")
    return result


def _run_enrichment_loop(interval_s: float = _ENRICHMENT_CYCLE_INTERVAL_S) -> None:
    while True:
        try:
            run_enrichment_once()
        except Exception as e:
            print(f"[tennis_match_tick_worker] enrichment ciclo con error: {e}")
        time.sleep(interval_s)


if __name__ == "__main__":
    if _REFRESH_INTERVAL_S:
        interval = int(_REFRESH_INTERVAL_S) if _REFRESH_INTERVAL_S.strip() else _DEFAULT_INTERVAL_S
        print(f"[tennis_match_tick_worker] loop mode, refresh_interval={interval}s")
        threading.Thread(target=_run_enrichment_loop, daemon=True).start()
        while True:
            try:
                run_once()
            except Exception as e:
                print(f"[tennis_match_tick_worker] ciclo con error: {e}")
            time.sleep(interval)
    else:
        run_once()
