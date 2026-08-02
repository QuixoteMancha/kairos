"""Tests de `scripts/sf_final_scan.py` (2026-08-01, pedido explícito del
usuario: escanear todas las SF/Final ATP+WTA disponibles en Kalshi)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from scripts.sf_final_scan import scan_sf_final_matches, _trade_price_summary, _parse_match_date


def _leg(ticker, title, event_ticker, rules_secondary, result=None, close_time="2026-08-01T20:00:00Z"):
    return {
        "id": ticker,
        "close_time": close_time,
        "raw": {
            "event_ticker": event_ticker, "title": title, "yes_sub_title": None,
            "rules_secondary": rules_secondary, "result": result,
        },
    }


def _fake_api_with_markets(markets_by_series):
    api = MagicMock()
    api.get_markets.side_effect = lambda status, series_ticker, max_pages: markets_by_series.get(series_ticker, [])
    api.get_trades.return_value = []
    return api


def test_filters_only_semifinal_and_final_rounds():
    legs_qf = [
        _leg("T1-A", "Will A win the A vs B: Quarterfinal match?", "EVT1", "ATP Miami Quarterfinal"),
        _leg("T1-B", "Will B win the A vs B: Quarterfinal match?", "EVT1", "ATP Miami Quarterfinal"),
    ]
    legs_sf = [
        _leg("T2-A", "Will C win the C vs D: Semifinal match?", "EVT2", "ATP Miami Semifinal", result="yes"),
        _leg("T2-B", "Will D win the C vs D: Semifinal match?", "EVT2", "ATP Miami Semifinal", result="no"),
    ]
    api = _fake_api_with_markets({"KXATPMATCH": legs_qf + legs_sf, "KXWTAMATCH": []})

    with patch("scripts.sf_final_scan.KalshiAPI", return_value=api), \
         patch("scripts.sf_final_scan.get_age", return_value=None), \
         patch("scripts.sf_final_scan.get_injury_risk_flag", return_value=None), \
         patch("scripts.sf_final_scan.calculate_fatigue_index", return_value=None):
        results = scan_sf_final_matches(fetch_stats=False)

    assert len(results) == 1
    assert results[0]["round"] == "Semifinal"
    assert results[0]["winner"] == "C"


def test_skips_incomplete_events():
    legs = [_leg("T1-A", "Will A win the A vs B: Final match?", "EVT1", "ATP Final")]
    api = _fake_api_with_markets({"KXATPMATCH": legs, "KXWTAMATCH": []})

    with patch("scripts.sf_final_scan.KalshiAPI", return_value=api):
        results = scan_sf_final_matches(fetch_stats=False)

    assert results == []


def test_winner_resolved_from_leg_a_yes():
    legs = [
        _leg("T1-A", "Will A win the A vs B: Final match?", "EVT1", "ATP Final", result="yes"),
        _leg("T1-B", "Will B win the A vs B: Final match?", "EVT1", "ATP Final", result="no"),
    ]
    api = _fake_api_with_markets({"KXATPMATCH": legs, "KXWTAMATCH": []})

    with patch("scripts.sf_final_scan.KalshiAPI", return_value=api), \
         patch("scripts.sf_final_scan.get_age", return_value=None), \
         patch("scripts.sf_final_scan.get_injury_risk_flag", return_value=None), \
         patch("scripts.sf_final_scan.calculate_fatigue_index", return_value=None):
        results = scan_sf_final_matches(fetch_stats=False)

    assert results[0]["winner"] == "A"


def test_fetch_stats_false_skips_flashscore_entirely():
    legs = [
        _leg("T1-A", "Will A win the A vs B: Final match?", "EVT1", "ATP Final", result="yes"),
        _leg("T1-B", "Will B win the A vs B: Final match?", "EVT1", "ATP Final", result="no"),
    ]
    api = _fake_api_with_markets({"KXATPMATCH": legs, "KXWTAMATCH": []})

    with patch("scripts.sf_final_scan.KalshiAPI", return_value=api), \
         patch("scripts.sf_final_scan.get_age", return_value=None), \
         patch("scripts.sf_final_scan.get_injury_risk_flag", return_value=None), \
         patch("scripts.sf_final_scan.calculate_fatigue_index", return_value=None), \
         patch("scripts.sf_final_scan.find_match_id") as mock_find:
        results = scan_sf_final_matches(fetch_stats=False)

    mock_find.assert_not_called()
    assert results[0]["flashscore_match_id"] is None
    assert results[0]["flashscore_stats"] is None


def test_trade_price_summary_parses_real_field_format():
    api = MagicMock()
    api.get_trades.return_value = [
        {"yes_price_dollars": "0.9900"},
        {"yes_price_dollars": "0.5000"},
        {"yes_price_dollars": "0.3000"},
    ]
    summary = _trade_price_summary(api, "SOME-TICKER")
    assert summary["trade_count"] == 3
    assert summary["price_close"] == 0.99  # más reciente primero
    assert summary["price_open"] == 0.30   # más viejo al final
    assert summary["price_min"] == 0.30
    assert summary["price_max"] == 0.99


def test_trade_price_summary_fail_closed_no_trades():
    api = MagicMock()
    api.get_trades.return_value = []
    summary = _trade_price_summary(api, "SOME-TICKER")
    assert summary == {"trade_count": 0, "price_open": None, "price_close": None, "price_min": None, "price_max": None}


def test_parse_match_date_fail_closed_on_bad_format():
    assert _parse_match_date(None) is None
    assert _parse_match_date("garbage") is None
    assert _parse_match_date("2026-08-01T20:00:00Z").isoformat() == "2026-08-01"
