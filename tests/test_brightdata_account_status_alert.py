"""Tests de `check_account_status`/`alert_if_account_broken`
(brightdata_proxy.py) -- 2026-08-11, pedido explícito del usuario: "si
no funciona con el proxy o no hay dinero en el proxy, enviarme un
correo". Usa el endpoint real de BrightData `GET /status`
(`can_make_requests`/`auth_fail_reason`), documentado oficialmente.
Mockea toda request HTTP y el envío de correo."""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from database.db import init_db, db
from database.accounting.systemhaltstate import SystemHaltState, is_halted
from outer_apis.scrapers.brightdata_proxy import (
    check_account_status, alert_if_account_broken,
)
import outer_apis.email_alert as email_alert


@pytest.fixture(autouse=True)
def clean_state():
    email_alert._last_sent.clear()
    init_db()
    with db.connection_context():
        SystemHaltState.delete().execute()
    yield
    with db.connection_context():
        SystemHaltState.delete().execute()


def _fake_response(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


def test_check_account_status_returns_parsed_json():
    with patch("requests.get", return_value=_fake_response({"can_make_requests": True, "status": "active"})):
        status = check_account_status("tok")
    assert status == {"can_make_requests": True, "status": "active"}


def test_check_account_status_fail_closed_on_request_exception():
    import requests as requests_module

    with patch("requests.get", side_effect=requests_module.RequestException("timeout")):
        assert check_account_status("tok") is None


def test_check_account_status_fail_closed_on_bad_json():
    resp = MagicMock()
    resp.json.side_effect = ValueError("not json")
    with patch("requests.get", return_value=resp):
        assert check_account_status("tok") is None


def test_alert_if_account_broken_sends_email_with_real_reason(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    mock_server = MagicMock()
    with patch(
        "requests.get",
        return_value=_fake_response({"can_make_requests": False, "auth_fail_reason": "insufficient_balance"}),
    ), patch("smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_cls.return_value.__enter__.return_value = mock_server
        broken = alert_if_account_broken("tok")

    assert broken is True
    mock_server.sendmail.assert_called_once()
    # el cuerpo real viene base64-encoded (MIMEText detecta acentos ->
    # charset no-ASCII) -- se decodifica para confirmar que el motivo
    # REAL reportado por BrightData (no inventado) llegó al correo.
    import email

    raw = mock_server.sendmail.call_args[0][2]
    parsed = email.message_from_string(raw)
    decoded_body = parsed.get_payload(decode=True).decode("utf-8")
    assert "insufficient_balance" in decoded_body


def test_alert_if_account_broken_does_nothing_when_account_healthy(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    with patch(
        "requests.get", return_value=_fake_response({"can_make_requests": True}),
    ), patch("smtplib.SMTP") as mock_smtp_cls:
        broken = alert_if_account_broken("tok")

    assert broken is False
    mock_smtp_cls.assert_not_called()


def test_alert_if_account_broken_fail_closed_when_status_unknown(monkeypatch):
    # no se pudo determinar el estado -- nunca alarma sin certeza.
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    import requests as requests_module

    with patch("requests.get", side_effect=requests_module.RequestException("timeout")), \
         patch("smtplib.SMTP") as mock_smtp_cls:
        broken = alert_if_account_broken("tok")

    assert broken is False
    mock_smtp_cls.assert_not_called()


def test_alert_if_account_broken_halts_operations_when_reason_looks_like_no_funds(monkeypatch):
    # 2026-08-11, pedido explícito del usuario: "si efectivamente se
    # comprueba que no hay fondos, se deben interrumpir todas las
    # operaciones".
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    with patch(
        "requests.get",
        return_value=_fake_response({"can_make_requests": False, "auth_fail_reason": "insufficient_balance"}),
    ), patch("smtplib.SMTP"):
        alert_if_account_broken("tok")

    halted, reason = is_halted()
    assert halted is True
    assert reason == "brightdata_account_broken"


def test_alert_if_account_broken_does_not_halt_for_unrelated_reason(monkeypatch):
    # un problema de config de zona (no de plata) NO debe frenar todo
    # el sistema -- solo el correo de aviso.
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    with patch(
        "requests.get",
        return_value=_fake_response({"can_make_requests": False, "auth_fail_reason": "zone_not_found"}),
    ), patch("smtplib.SMTP"):
        alert_if_account_broken("tok")

    halted, _ = is_halted()
    assert halted is False


def test_alert_if_account_broken_clears_halt_when_account_recovers(monkeypatch):
    from database.accounting.systemhaltstate import halt_operations

    halt_operations("brightdata_account_broken")
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    with patch(
        "requests.get", return_value=_fake_response({"can_make_requests": True}),
    ), patch("smtplib.SMTP"):
        alert_if_account_broken("tok")

    halted, _ = is_halted()
    assert halted is False


def test_alert_if_account_broken_does_not_clear_unrelated_halt(monkeypatch):
    # si el sistema estaba frenado por OTRO motivo, recuperar la cuenta
    # de BrightData no debe levantar ese freno ajeno.
    from database.accounting.systemhaltstate import halt_operations

    halt_operations("some_other_reason")
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    with patch(
        "requests.get", return_value=_fake_response({"can_make_requests": True}),
    ), patch("smtplib.SMTP"):
        alert_if_account_broken("tok")

    halted, reason = is_halted()
    assert halted is True
    assert reason == "some_other_reason"
