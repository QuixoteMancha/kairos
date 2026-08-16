"""Tests de `fetch_with_ip_block_fallback` (brightdata_proxy.py) --
2026-08-11, pedido explícito del usuario: "los scrapers deben tener la
característica de detectar cuando los sitios bloquean posiblemente por
IP y en ese caso entrar al fallback de lanzar el request proxeado. Por
ende, se necesita whitelistear la IP, si es que el proxy responde
unauthorized". Mockea toda request HTTP real (directa y proxeada)."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

from outer_apis.scrapers.brightdata_proxy import fetch_with_ip_block_fallback


def _fake_response(status_code=200, text="ok"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


def test_returns_direct_response_when_no_block_detected():
    # 200 real -- nunca debe tocar el proxy.
    with patch("requests.get", return_value=_fake_response(200, "real content")) as mock_get, \
         patch("outer_apis.scrapers.brightdata_proxy.request_via_proxy_with_ip_retry") as mock_proxy:
        resp = fetch_with_ip_block_fallback("https://example.com/x")
    assert resp.status_code == 200
    assert resp.text == "real content"
    mock_get.assert_called_once()
    mock_proxy.assert_not_called()


def test_returns_direct_response_for_real_404_never_falls_back():
    # un 404 real (contenido no existe) NO es un posible bloqueo por IP
    # -- no debe disparar el fallback a proxy.
    with patch("requests.get", return_value=_fake_response(404, "not found")), \
         patch("outer_apis.scrapers.brightdata_proxy.request_via_proxy_with_ip_retry") as mock_proxy:
        resp = fetch_with_ip_block_fallback("https://example.com/x")
    assert resp.status_code == 404
    mock_proxy.assert_not_called()


def test_falls_back_to_proxy_on_403():
    with patch("requests.get", return_value=_fake_response(403, "blocked")), \
         patch(
             "outer_apis.scrapers.brightdata_proxy.request_via_proxy_with_ip_retry",
             return_value=_fake_response(200, "via proxy"),
         ) as mock_proxy:
        resp = fetch_with_ip_block_fallback("https://example.com/x")
    assert resp.status_code == 200
    assert resp.text == "via proxy"
    mock_proxy.assert_called_once()


def test_falls_back_to_proxy_on_429():
    with patch("requests.get", return_value=_fake_response(429, "rate limited")), \
         patch(
             "outer_apis.scrapers.brightdata_proxy.request_via_proxy_with_ip_retry",
             return_value=_fake_response(200, "via proxy"),
         ) as mock_proxy:
        resp = fetch_with_ip_block_fallback("https://example.com/x")
    assert resp.status_code == 200
    mock_proxy.assert_called_once()


def test_falls_back_to_proxy_on_503():
    with patch("requests.get", return_value=_fake_response(503, "unavailable")), \
         patch(
             "outer_apis.scrapers.brightdata_proxy.request_via_proxy_with_ip_retry",
             return_value=_fake_response(200, "via proxy"),
         ) as mock_proxy:
        resp = fetch_with_ip_block_fallback("https://example.com/x")
    assert resp.status_code == 200
    mock_proxy.assert_called_once()


def test_falls_back_to_proxy_on_connection_exception():
    import requests as requests_module

    with patch("requests.get", side_effect=requests_module.RequestException("conn refused")), \
         patch(
             "outer_apis.scrapers.brightdata_proxy.request_via_proxy_with_ip_retry",
             return_value=_fake_response(200, "via proxy"),
         ) as mock_proxy:
        resp = fetch_with_ip_block_fallback("https://example.com/x")
    assert resp.status_code == 200
    mock_proxy.assert_called_once()


def test_returns_none_when_direct_blocked_and_proxy_also_fails():
    # ambos caminos fallan -- fail-closed, None, nunca fabrica una
    # respuesta.
    with patch("requests.get", return_value=_fake_response(403, "blocked")), \
         patch(
             "outer_apis.scrapers.brightdata_proxy.request_via_proxy_with_ip_retry",
             return_value=None,
         ), patch("outer_apis.scrapers.brightdata_proxy.get_brightdata_config", return_value={}):
        resp = fetch_with_ip_block_fallback("https://example.com/x")
    assert resp is None


def test_checks_account_status_when_proxy_fallback_fails():
    # 2026-08-11, pedido explícito del usuario ("si no funciona con el
    # proxy... enviarme un correo") -- cuando el fallback proxeado
    # TAMBIÉN falla, se dispara el chequeo reactivo de cuenta (además
    # del proactivo del watcher).
    with patch("requests.get", return_value=_fake_response(403, "blocked")), \
         patch(
             "outer_apis.scrapers.brightdata_proxy.request_via_proxy_with_ip_retry",
             return_value=None,
         ), patch(
             "outer_apis.scrapers.brightdata_proxy.get_brightdata_config",
             return_value={"proxy_url": "x", "api_token": "tok", "zone": "z"},
         ), patch(
             "outer_apis.scrapers.brightdata_proxy.alert_if_account_broken",
         ) as mock_alert:
        fetch_with_ip_block_fallback("https://example.com/x")
    mock_alert.assert_called_once_with("tok", verbose=False)


def test_does_not_check_account_status_when_direct_request_succeeds():
    # camino feliz -- nunca debe tocar el chequeo de cuenta.
    with patch("requests.get", return_value=_fake_response(200, "ok")), \
         patch("outer_apis.scrapers.brightdata_proxy.alert_if_account_broken") as mock_alert:
        fetch_with_ip_block_fallback("https://example.com/x")
    mock_alert.assert_not_called()


def test_proxy_fallback_whitelists_ip_when_proxy_itself_unauthorized():
    # 2026-08-11, pedido explícito: "si es que el proxy responde
    # unauthorized... se necesita whitelistear la IP" -- este
    # comportamiento YA existe dentro de `request_via_proxy_with_ip_retry`
    # (ver test_brightdata_proxy* si existiera, o el propio código: 407 =
    # `_looks_like_ip_auth_issue` -> `ensure_ip_whitelisted` + reintento).
    # Este test confirma que `fetch_with_ip_block_fallback` REALMENTE
    # delega en esa función (no reimplementa su propia lógica de
    # whitelist por separado) -- el mecanismo de whitelist-on-407 se
    # hereda gratis.
    with patch("requests.get", return_value=_fake_response(403, "blocked")), \
         patch(
             "outer_apis.scrapers.brightdata_proxy.get_brightdata_config",
             return_value={"proxy_url": "http://user-zone-z:pass@proxy:1", "api_token": "tok", "zone": "z"},
         ), \
         patch(
             "outer_apis.scrapers.brightdata_proxy.ensure_ip_whitelisted", return_value=True,
         ) as mock_whitelist, \
         patch("curl_cffi.requests.Session") as mock_session_cls:
        mock_session = MagicMock()
        mock_session.get.side_effect = [
            _fake_response(407, "proxy unauthorized"),
            _fake_response(200, "via proxy after whitelist"),
        ]
        mock_session_cls.return_value = mock_session

        resp = fetch_with_ip_block_fallback("https://example.com/x")

    mock_whitelist.assert_called_once()
    assert resp is not None
    assert resp.status_code == 200
