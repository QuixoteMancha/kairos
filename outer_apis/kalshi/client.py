"""Wiring de solo lectura contra la API pública de Kalshi (trade-api/v2).

IMPORTANTE: este módulo NUNCA coloca órdenes reales. Solo lee mercados y
order books públicos. La ejecución real de órdenes es una línea roja del
proyecto (requiere auth firmada + confirmación explícita del usuario en cada
paso) y todavía no existe en Kairos — no la agregues acá sin autorización
explícita.

Endpoints usados (públicos, no requieren API key):
  GET /trade-api/v2/markets
  GET /trade-api/v2/markets/{ticker}
  GET /trade-api/v2/markets/{ticker}/orderbook
  GET /trade-api/v2/markets/trades

Nota (2026-07-28): para leer el portfolio REAL del usuario (balance,
posiciones, fills -- el usuario opera Kalshi manualmente, fuera de
Kairos) se agregó un módulo separado, `outer_apis/kalshi_portfolio.py`,
que sí requiere auth firmada (RSA-PSS) pero sigue siendo SOLO LECTURA.
Se mantuvo separado de este archivo a propósito para que este módulo siga
siendo 100% público/sin auth de un vistazo.
"""

import time

import requests

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
DEFAULT_TIMEOUT_S = 10
DEFAULT_MAX_PAGES = 5
REQUEST_DELAY_S = 0.25


