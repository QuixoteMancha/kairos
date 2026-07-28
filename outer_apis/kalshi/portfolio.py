"""Wiring de SOLO LECTURA autenticado contra el portfolio real de Kalshi
(trade-api/v2/portfolio/*).

Contexto (2026-07-28): el usuario opera Kalshi MANUALMENTE, fuera de
Kairos (Kairos no coloca órdenes reales en ningún lado, ver
`outer_apis/kalshi.py` y `database/accounting/papertrade.py` -- línea
roja del proyecto). Este módulo existe para que una app de escritorio
separada (Kairos_Desktop, otro repo) pueda mostrarle su balance,
posiciones y fills reales y armar una curva de equity, vía
`inner_api/server.py` -> `GET /api/portfolio/kalshi`.

Separado deliberadamente de `outer_apis/kalshi.py` (que es 100% público,
sin auth, y así lo documenta su propio docstring) en vez de mezclarlo ahí,
para que ese archivo siga siendo verificable de un vistazo como "no firma
nada" -- cualquier lectura que requiera credenciales y firma vive acá.

ESTE MÓDULO SOLO HACE GET. No implementa (ni debe implementar sin
autorización explícita del usuario, confirmando cada paso) ningún
endpoint de escritura (POST/DELETE /portfolio/orders, etc.) -- esa es la
línea roja más dura del proyecto.

Auth (confirmado contra docs.kalshi.com, consultado 2026-07-28):
  - Headers: KALSHI-ACCESS-KEY (key id), KALSHI-ACCESS-SIGNATURE
    (firma base64), KALSHI-ACCESS-TIMESTAMP (unix ms).
  - Mensaje firmado: f"{timestamp_ms}{method}{path}" donde `path` es el
    path SIN query params pero CON el prefijo "/trade-api/v2" (ej.
    "/trade-api/v2/portfolio/balance"), method en mayúsculas ("GET").
  - Firma: RSA-PSS sobre SHA-256, MGF1(SHA-256), salt_length =
    digest_size (32 bytes) -- exactamente `padding.PSS(mgf=MGF1(SHA256),
    salt_length=PSS.DIGEST_LENGTH)` en el ejemplo oficial de Kalshi.

Endpoints usados:
  GET /trade-api/v2/portfolio/balance
  GET /trade-api/v2/portfolio/positions   (paginado por cursor)
  GET /trade-api/v2/portfolio/fills       (paginado por cursor)

Credenciales: `KALSHI_API_KEY_ID` y `KALSHI_PRIVATE_KEY` vía entorno/.env
(mismo patrón que `database/db.py`: `python-dotenv` si está disponible,
si no se asume que ya están en el entorno). `KALSHI_PRIVATE_KEY` acepta:
  - el PEM completo en la variable, con saltos de línea reales, o
  - el PEM con "\\n" literales (típico al pegarlo en un .env de una sola
    línea), o
  - un path a un archivo .pem en disco.
Fail-closed: si faltan credenciales, o si la clave no parsea, o si Kalshi
devuelve 401/403, se lanza una excepción explícita -- nunca se devuelven
datos vacíos en silencio.
"""

from __future__ import annotations

import base64
import os
import time

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
SIGNED_PATH_PREFIX = "/trade-api/v2"
DEFAULT_TIMEOUT_S = 10
DEFAULT_MAX_PAGES = 20
REQUEST_DELAY_S = 0.25


class KalshiCredentialsError(Exception):
    """Faltan KALSHI_API_KEY_ID / KALSHI_PRIVATE_KEY o la clave no es válida."""


class KalshiAuthError(Exception):
    """Kalshi rechazó la request autenticada (401/403)."""


