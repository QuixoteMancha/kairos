"""Tests for src/core/brightdata_utils.request_via_proxy_with_ip_retry() —
the fallback logic (check IP whitelisted -> whitelist if not -> retry once)
that existed as ensure_ip_whitelisted() but was never wired into any real
proxy call site until this round. No real network calls."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.core import brightdata_utils as bdu


class _FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


def test_success_on_first_try_never_touches_whitelist():
    make_session = MagicMock(return_value="session-1")
    request_fn = MagicMock(return_value=_FakeResp(200))
    with patch.object(bdu, "ensure_ip_whitelisted") as mock_ensure:
        result = bdu.request_via_proxy_with_ip_retry(
            make_session, request_fn, api_token="tok", zone="zone1",
        )
    assert result.status_code == 200
    mock_ensure.assert_not_called()
    assert make_session.call_count == 1


def test_407_triggers_whitelist_check_and_retry():
    make_session = MagicMock(side_effect=["session-1", "session-2"])
    request_fn = MagicMock(side_effect=[_FakeResp(407), _FakeResp(200)])
    with patch.object(bdu, "ensure_ip_whitelisted") as mock_ensure:
        result = bdu.request_via_proxy_with_ip_retry(
            make_session, request_fn, api_token="tok", zone="zone1",
        )
    assert result.status_code == 200
    mock_ensure.assert_called_once_with("tok", "zone1", verbose=True)
    assert make_session.call_count == 2


def test_connection_exception_triggers_whitelist_check_and_retry():
    make_session = MagicMock(side_effect=["session-1", "session-2"])
    request_fn = MagicMock(side_effect=[ConnectionError("tunnel failed"), _FakeResp(200)])
    with patch.object(bdu, "ensure_ip_whitelisted") as mock_ensure:
        result = bdu.request_via_proxy_with_ip_retry(
            make_session, request_fn, api_token="tok", zone="zone1",
        )
    assert result.status_code == 200
    mock_ensure.assert_called_once()


def test_403_never_triggers_whitelist_retry():
    # 403 is the TARGET SITE's anti-bot challenge, not a proxy-IP-auth issue
    # -- retrying via whitelist would be pointless and must never happen.
    make_session = MagicMock(return_value="session-1")
    request_fn = MagicMock(return_value=_FakeResp(403))
    with patch.object(bdu, "ensure_ip_whitelisted") as mock_ensure:
        result = bdu.request_via_proxy_with_ip_retry(
            make_session, request_fn, api_token="tok", zone="zone1",
        )
    assert result.status_code == 403
    mock_ensure.assert_not_called()
    assert make_session.call_count == 1


def test_missing_api_token_or_zone_skips_retry_gracefully():
    make_session = MagicMock(return_value="session-1")
    request_fn = MagicMock(return_value=_FakeResp(407))
    with patch.object(bdu, "ensure_ip_whitelisted") as mock_ensure:
        result = bdu.request_via_proxy_with_ip_retry(
            make_session, request_fn, api_token="", zone="",
        )
    assert result.status_code == 407  # returns the original (failed) response
    mock_ensure.assert_not_called()
    assert make_session.call_count == 1  # no retry attempted


def test_retry_still_fails_returns_none():
    make_session = MagicMock(side_effect=["session-1", "session-2"])
    request_fn = MagicMock(side_effect=[_FakeResp(407), ConnectionError("still broken")])
    with patch.object(bdu, "ensure_ip_whitelisted"):
        result = bdu.request_via_proxy_with_ip_retry(
            make_session, request_fn, api_token="tok", zone="zone1",
        )
    assert result is None