class KalshiAPI:
    def __init__(self, base_url: str = BASE_URL, timeout_s: int = DEFAULT_TIMEOUT_S):
        self.base_url = base_url
        self.timeout_s = timeout_s
        self.session = None

    def initialize(self):
        """Prepara la sesión HTTP. No hay auth: los endpoints de lectura de
        mercados son públicos. Se deja como método separado (en vez de
        armarlo en __init__) para dejar el punto de extensión listo el día
        que se agregue auth firmada para escritura de órdenes."""
        self.session = requests.Session()
        return self

    def _get(self, path: str, params: dict | None = None, retries: int = 3) -> dict:
        if self.session is None:
            self.initialize()
        for attempt in range(retries):
            resp = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout_s)
            if resp.status_code == 429:
                # backoff exponencial simple ante rate limit de Kalshi
                time.sleep(REQUEST_DELAY_S * (2 ** attempt) + 1)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return resp.json()

    def get_markets(self, status: str = "open", limit: int = 200, series_ticker: str | None = None,
                     max_pages: int = DEFAULT_MAX_PAGES) -> list[dict]:
        """Trae mercados abiertos y los normaliza al shape interno que
        consumen las estrategias/modelos de Kairos:
            {id, type, ticker, current_a_price, current_b_price,
             close_time, raw}
        Pagina con cursor hasta agotar resultados, hasta que Kalshi deje de
        devolver cursor, o hasta `max_pages` (tope defensivo: Kalshi tiene
        miles de mercados abiertos y sin tope el scan cold-cache es lento y
        dispara rate-limit — ver nota de memoria sobre scan bottleneck)."""
        markets: list[dict] = []
        cursor = None
        for page in range(max_pages):
            params = {"status": status, "limit": limit}
            if series_ticker:
                params["series_ticker"] = series_ticker
            if cursor:
                params["cursor"] = cursor

            data = self._get("/markets", params=params)
            for raw in data.get("markets", []):
                normalized = self._normalize_market(raw)
                if normalized is not None:
                    markets.append(normalized)

            cursor = data.get("cursor")
            if not cursor or not data.get("markets"):
                break
            time.sleep(REQUEST_DELAY_S)

        return markets

    def get_market(self, ticker: str) -> dict | None:
        data = self._get(f"/markets/{ticker}")
        raw = data.get("market")
        if raw is None:
            return None
        return self._normalize_market(raw)

    def get_market_any_status(self, ticker: str) -> dict | None:
        """GET /markets?tickers=<ticker> -- a diferencia de get_market()
        (GET /markets/{ticker}, que 404ea una vez el mercado se purga tras
        liquidarse), este endpoint de LISTADO devuelve el mercado incluso ya
        liquidado/finalizado, con 'result' ('yes'|'no') y 'status'
        ('finalized' cuando liquidado) en el dict crudo -- ver
        self._normalize_market, que ya preserva todo el dict crudo bajo
        'raw'. Devuelve None si Kalshi realmente no tiene ningún registro de
        ese ticker (nunca existió, o error genuino) -- fail-closed, no
        fabricar."""
        data = self._get("/markets", params={"tickers": ticker})
        raws = data.get("markets") or []
        if len(raws) != 1:
            return None
        raw = raws[0]
        normalized = self._normalize_market(raw)
        if normalized is not None:
            return normalized
        # Mercado finalizado: puede no tener yes_ask/no_ask válidos (sin
        # cotizaciones activas ya liquidado) y por eso _normalize_market
        # descarta fail-closed -- igual devolvemos un dict mínimo con 'raw'
        # intacto para que el caller pueda leer raw.get('result')/'status'.
        return {
            "id": raw.get("ticker"),
            "ticker": raw.get("ticker"),
            "type": _infer_category(raw),
            "current_a_price": None,
            "current_b_price": None,
            "close_time": raw.get("close_time"),
            "raw": raw,
        }

    def get_markets_by_tickers(self, tickers: list[str], chunk_size: int = 200) -> dict[str, dict]:
        """Batched lookup: UNA llamada GET /markets?tickers=t1,t2,...,tN
        (chunkeada cada `chunk_size` para no armar una URL gigante) en vez
        de N llamadas GET /markets/{ticker} secuenciales.

        Construida para limitarb_monitor_worker.py (ver memoria
        kairos-limitarb-monitor-worker-unthrottled-burst-risk-2026-07-26):
        con cientos de señales abiertas mapeando a un puñado de tickers
        únicos (mismo partido referenciado por muchas señales), el patrón
        anterior de 1 call por señal por leg desperdiciaba la enorme
        mayoría de las llamadas en el MISMO ticker repetido.

        Usa el mismo endpoint de LISTADO que get_market_any_status() (no
        GET /markets/{ticker}, que 404ea una vez el mercado se purga tras
        liquidarse) -- por eso este único método reemplaza tanto el
        get_market() como el fallback get_market_any_status() de una sola
        vez: la respuesta ya trae el mercado esté vivo o ya finalizado
        (con 'result'/'status' en el raw), verificado contra la API real.

        Devuelve {ticker: market_normalizado}. Un ticker ausente del dict
        de retorno significa que Kalshi genuinamente no tiene ningún
        registro de él (nunca existió) -- fail-closed, el caller debe
        tratar la ausencia como 'market_gone', igual que antes.

        Mercados finalizados sin cotización activa (yes_ask/no_ask
        ausentes) no se descartan silenciosamente como en
        _normalize_market: se devuelve un dict mínimo con 'raw' intacto
        (mismo shape de fallback que get_market_any_status), para que el
        caller pueda leer raw.get('result')."""
        unique_tickers = list(dict.fromkeys(t for t in tickers if t))
        result: dict[str, dict] = {}
        for i in range(0, len(unique_tickers), chunk_size):
            chunk = unique_tickers[i:i + chunk_size]
            data = self._get("/markets", params={"tickers": ",".join(chunk)})
            for raw in data.get("markets") or []:
                ticker = raw.get("ticker")
                if not ticker:
                    continue
                normalized = self._normalize_market(raw)
                if normalized is not None:
                    result[ticker] = normalized
                else:
                    # Finalizado/sin cotización activa -- mismo fallback
                    # mínimo que get_market_any_status.
                    result[ticker] = {
                        "id": ticker,
                        "ticker": ticker,
                        "type": _infer_category(raw),
                        "current_a_price": None,
                        "current_b_price": None,
                        "close_time": raw.get("close_time"),
                        "raw": raw,
                    }
            if i + chunk_size < len(unique_tickers):
                time.sleep(REQUEST_DELAY_S)
        return result

    def get_orderbook(self, ticker: str) -> dict:
        """Devuelve el order book crudo de Kalshi (niveles yes/no en centavos).
        Usado por market_arb (MarketArb) para evaluar liquidez real, no solo
        last-price."""
        return self._get(f"/markets/{ticker}/orderbook")

    def get_trades(self, ticker: str | None = None, min_ts: int | None = None,
                    max_ts: int | None = None, limit: int = 200,
                    max_pages: int = DEFAULT_MAX_PAGES) -> list[dict]:
        """Trae trades públicos reales de `/markets/trades` (per-trade ticks,
        no order book). Pagina con cursor igual que get_markets, hasta que
        Kalshi deje de devolver cursor o hasta `max_pages`. Fail-closed: no
        inventa trades — si la respuesta viene sin 'trades', devuelve lo que
        haya acumulado hasta ahí en vez de fallar toda la colecta."""
        trades: list[dict] = []
        cursor = None
        for page in range(max_pages):
            params: dict = {"limit": limit}
            if ticker:
                params["ticker"] = ticker
            if min_ts is not None:
                params["min_ts"] = min_ts
            if max_ts is not None:
                params["max_ts"] = max_ts
            if cursor:
                params["cursor"] = cursor

            data = self._get("/markets/trades", params=params)
            page_trades = data.get("trades") or []
            trades.extend(page_trades)

            cursor = data.get("cursor")
            if not cursor or not page_trades:
                break
            time.sleep(REQUEST_DELAY_S)

        return trades

    @staticmethod
    def _normalize_market(raw: dict) -> dict | None:
        """Fail-closed: si faltan precios o vienen fuera de [0,1], se
        descarta el mercado en vez de dejarlo pasar con datos inventados
        (nunca aprobar por ausencia de información).

        CRÍTICO (bug real encontrado y corregido 2026-07-20, ver memoria
        kairos-kalshi-bid-vs-ask-critical-bug): `current_a_price`/
        `current_b_price` deben ser el precio ASK (lo que realmente cuesta
        COMPRAR ahora vía market order), NUNCA el bid. Usar el bid (lo que
        alguien pagaría por VENDER) subestima el costo real de ejecutar
        ambas patas de un arb — en mercados nuevos/ilíquidos el spread
        bid/ask puede ser enorme (ej. bid 0.08 / ask 0.93), y sumar bids de
        dos tickets complementarios da un "arb" que en realidad no es
        ejecutable a esos precios. `current_b_price` es el ask real de
        `no_ask_dollars` (NO `1 - yes_price`, que ignora el spread por
        completo). Tampoco se usa `last_price_dollars` como fallback: en
        mercados recién abiertos sin trades puede venir en 0.0000 aunque
        haya cotizaciones bid/ask reales, y usarlo inventaría un precio
        todavía peor que el bid."""
        try:
            yes_ask = raw.get("yes_ask_dollars")
            no_ask = raw.get("no_ask_dollars")
            if yes_ask is None or no_ask is None:
                return None
            yes_ask = float(yes_ask)
            no_ask = float(no_ask)
        except (TypeError, ValueError):
            return None

        if not (0.0 <= yes_ask <= 1.0) or not (0.0 <= no_ask <= 1.0):
            return None

        return {
            "id": raw.get("ticker"),
            "ticker": raw.get("ticker"),
            "type": _infer_category(raw),
            "current_a_price": yes_ask,
            "current_b_price": no_ask,
            "close_time": raw.get("close_time"),
            "raw": raw,
        }


