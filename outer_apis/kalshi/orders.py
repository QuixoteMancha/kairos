"""Wiring de ESCRITURA autenticado contra Kalshi (trade-api/v2/portfolio/
events/orders) -- coloca/cancela órdenes reales con dinero real.

CONTEXTO (2026-07-30): hasta hoy Kairos NUNCA colocó una orden real (línea
roja del proyecto, ver `strategies/limit_arb.py::execute_order_placement`,
que hasta este commit era un no-op documentado). El usuario autorizó
explícitamente, paso a paso, una PRUEBA puntual con capital real (5% del
balance real de Kalshi) para validar la estrategia de dwell-time/limit-arb
en un solo partido de tenis. Este módulo es el wiring mínimo que lo hace
posible -- NO se invoca automáticamente desde ningún worker/scan existente,
solo desde `scripts/live_limit_arb_test.py` (explícito, con confirmación
manual antes de tocar dinero real).

Auth: idéntico patrón RSA-PSS-SHA256 que `outer_apis/kalshi/portfolio.py`
(mismas credenciales, mismo fallback a /data/secrets/*). El mensaje firmado
sigue siendo f"{timestamp_ms}{METHOD}{path}" -- Kalshi NO incluye el body
en la firma, confirmado contra el mismo patrón usado en las llamadas GET
ya verificadas en producción.

Schema de creación de orden (confirmado contra docs.kalshi.com V2,
2026-07-30, POST /trade-api/v2/portfolio/events/orders):
  - side: "bid" (comprar YES al precio dado) | "ask" (vender YES al precio
    dado -- económicamente equivalente a comprar NO a 1-price). NO existe
    un ticker separado para "no": todo se expresa sobre el ticker YES con
    side/price relativo a YES.
  - count: cantidad de contratos, string fixed-point (ej. "5.00").
  - price: dólares por contrato YES, string fixed-point (ej. "0.4600").
  - time_in_force: "immediate_or_cancel" (equivalente a "market order" --
    se llena lo que se pueda YA contra el book y se cancela el resto) |
    "good_till_canceled" (queda como límite hasta cancelarse o llenarse) |
    "fill_or_kill".
  - client_order_id: UUID por request, para idempotencia -- reusarlo
    devuelve la orden original en vez de crear una nueva (protección
    contra doble-submit por reintentos).

Fail-closed: cualquier respuesta que no sea 201 se propaga como excepción
explícita, nunca se asume éxito. No hay reintentos automáticos en POST
(a diferencia del cliente de lectura) -- un reintento silencioso de una
orden es exactamente el tipo de bug que duplica exposición real."""

from __future__ import annotations

import base64
import time
import uuid

import requests

from outer_apis.kalshi.portfolio import (
    BASE_URL,
    SIGNED_PATH_PREFIX,
    DEFAULT_TIMEOUT_S,
    KalshiCredentialsError,
    KalshiAuthError,
)
from outer_apis.kalshi.portfolio import KalshiPortfolioAPI


class KalshiOrderError(Exception):
    """Kalshi rechazó la orden (400/422) o la respuesta no fue la esperada."""


class KalshiOrdersAPI(KalshiPortfolioAPI):
    """Extiende el cliente de solo-lectura ya verificado en producción con
    los 3 métodos de escritura mínimos necesarios para la prueba: colocar
    orden IOC (market-like), colocar orden GTC (limit), y cancelar. Hereda
    `_load_credentials`/`_signed_headers` sin cambios -- misma auth, mismas
    credenciales, mismo fallback a /data/secrets/*."""

    def _post(self, path: str, body: dict) -> dict:
        self._load_credentials()
        if self.session is None:
            self.initialize()

        headers = self._signed_headers("POST", path)
        headers["Content-Type"] = "application/json"
        resp = self.session.post(
            f"{self.base_url}{path}", json=body, headers=headers, timeout=self.timeout_s
        )
        if resp.status_code in (401, 403):
            raise KalshiAuthError(
                f"Kalshi rechazó la autenticación ({resp.status_code}) en "
                f"POST {path}: {resp.text[:300]}"
            )
        if resp.status_code not in (200, 201):
            raise KalshiOrderError(
                f"Kalshi rechazó la orden ({resp.status_code}) en POST {path}: "
                f"{resp.text[:500]}"
            )
        return resp.json()

    def _delete(self, path: str) -> dict:
        self._load_credentials()
        if self.session is None:
            self.initialize()

        headers = self._signed_headers("DELETE", path)
        resp = self.session.delete(
            f"{self.base_url}{path}", headers=headers, timeout=self.timeout_s
        )
        if resp.status_code in (401, 403):
            raise KalshiAuthError(
                f"Kalshi rechazó la autenticación ({resp.status_code}) en "
                f"DELETE {path}: {resp.text[:300]}"
            )
        if resp.status_code not in (200, 204):
            raise KalshiOrderError(
                f"Kalshi rechazó la cancelación ({resp.status_code}) en "
                f"DELETE {path}: {resp.text[:500]}"
            )
        return resp.json() if resp.text else {}

    def place_order(
        self,
        ticker: str,
        side: str,
        count: str,
        price: str,
        time_in_force: str,
        client_order_id: str | None = None,
        self_trade_prevention_type: str = "taker_at_cross",
    ) -> dict:
        """POST /portfolio/events/orders. Fail-closed: valida side/
        time_in_force contra los únicos valores válidos antes de llamar a
        Kalshi -- un typo acá no debe convertirse en una orden mal
        formada silenciosamente rechazada o, peor, aceptada con una
        interpretación inesperada."""
        if side not in ("bid", "ask"):
            raise ValueError(f"side inválido: {side!r} (debe ser 'bid' o 'ask')")
        if time_in_force not in ("immediate_or_cancel", "good_till_canceled", "fill_or_kill"):
            raise ValueError(f"time_in_force inválido: {time_in_force!r}")

        body = {
            "ticker": ticker,
            "client_order_id": client_order_id or str(uuid.uuid4()),
            "side": side,
            "count": count,
            "price": price,
            "time_in_force": time_in_force,
            "self_trade_prevention_type": self_trade_prevention_type,
        }
        return self._post("/portfolio/events/orders", body)

    def cancel_order(self, order_id: str) -> dict:
        return self._delete(f"/portfolio/events/orders/{order_id}")
