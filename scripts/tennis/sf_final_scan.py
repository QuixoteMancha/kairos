"""
Escaneo de TODAS las semifinales/finales ATP+WTA disponibles en el
historial de Kalshi (2026-08-01, pedido explícito del usuario) --
jugadores, resultado real, estadísticas de partido (Flashscore),
probabilidad histórica real (trade tape de Kalshi), edad, lesiones,
fatiga.

INVESTIGACIÓN KALSHI API (pedido explícito del usuario: "investigar si
se puede buscar match por nombre o semifinal y final de torneos")
-- CONFIRMADO EN VIVO 2026-08-01: NO. `GET /markets` (ver
`outer_apis/kalshi/client.py::get_markets`) solo acepta
`status`/`series_ticker`/`limit`/`cursor` como parámetros reales -- no
existe ningún filtro server-side por nombre de jugador, título, ni
ronda. Hay que traer TODOS los mercados `status=settled` de cada serie
(paginado con cursor hasta agotarlo) y filtrar CLIENT-SIDE por el texto
del título real (`outer_apis/kalshi/markets/tennis.py::_extract_round`,
mismo extractor ya usado para el backtest de convergencia SF/Final).

LIMITACIÓN DE RETENCIÓN (verificada en vivo, NO es un límite de este
script): Kalshi solo mantiene mercados `settled` consultables hacia
atrás hasta aproximadamente 2026-05-25 para KXATPMATCH (~2 meses reales
desde hoy) -- el cursor se agota ahí, no importa cuántas páginas se
pidan. "Todas las semifinales/finales de este año" en la práctica es
"de los últimos ~2 meses que Kalshi todavía expone", no del año
calendario completo -- no hay forma de recuperar lo anterior vía la API
pública.

PROBABILIDAD HISTÓRICA REAL: vía `KalshiAPI.get_trades(ticker=...)`
(trade tape público, mismo endpoint que ya usa
`fill_verification_worker`) -- serie real de `(created_time,
yes_price_dollars)` por cada trade ejecutado en cada pierna. Se resume a
open/close/min/max por pierna (guardar cada trade individual sería
demasiado volumen para un reporte).

COSTO REAL A TENER EN CUENTA: por cada match encontrado, se hace 1
fetch a Flashscore (BrightData, vía `find_match_id`+
`get_match_statistics`) para las estadísticas reales de partido -- con
potencialmente varias decenas de matches en la ventana de ~2 meses, esto
consume proxy real (mismo endpoint ya auditado, ver memoria
kairos-brightdata-*). Usar `fetch_stats=False` / `--no-stats` para un
primer conteo sin ese costo antes de correr el scan completo.

Guarda el resultado en `sf_final_scan_result.json` (no en DB -- es un
reporte de una sola vez para investigación, no un dato que necesite
persistencia estructurada todavía)."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from outer_apis.kalshi.client import KalshiAPI
from outer_apis.kalshi.markets.tennis import _extract_round, _extract_player_name
from outer_apis.scrapers.tennis.tournament_surface import extract_tournament_name
from outer_apis.scrapers.tennis.flashscore_scraper import find_match_id, get_match_statistics
from outer_apis.sports.tennis.player_age import get_age
from outer_apis.sports.tennis.injury_risk import get_injury_risk_flag
from outer_apis.sports.tennis.fatigue import calculate_fatigue_index

_SERIES = ["KXATPMATCH", "KXWTAMATCH"]
_TARGET_ROUNDS = {"semifinal", "final"}


def _group_by_event(markets: list[dict]) -> dict[str, list[dict]]:
    by_event: dict[str, list[dict]] = {}
    for m in markets:
        event_ticker = (m.get("raw") or {}).get("event_ticker")
        if not event_ticker:
            continue
        by_event.setdefault(event_ticker, []).append(m)
    return by_event


def _parse_match_date(close_time: str | None) -> date | None:
    if not close_time:
        return None
    try:
        return datetime.fromisoformat(close_time.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _trade_price_summary(api: KalshiAPI, ticker: str) -> dict[str, float | int | None]:
    """Fail-closed: todos los valores None si no hay trades reales para
    este ticker (mercado purgado, o nunca tuvo actividad)."""
    trades = api.get_trades(ticker=ticker, max_pages=10)
    prices = []
    for t in trades:
        raw = t.get("yes_price_dollars")
        if raw is None:
            continue
        try:
            prices.append(float(raw))
        except (TypeError, ValueError):
            continue
    if not prices:
        return {"trade_count": 0, "price_open": None, "price_close": None, "price_min": None, "price_max": None}
    # get_trades devuelve más reciente primero (ver docstring del cliente).
    return {
        "trade_count": len(prices),
        "price_close": prices[0],
        "price_open": prices[-1],
        "price_min": min(prices),
        "price_max": max(prices),
    }


def scan_sf_final_matches(fetch_stats: bool = True) -> list[dict[str, Any]]:
    api = KalshiAPI()
    api.initialize()

    results: list[dict[str, Any]] = []
    for series in _SERIES:
        markets = api.get_markets(status="settled", series_ticker=series, max_pages=50)
        by_event = _group_by_event(markets)

        for event_ticker, legs in by_event.items():
            if len(legs) != 2:
                continue

            round_name = _extract_round(legs[0]["raw"])
            if not round_name or round_name.strip().lower() not in _TARGET_ROUNDS:
                continue

            names = [_extract_player_name(leg["raw"]) for leg in legs]
            if not all(names):
                continue

            rules_secondary = legs[0]["raw"].get("rules_secondary")
            tournament = extract_tournament_name(rules_secondary)
            match_date = _parse_match_date(legs[0].get("close_time"))

            result_a = (legs[0].get("raw") or {}).get("result")
            result_b = (legs[1].get("raw") or {}).get("result")
            winner = None
            if result_a == "yes" or result_b == "no":
                winner = names[0]
            elif result_b == "yes" or result_a == "no":
                winner = names[1]

            entry: dict[str, Any] = {
                "event_ticker": event_ticker,
                "series": series,
                "round": round_name,
                "tournament": tournament,
                "match_date": match_date.isoformat() if match_date else None,
                "player_a": names[0],
                "player_b": names[1],
                "winner": winner,
                "ticker_a": legs[0].get("id"),
                "ticker_b": legs[1].get("id"),
            }

            for label, name in (("player_a", names[0]), ("player_b", names[1])):
                entry[f"{label}_age"] = get_age(name, as_of=match_date) if match_date else get_age(name)
                entry[f"{label}_injury"] = get_injury_risk_flag(name)
                fatigue = (
                    calculate_fatigue_index(name, tournament, match_date)
                    if (tournament and match_date) else None
                )
                entry[f"{label}_fatigue_index"] = fatigue["fatigue_index"] if fatigue else None

            for label in ("player_a", "player_b"):
                summary = _trade_price_summary(api, entry[f"ticker_{label[-1]}"])
                for k, v in summary.items():
                    entry[f"{label}_{k}"] = v

            if fetch_stats and match_date:
                match_id = find_match_id(names[0], names[1], match_date.isoformat(), tournament)
                entry["flashscore_match_id"] = match_id
                entry["flashscore_stats"] = get_match_statistics(match_id) if match_id else None
            else:
                entry["flashscore_match_id"] = None
                entry["flashscore_stats"] = None

            results.append(entry)

    return results


if __name__ == "__main__":
    import sys

    fetch_stats = "--no-stats" not in sys.argv
    data = scan_sf_final_matches(fetch_stats=fetch_stats)
    print(f"Encontrados {len(data)} partidos SF/Final settled (ventana real disponible en Kalshi).")

    out_path = "sf_final_scan_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Guardado en {out_path}")