class KalshiPortfolioAPI:
    """Cliente de solo lectura para /portfolio/* de Kalshi. Ver docstring
    de módulo para el detalle de firma. No agregar métodos de escritura
    acá sin autorización explícita del usuario."""

    def __init__(self, base_url: str = BASE_URL, timeout_s: int = DEFAULT_TIMEOUT_S):
        self.base_url = base_url
        self.timeout_s = timeout_s
        self.session: requests.Session | None = None
        self._key_id: str | None = None
        self._private_key = None

    def initialize(self):
        self.session = requests.Session()
        return self

    def _load_credentials(self):
        """Carga y cachea key id + private key parseada. Fail-closed:
        levanta KalshiCredentialsError con mensaje explícito en vez de
        dejar que un AttributeError críptico llegue más arriba, o peor,
        devolver silenciosamente datos vacíos."""
        if self._key_id and self._private_key is not None:
            return

        key_id = os.getenv("KALSHI_API_KEY_ID")
        pk_raw = os.getenv("KALSHI_PRIVATE_KEY")
        if not key_id or not pk_raw:
            raise KalshiCredentialsError(
                "KALSHI_API_KEY_ID y/o KALSHI_PRIVATE_KEY no están seteadas "
                "(env / .env). Sin esto no se puede leer el portfolio real "
                "de Kalshi -- no se devuelven datos vacíos en su lugar, "
                "cargá las credenciales en .env (ver comentario en la raíz "
                "del repo) y reintentá."
            )

        pk_pem = pk_raw.strip()
        if not pk_pem.startswith("-----BEGIN"):
            if os.path.isfile(pk_pem):
                with open(pk_pem, "r") as f:
                    pk_pem = f.read()
            else:
                # .env de una sola línea con "\n" literales en vez de
                # saltos de línea reales -- patrón común al pegar un PEM.
                pk_pem = pk_pem.replace("\\n", "\n")

        try:
            private_key = serialization.load_pem_private_key(
                pk_pem.encode(), password=None
            )
        except Exception as exc:
            raise KalshiCredentialsError(
                f"KALSHI_PRIVATE_KEY está seteada pero no parsea como clave "
                f"RSA PEM válida: {exc}"
            ) from exc

        self._key_id = key_id
        self._private_key = private_key

    def _signed_headers(self, method: str, path: str) -> dict:
        timestamp_ms = str(int(time.time() * 1000))
        message = f"{timestamp_ms}{method}{SIGNED_PATH_PREFIX}{path}".encode()
        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=hashes.SHA256().digest_size,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self._key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        }

    def _get(self, path: str, params: dict | None = None, retries: int = 3) -> dict:
        self._load_credentials()
        if self.session is None:
            self.initialize()

        for attempt in range(retries):
            headers = self._signed_headers("GET", path)  # timestamp debe ser fresco en cada intento
            resp = self.session.get(
                f"{self.base_url}{path}", params=params, headers=headers, timeout=self.timeout_s
            )
            if resp.status_code in (401, 403):
                raise KalshiAuthError(
                    f"Kalshi rechazó la autenticación ({resp.status_code}) en "
                    f"GET {path}: {resp.text[:300]}"
                )
            if resp.status_code == 429:
                time.sleep(REQUEST_DELAY_S * (2 ** attempt) + 1)
                continue
            resp.raise_for_status()
            return resp.json()

        resp.raise_for_status()
        return resp.json()

    def get_portfolio_balance(self) -> dict:
        """GET /portfolio/balance -- balance disponible y valor de
        portfolio, en centavos y en dólares (balance_dollars)."""
        return self._get("/portfolio/balance")

    def get_portfolio_positions(
        self,
        limit: int = 200,
        max_pages: int = DEFAULT_MAX_PAGES,
        ticker: str | None = None,
        event_ticker: str | None = None,
    ) -> dict:
        """GET /portfolio/positions, paginado por cursor. Devuelve
        {'market_positions': [...], 'event_positions': [...]} acumulando
        todas las páginas (Kalshi separa posiciones por mercado y por
        evento en la misma respuesta)."""
        market_positions: list[dict] = []
        event_positions: list[dict] = []
        cursor = None
        for _ in range(max_pages):
            params: dict = {"limit": limit}
            if ticker:
                params["ticker"] = ticker
            if event_ticker:
                params["event_ticker"] = event_ticker
            if cursor:
                params["cursor"] = cursor

            data = self._get("/portfolio/positions", params=params)
            market_positions.extend(data.get("market_positions") or [])
            event_positions.extend(data.get("event_positions") or [])

            cursor = data.get("cursor")
            if not cursor:
                break
            time.sleep(REQUEST_DELAY_S)

        return {"market_positions": market_positions, "event_positions": event_positions}

    def get_portfolio_fills(
        self,
        limit: int = 200,
        max_pages: int = DEFAULT_MAX_PAGES,
        ticker: str | None = None,
        min_ts: int | None = None,
        max_ts: int | None = None,
    ) -> list[dict]:
        """GET /portfolio/fills, paginado por cursor. `min_ts`/`max_ts` en
        unix seconds (filtro de rango de fecha opcional para no traer todo
        el historial en cada request). Devuelve la lista cruda de fills
        (cada uno con fill_id, ticker, yes_price_dollars/no_price_dollars,
        is_taker, fee_cost, created_time, ts) -- sin recalcular PnL acá,
        ver docstring de inner_api/server.py sobre esa decisión."""
        fills: list[dict] = []
        cursor = None
        for _ in range(max_pages):
            params: dict = {"limit": limit}
            if ticker:
                params["ticker"] = ticker
            if min_ts is not None:
                params["min_ts"] = min_ts
            if max_ts is not None:
                params["max_ts"] = max_ts
            if cursor:
                params["cursor"] = cursor

            data = self._get("/portfolio/fills", params=params)
            page_fills = data.get("fills") or []
            fills.extend(page_fills)

            cursor = data.get("cursor")
            if not cursor or not page_fills:
                break
            time.sleep(REQUEST_DELAY_S)

        return fills
