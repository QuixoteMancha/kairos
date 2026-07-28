"""Tests de outer_apis/kalshi_portfolio.py -- SOLO mockea requests, nunca
pega contra la API real de Kalshi.

Cubre:
  - firma RSA-PSS se genera y verifica correctamente contra una clave de
    prueba (no confiamos en "no tiró excepción", verificamos la firma con
    la public key correspondiente).
  - manejo de credenciales faltantes (KalshiCredentialsError, fail-closed).
  - manejo de 401 de Kalshi (KalshiAuthError).
  - paginación de /portfolio/positions y /portfolio/fills.
"""
from __future__ import annotations

import base64
from unittest.mock import patch, MagicMock

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from outer_apis.kalshi.portfolio import (
    KalshiAuthError,
    KalshiCredentialsError,
    KalshiPortfolioAPI,
)


@pytest.fixture
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return private_key, pem


@pytest.fixture
def env_with_creds(monkeypatch, rsa_keypair):
    _, pem = rsa_keypair
    monkeypatch.setenv("KALSHI_API_KEY_ID", "test-key-id")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", pem)
    return pem


def _mock_response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    if status_code >= 400 and status_code not in (401, 403, 429):
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


def test_missing_credentials_fail_closed(monkeypatch):
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY", raising=False)
    client = KalshiPortfolioAPI()
    with pytest.raises(KalshiCredentialsError):
        client.get_portfolio_balance()


def test_invalid_private_key_fails_closed(monkeypatch):
    monkeypatch.setenv("KALSHI_API_KEY_ID", "test-key-id")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", "not-a-real-pem")
    client = KalshiPortfolioAPI()
    with pytest.raises(KalshiCredentialsError):
        client.get_portfolio_balance()


def test_falls_back_to_volume_files_when_env_vars_absent(monkeypatch, rsa_keypair, tmp_path):
    """Sin KALSHI_API_KEY_ID/KALSHI_PRIVATE_KEY en el entorno, debe caer a
    los archivos fijos del volumen (/data/secrets/...) en vez de fallar --
    este es el mecanismo que evita tener que recrear el contenedor solo
    para inyectar 2 credenciales (ver docstring de _load_credentials)."""
    import outer_apis.kalshi.portfolio as portfolio_module

    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY", raising=False)

    _, pem = rsa_keypair
    key_id_file = tmp_path / "kalshi_api_key_id.txt"
    key_id_file.write_text("volume-key-id\n")
    pem_file = tmp_path / "kalshi_key.pem"
    pem_file.write_text(pem)

    monkeypatch.setattr(portfolio_module, "DEFAULT_KEY_ID_PATH", str(key_id_file))
    monkeypatch.setattr(portfolio_module, "DEFAULT_PRIVATE_KEY_PATH", str(pem_file))

    client = KalshiPortfolioAPI()
    client._load_credentials()

    assert client._key_id == "volume-key-id"
    assert client._private_key is not None


def test_signature_is_valid_rsa_pss(env_with_creds, rsa_keypair, monkeypatch):
    """Verifica que la firma generada realmente verifica contra la public
    key correspondiente, con el mensaje exacto que exige la doc de Kalshi:
    f'{timestamp_ms}{METHOD}/trade-api/v2{path}'."""
    private_key, _ = rsa_keypair
    public_key = private_key.public_key()

    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["headers"] = headers
        captured["url"] = url
        return _mock_response(200, {"balance": 1000, "balance_dollars": "10.00"})

    client = KalshiPortfolioAPI()
    client.initialize()
    client.session.get = fake_get

    result = client.get_portfolio_balance()
    assert result == {"balance": 1000, "balance_dollars": "10.00"}

    headers = captured["headers"]
    assert headers["KALSHI-ACCESS-KEY"] == "test-key-id"
    timestamp_ms = headers["KALSHI-ACCESS-TIMESTAMP"]
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])

    expected_message = f"{timestamp_ms}GET/trade-api/v2/portfolio/balance".encode()
    # No debe lanzar InvalidSignature.
    public_key.verify(
        signature,
        expected_message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=hashes.SHA256().digest_size,
        ),
        hashes.SHA256(),
    )


def test_401_raises_auth_error(env_with_creds):
    client = KalshiPortfolioAPI()
    client.initialize()
    client.session.get = MagicMock(
        return_value=_mock_response(401, text="unauthorized")
    )
    with pytest.raises(KalshiAuthError):
        client.get_portfolio_balance()


def test_403_raises_auth_error(env_with_creds):
    client = KalshiPortfolioAPI()
    client.initialize()
    client.session.get = MagicMock(
        return_value=_mock_response(403, text="forbidden")
    )
    with pytest.raises(KalshiAuthError):
        client.get_portfolio_positions()


def test_positions_pagination(env_with_creds):
    pages = [
        {
            "market_positions": [{"ticker": "A"}],
            "event_positions": [],
            "cursor": "page2",
        },
        {
            "market_positions": [{"ticker": "B"}],
            "event_positions": [{"event_ticker": "EVT"}],
            "cursor": "",
        },
    ]
    calls = {"n": 0}

    def fake_get(url, params=None, headers=None, timeout=None):
        page = pages[calls["n"]]
        calls["n"] += 1
        return _mock_response(200, page)

    client = KalshiPortfolioAPI()
    client.initialize()
    client.session.get = fake_get

    with patch("outer_apis.kalshi.portfolio.time.sleep"):
        result = client.get_portfolio_positions()

    assert calls["n"] == 2
    assert [p["ticker"] for p in result["market_positions"]] == ["A", "B"]
    assert [p["event_ticker"] for p in result["event_positions"]] == ["EVT"]


def test_fills_pagination_stops_on_empty_page(env_with_creds):
    pages = [
        {"fills": [{"fill_id": "1"}, {"fill_id": "2"}], "cursor": "page2"},
        {"fills": [], "cursor": "page3"},  # cursor presente pero sin fills -> corta
    ]
    calls = {"n": 0}

    def fake_get(url, params=None, headers=None, timeout=None):
        page = pages[calls["n"]]
        calls["n"] += 1
        return _mock_response(200, page)

    client = KalshiPortfolioAPI()
    client.initialize()
    client.session.get = fake_get

    with patch("outer_apis.kalshi.portfolio.time.sleep"):
        fills = client.get_portfolio_fills()

    assert calls["n"] == 2
    assert [f["fill_id"] for f in fills] == ["1", "2"]


def test_fills_passes_ts_range_params(env_with_creds):
    captured_params = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured_params.update(params or {})
        return _mock_response(200, {"fills": [], "cursor": ""})

    client = KalshiPortfolioAPI()
    client.initialize()
    client.session.get = fake_get

    client.get_portfolio_fills(min_ts=1000, max_ts=2000)
    assert captured_params["min_ts"] == 1000
    assert captured_params["max_ts"] == 2000
