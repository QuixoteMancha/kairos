"""
PROBE EXPLORATORIO — desechable — Fase 0 del plan crypto_15m Strategy 1 variant.

Ver docs/crypto-15m-kalshi-strategy1-variant-plan-2026-07-11.md.

Hace llamadas REALES (no mockeadas) contra:
  - Kalshi (external-api.kalshi.com): /series (category=Crypto), /markets
    (series_ticker=KXBTC15M), /events, /markets/trades (con y sin filtro de
    ticker/rango de tiempo), /series/{s}/markets/{t}/candlesticks.
  - Binance: /api/v3/klines (BTCUSDT, 1m).

Autora: Priya. Fecha: 2026-07-11. NO se corre en producción — es investigación
de una sola vez. Recomendación de qué hacer con este archivo al final de este
mismo output de la agente que lo escribió (ver reporte a Sara), no se decide
acá.

Requiere red saliente real. Si falla por falta de red, el output lo va a
mostrar explícitamente (no hay manejo silencioso de errores acá a propósito:
este script es para ver qué pasa, no para ocultarlo).
"""

from __future__ import annotations

import json
import time

import requests

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
BINANCE_BASE = "https://api.binance.com/api/v3"


def _print_header(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def probe_kalshi_series_crypto() -> list[dict]:
    _print_header("1. Kalshi /series?category=Crypto — buscar series *_15M / fifteen_min")
    r = requests.get(f"{KALSHI_BASE}/series", params={"category": "Crypto"}, timeout=15)
    r.raise_for_status()
    series = r.json().get("series", [])
    fifteen = [s for s in series if s.get("frequency") == "fifteen_min"]
    print(f"total series en categoria Crypto: {len(series)}")
    print(f"series con frequency == 'fifteen_min': {len(fifteen)}")
    for s in fifteen:
        print(f"  {s['ticker']:20s} | {s['title']}")
    return fifteen


def probe_kalshi_markets_15m(series_ticker: str = "KXBTC15M") -> list[dict]:
    _print_header(f"2. Kalshi /markets?series_ticker={series_ticker} — patron de ticker y cadencia")
    r = requests.get(
        f"{KALSHI_BASE}/markets",
        params={"series_ticker": series_ticker, "limit": 30},
        timeout=15,
    )
    r.raise_for_status()
    markets = r.json().get("markets", [])
    print(f"n markets devueltos: {len(markets)}")
    for m in markets[:10]:
        print(f"  {m.get('ticker'):30s} open={m.get('open_time')} close={m.get('close_time')} status={m.get('status')}")
    # Overlap check: ¿el close_time de uno == open_time del siguiente?
    times = sorted((m["open_time"], m["close_time"]) for m in markets if m.get("open_time") and m.get("close_time"))
    overlaps = 0
    for i in range(1, len(times)):
        if times[i][0] < times[i - 1][1]:
            overlaps += 1
    print(f"ventanas con overlap detectado: {overlaps} / {len(times)}")
    return markets


def probe_kalshi_events_15m(series_ticker: str = "KXBTC15M") -> None:
    _print_header(f"3. Kalshi /events?series_ticker={series_ticker}&status=open")
    r = requests.get(
        f"{KALSHI_BASE}/events",
        params={"series_ticker": series_ticker, "status": "open", "limit": 10},
        timeout=15,
    )
    r.raise_for_status()
    print(json.dumps(r.json(), indent=2)[:1500])


def probe_kalshi_trades(ticker: str) -> None:
    _print_header(f"4. Kalshi /markets/trades — shape, filtro por ticker, paginacion, rate limit")

    r = requests.get(f"{KALSHI_BASE}/markets/trades", params={"ticker": ticker, "limit": 5}, timeout=15)
    r.raise_for_status()
    d = r.json()
    print(f"filtro por ticker='{ticker}' -> n trades: {len(d.get('trades', []))}, cursor: {bool(d.get('cursor'))}")
    if d.get("trades"):
        print("  ejemplo de trade:")
        print(json.dumps(d["trades"][0], indent=4))
    print(f"headers relevantes: {[h for h in r.headers if 'rate' in h.lower()]} (vacio == Kalshi no expone rate-limit headers en este endpoint)")

    # Paginacion
    cursor = d.get("cursor")
    if cursor:
        r2 = requests.get(
            f"{KALSHI_BASE}/markets/trades",
            params={"ticker": ticker, "limit": 5, "cursor": cursor},
            timeout=15,
        )
        d2 = r2.json()
        print(f"pagina 2 via cursor -> n trades: {len(d2.get('trades', []))}, distinto de pagina 1: "
              f"{d2['trades'][0]['trade_id'] != d['trades'][0]['trade_id'] if d2.get('trades') and d.get('trades') else 'n/a'}")

    # Filtro por rango de tiempo (min_ts/max_ts)
    now = int(time.time())
    r3 = requests.get(
        f"{KALSHI_BASE}/markets/trades",
        params={"ticker": ticker, "min_ts": now + 3600, "limit": 5},
        timeout=15,
    )
    print(f"min_ts en el futuro (+1h) -> n trades: {len(r3.json().get('trades', []))} (0 esperado si el filtro es real)")

    # Sin filtro de ticker -> feed global de TODOS los mercados
    r4 = requests.get(f"{KALSHI_BASE}/markets/trades", params={"limit": 5}, timeout=15)
    d4 = r4.json()
    print(f"sin filtro de ticker -> feed global, tickers vistos: {[t.get('ticker') for t in d4.get('trades', [])]}")

    # Rate limit real: burst de N llamadas seguidas, medir latencia y códigos
    _print_header("4b. Kalshi /markets/trades — burst de 30 llamadas seguidas (sin auth)")
    codes = []
    t0 = time.time()
    for _ in range(30):
        rb = requests.get(f"{KALSHI_BASE}/markets/trades", params={"limit": 1}, timeout=10)
        codes.append(rb.status_code)
    elapsed = time.time() - t0
    from collections import Counter
    print(f"30 llamadas en {elapsed:.2f}s (~{30/elapsed:.2f} req/s sostenido) -> codigos: {Counter(codes)}")
    if 429 in codes:
        print("  RATE LIMITED — ver headers Retry-After en la corrida real")


def probe_kalshi_candlesticks(series_ticker: str, ticker: str) -> None:
    _print_header(f"5. Kalshi candlesticks nativas — /series/{series_ticker}/markets/{ticker}/candlesticks")
    now = int(time.time())
    r = requests.get(
        f"{KALSHI_BASE}/series/{series_ticker}/markets/{ticker}/candlesticks",
        params={"period_interval": 1, "start_ts": now - 3600, "end_ts": now},
        timeout=15,
    )
    print(f"status: {r.status_code}")
    if r.ok:
        candles = r.json().get("candlesticks", [])
        print(f"n candlesticks (1min, ultima hora): {len(candles)}")
        if candles:
            print("  ejemplo:")
            print(json.dumps(candles[0], indent=4))
    else:
        print(r.text[:500])


def probe_binance_klines() -> None:
    _print_header("6. Binance /api/v3/klines — shape, timestamp semantics, rate limit")
    r = requests.get(
        f"{BINANCE_BASE}/klines",
        params={"symbol": "BTCUSDT", "interval": "1m", "limit": 5},
        timeout=15,
    )
    print(f"status: {r.status_code}")
    print(f"X-MBX-USED-WEIGHT-1M: {r.headers.get('X-MBX-USED-WEIGHT-1M')}")
    klines = r.json()
    print(f"n klines: {len(klines)}")
    if klines:
        print("ejemplo de kline (array, no dict):")
        print(json.dumps(klines[0], indent=2))
        print("campos: [open_time_ms, open, high, low, close, volume, close_time_ms, quote_asset_volume, "
              "n_trades, taker_buy_base_vol, taker_buy_quote_vol, ignore]")


def main() -> None:
    fifteen_min_series = probe_kalshi_series_crypto()
    series_ticker = fifteen_min_series[0]["ticker"] if fifteen_min_series else "KXBTC15M"

    markets = probe_kalshi_markets_15m(series_ticker)
    probe_kalshi_events_15m(series_ticker)

    if markets:
        sample_ticker = markets[0]["ticker"]
        probe_kalshi_trades(sample_ticker)
        probe_kalshi_candlesticks(series_ticker, sample_ticker)

    probe_binance_klines()


if __name__ == "__main__":
    main()
