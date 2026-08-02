"""Autenticación real de Kalshi (RSA-PSS) -- 2026-08-02, pedido explícito
del usuario para el scanner de MarketArb vía websocket (el websocket de
Kalshi requiere auth incluso para canales públicos, a diferencia del
REST de solo lectura que usamos hasta ahora, que es público sin
credenciales).

Esquema real confirmado (investigación 2026-08-02, docs.kalshi.com):
  - Headers: `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-SIGNATURE`,
    `KALSHI-ACCESS-TIMESTAMP`.
  - Mensaje a firmar: `f"{timestamp_ms}{method}{path}"` (path SIN query
    params, timestamp en milisegundos Unix).
  - Firma: RSA-PSS, SHA-256, salt length = largo del digest (32 para
    SHA-256), resultado en base64.

CREDENCIALES REALES: viven en `/data/secrets/` (volumen `kairos_data_v2`,
ya montado en el contenedor `kairos` en `/data`) -- `kalshi_api_key_id.txt`
y `kalshi_key.pem`. Colocadas ahí el 2026-07-28, nunca antes usadas por
ningún código de Kairos (primera vez que se usan credenciales reales de
Kalshi para algo, confirmado 2026-08-02: cero variables KALSHI_* en el
entorno del contenedor antes de este cambio).

SOLO LECTURA -- esto autentica canales de datos (websocket), NO coloca
ni firma órdenes de trading. La línea roja del proyecto (sin ejecución
real) sigue vigente; este módulo no la toca."""

from __future__ import annotations

import base64
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

_SECRETS_DIR = Path("/data/secrets")
_KEY_ID_PATH = _SECRETS_DIR / "kalshi_api_key_id.txt"
_PRIVATE_KEY_PATH = _SECRETS_DIR / "kalshi_key.pem"

_cached_key_id: str | None = None
_cached_private_key = None


def _load_credentials():
    """Fail-closed: None, None si los archivos no existen o no se
    pueden parsear -- nunca fabrica una credencial falsa. Cacheado en
    memoria del proceso (la clave no cambia en caliente)."""
    global _cached_key_id, _cached_private_key
    if _cached_key_id is not None and _cached_private_key is not None:
        return _cached_key_id, _cached_private_key

    if not _KEY_ID_PATH.exists() or not _PRIVATE_KEY_PATH.exists():
        return None, None

    try:
        key_id = _KEY_ID_PATH.read_text().strip()
        private_key = serialization.load_pem_private_key(
            _PRIVATE_KEY_PATH.read_bytes(), password=None,
        )
    except (ValueError, OSError):
        return None, None

    _cached_key_id, _cached_private_key = key_id, private_key
    return key_id, private_key


def has_credentials() -> bool:
    key_id, private_key = _load_credentials()
    return key_id is not None and private_key is not None


def sign_request(method: str, path: str) -> dict[str, str] | None:
    """Headers reales de autenticación para una request a `path`
    (SIN query params) con método `method` ('GET'/'POST'/etc.).

    Fail-closed: None si no hay credenciales reales disponibles --
    nunca firma con una clave inventada."""
    key_id, private_key = _load_credentials()
    if key_id is None or private_key is None:
        return None

    timestamp_ms = str(int(time.time() * 1000))
    message = f"{timestamp_ms}{method.upper()}{path}".encode("utf-8")

    signature = private_key.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
        hashes.SHA256(),
    )

    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("ascii"),
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
    }