def _infer_category(raw: dict) -> str:
    """Heurística simple sobre el ticker/event_ticker para asignar la
    categoría que consumen los modelos de volatility/fairprice. Fail-closed:
    si no reconoce el prefijo, devuelve 'unknown' (los modelos deben tratar
    'unknown' como sin fair-price-model disponible, no inventar uno)."""
    ticker = (raw.get("event_ticker") or raw.get("ticker") or "").upper()

    prefixes = {
        "KXMLB": "baseball",
        "KXNBA": "basketball",
        "KXNCAAB": "basketball",
        "KXNFL": "american_football",
        "KXNHL": "hockey",
        "KXATP": "tennis",
        "KXWTA": "tennis",
        "KXTEMP": "temperature",
        "KXHIGH": "temperature",
        "KXCPI": "inflation",
        "KXUNEMP": "unemployment",
        "KXGDP": "gdp",
        "KXBTC": "btc",
        "KXETH": "eth",
        "KXSOL": "sol",
        "KXXRP": "xrp",
        "KXDOGE": "doge",
        "KXBNB": "bnb",
        "KXINX": "sp500",
        "KXNASDAQ100": "nasdaq",
        "KXDJI": "dowj",
    }
    for prefix, category in prefixes.items():
        if ticker.startswith(prefix):
            return category
    return "unknown"
