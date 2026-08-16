"""Tests de `outer_apis/email_alert.py` -- 2026-08-11, pedido explícito
del usuario: "si no funciona con el proxy o no hay dinero en el proxy,
enviarme un correo a alertas@example.com". Mockea SMTP real, nunca
envía un correo de verdad."""

from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

import outer_apis.email_alert as email_alert


def setup_function(_fn):
    email_alert._last_sent.clear()


def _smtp_env(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")


def test_returns_false_when_smtp_not_configured(monkeypatch):
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    assert email_alert.send_email_alert("test", "body") is False


def test_sends_real_email_via_smtp(monkeypatch):
    _smtp_env(monkeypatch)
    mock_server = MagicMock()
    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_cls.return_value.__enter__.return_value = mock_server
        result = email_alert.send_email_alert("BrightData proxy no disponible", "detalle real")

    assert result is True
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with("sender@example.com", "app-password")
    mock_server.sendmail.assert_called_once()
    # destino default = alertas@example.com, pedido explícito del usuario
    to_arg = mock_server.sendmail.call_args[0][1]
    assert to_arg == ["alertas@example.com"]


def test_respects_custom_to_address(monkeypatch):
    _smtp_env(monkeypatch)
    monkeypatch.setenv("ALERT_EMAIL_TO", "otra@example.com")
    mock_server = MagicMock()
    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_cls.return_value.__enter__.return_value = mock_server
        email_alert.send_email_alert("x", "y")
    to_arg = mock_server.sendmail.call_args[0][1]
    assert to_arg == ["otra@example.com"]


def test_cooldown_prevents_spam_for_same_reason_key(monkeypatch):
    _smtp_env(monkeypatch)
    mock_server = MagicMock()
    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_cls.return_value.__enter__.return_value = mock_server
        first = email_alert.send_email_alert("A", "1", reason_key="proxy_down")
        second = email_alert.send_email_alert("A", "2", reason_key="proxy_down")

    assert first is True
    assert second is False  # en cooldown, no reenvía
    assert mock_server.sendmail.call_count == 1


def test_different_reason_keys_do_not_share_cooldown(monkeypatch):
    _smtp_env(monkeypatch)
    mock_server = MagicMock()
    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_cls.return_value.__enter__.return_value = mock_server
        first = email_alert.send_email_alert("A", "1", reason_key="reason_a")
        second = email_alert.send_email_alert("B", "2", reason_key="reason_b")

    assert first is True
    assert second is True
    assert mock_server.sendmail.call_count == 2


def test_cooldown_expires_after_window(monkeypatch):
    _smtp_env(monkeypatch)
    mock_server = MagicMock()
    with patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_cls.return_value.__enter__.return_value = mock_server
        email_alert.send_email_alert("A", "1", reason_key="k")
        # simula que ya pasó el cooldown
        email_alert._last_sent["k"] = time.time() - email_alert._COOLDOWN_S - 1
        second = email_alert.send_email_alert("A", "2", reason_key="k")

    assert second is True
    assert mock_server.sendmail.call_count == 2


def test_smtp_failure_returns_false_not_raises(monkeypatch):
    _smtp_env(monkeypatch)
    with patch("smtplib.SMTP", side_effect=Exception("connection refused")):
        result = email_alert.send_email_alert("A", "1")
    assert result is False
