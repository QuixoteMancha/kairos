"""Tests de autenticación RSA-PSS de Kalshi (2026-08-02, pedido
explícito del usuario). Usa una clave RSA de PRUEBA generada en memoria
-- nunca la clave real de `/data/secrets/`."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

import outer_apis.kalshi.auth as auth_module
from outer_apis.kalshi.auth import sign_request, has_credentials


@pytest.fixture
def fake_credentials(tmp_path):
    """Genera un Key ID + clave RSA de PRUEBA reales (criptográficamente
    válidos, pero sin ninguna relación con la cuenta real de Kalshi) y
    los apunta como si fueran los archivos de `/data/secrets/`."""
    key_id_path = tmp_path / "kalshi_api_key_id.txt"
    key_path = tmp_path / "kalshi_key.pem"

    key_id_path.write_text("test-key-id-1234\n")

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path.write_bytes(pem)

    auth_module._cached_key_id = None
    auth_module._cached_private_key = None
    with patch.object(auth_module, "_KEY_ID_PATH", key_id_path), \
         patch.object(auth_module, "_PRIVATE_KEY_PATH", key_path):
        yield private_key
    auth_module._cached_key_id = None
    auth_module._cached_private_key = None


@pytest.fixture(autouse=True)
def reset_cache():
    auth_module._cached_key_id = None
    auth_module._cached_private_key = None
    yield
    auth_module._cached_key_id = None
    auth_module._cached_private_key = None


def test_has_credentials_false_without_real_files():
    with patch.object(auth_module, "_KEY_ID_PATH", Path("/tmp/nonexistent-kalshi-key-id")), \
         patch.object(auth_module, "_PRIVATE_KEY_PATH", Path("/tmp/nonexistent-kalshi-key.pem")):
        assert has_credentials() is False


def test_sign_request_none_without_real_files():
    with patch.object(auth_module, "_KEY_ID_PATH", Path("/tmp/nonexistent-kalshi-key-id")), \
         patch.object(auth_module, "_PRIVATE_KEY_PATH", Path("/tmp/nonexistent-kalshi-key.pem")):
        assert sign_request("GET", "/trade-api/v2/markets") is None


def test_has_credentials_true_with_real_test_key(fake_credentials):
    assert has_credentials() is True


def test_sign_request_produces_valid_headers(fake_credentials):
    headers = sign_request("GET", "/trade-api/v2/markets")
    assert headers is not None
    assert headers["KALSHI-ACCESS-KEY"] == "test-key-id-1234"
    assert headers["KALSHI-ACCESS-TIMESTAMP"].isdigit()
    assert len(headers["KALSHI-ACCESS-SIGNATURE"]) > 0


def test_signature_verifies_against_real_message(fake_credentials):
    """Verifica que la firma generada es REALMENTE válida -- la
    verificamos con la clave pública correspondiente, no solo que el
    campo exista."""
    headers = sign_request("GET", "/trade-api/v2/markets")
    message = f"{headers['KALSHI-ACCESS-TIMESTAMP']}GET/trade-api/v2/markets".encode()
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    public_key = fake_credentials.public_key()

    # No debe lanzar excepción si la firma es válida.
    public_key.verify(
        signature, message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
        hashes.SHA256(),
    )


def test_signature_fails_verification_with_tampered_message(fake_credentials):
    from cryptography.exceptions import InvalidSignature

    headers = sign_request("GET", "/trade-api/v2/markets")
    tampered_message = f"{headers['KALSHI-ACCESS-TIMESTAMP']}GET/trade-api/v2/portfolio".encode()
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    public_key = fake_credentials.public_key()

    with pytest.raises(InvalidSignature):
        public_key.verify(
            signature, tampered_message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
            hashes.SHA256(),
        )


def test_credentials_are_cached_across_calls(fake_credentials):
    # Precalienta el cache con las credenciales de prueba reales.
    sign_request("GET", "/trade-api/v2/markets")
    assert auth_module._cached_key_id is not None

    # Con el cache ya tibio, un path roto NO debería importar -- el
    # segundo llamado no debería volver a tocar disco.
    with patch.object(auth_module, "_KEY_ID_PATH") as mock_path:
        mock_path.exists.return_value = True
        sign_request("GET", "/trade-api/v2/portfolio")
        mock_path.exists.assert_not_called()
