"""Tests for Fase 4 (docs/crypto-15m-kalshi-strategy1-variant-plan-2026-07-11.md):
real-time indicator construction (`_build_crypto15m_indicators`) and the
single entry point (`route_crypto15m_events`) + its hard gate
(`CRYPTO15M_LIVE_ENABLED`) inside `run_paper_trading_cycle()`.

Uses synthetic `CryptoPriceSnapshot`/`KalshiMarketTick` rows (never real
production data), per task instruction."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import pytest

from src.database.database import init_db, CryptoPriceSnapshot, KalshiMarketTick
from src.database.models.paper_wallet import SimulatedWallet, SimulatedLedger, SimulatedBet
from src.core.orchestration import paper_trading_cycle as cycle
from src.core.orchestration import portfolio
from src.core.strategies import limit_order_formation


@pytest.fixture(autouse=True)
def _clean_tables():
    init_db()
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()
    CryptoPriceSnapshot.delete().execute()
    KalshiMarketTick.delete().execute()
    yield
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()
    CryptoPriceSnapshot.delete().execute()
    KalshiMarketTick.delete().execute()


def _make_wallet(balance=200.0):
    return SimulatedWallet.create(
        label="t-crypto15m-fase4", platform="paper", initial_balance=balance,
        balance=balance, equity=balance, started_at=cycle._now_iso(),
        strategy_scope="all", reset_count=0, is_active=True,
    )


def _seed_snapshots(symbol: str, n: int, base_price: float = 100.0, step: float = 1.0, start=None):
    start = start or (datetime.now(timezone.utc) - timedelta(minutes=n))
    for i in range(n):
        price = base_price + step * i
        CryptoPriceSnapshot.create(
            symbol=symbol, timestamp=start + timedelta(minutes=i),
            open=price, high=price + 0.5, low=price - 0.5, close=price, volume=10.0,
        )


# ── portfolio.py / limit_order_formation.py wiring ─────────────────────────

def test_crypto15m_in_strategy1_categories_but_not_calibrated():
    assert "crypto_15m" in portfolio.STRATEGY1_CATEGORIES
    assert "crypto_15m" not in portfolio.STRATEGY1_CALIBRATED_CATEGORIES


def test_crypto15m_deliberately_excluded_from_dynamic_epsilon_categories():
    assert "crypto_15m" not in limit_order_formation._SUPPORTED_CATEGORIES


# ── _build_crypto15m_indicators ────────────────────────────────────────────

def test_build_indicators_unknown_series_prefix_returns_all_none():
    ind = cycle._build_crypto15m_indicators("NOTACRYPTOSERIES-26JUL110345-45", None)
    assert ind == {
        "momentum_zscore": None, "stoch": None, "price_direction": None,
        "seconds_remaining": None, "crossing_estimate": None,
    }


def test_build_indicators_hype_series_fails_closed_no_binance_symbol():
    # KXHYPE15M is documented as having no Binance spot symbol.
    ind = cycle._build_crypto15m_indicators("KXHYPE15M-26JUL110345-45", None)
    assert ind["momentum_zscore"] is None
    assert ind["stoch"] is None


def test_build_indicators_insufficient_bars_fails_closed():
    _seed_snapshots("BTCUSDT", n=3)  # far fewer than the ATR/stoch windows need
    ind = cycle._build_crypto15m_indicators("KXBTC15M-26JUL110345-45", None)
    assert ind["momentum_zscore"] is None
    assert ind["stoch"] is None
    assert ind["price_direction"] is None


def test_build_indicators_real_bars_produce_real_values():
    now = datetime.now(timezone.utc)
    _seed_snapshots("BTCUSDT", n=25, base_price=100.0, step=1.0,
                     start=now - timedelta(minutes=25))
    resolution_date = (now + timedelta(minutes=5)).isoformat()
    ind = cycle._build_crypto15m_indicators("KXBTC15M-26JUL110345-45", resolution_date, now=now)
    assert ind["momentum_zscore"] is not None
    assert ind["momentum_zscore"] > 0  # strictly increasing synthetic prices -> positive momentum
    assert ind["stoch"] is not None
    assert ind["price_direction"] is not None
    assert ind["price_direction"] > 0
    assert ind["seconds_remaining"] is not None
    assert 290 <= ind["seconds_remaining"] <= 300
    # No persisted CalibratedLookup exists yet -- must always fail-closed today.
    assert ind["crossing_estimate"] is not None
    assert ind["crossing_estimate"].calibrated is False


def test_build_indicators_past_resolution_date_gives_no_seconds_remaining():
    now = datetime.now(timezone.utc)
    _seed_snapshots("BTCUSDT", n=25, start=now - timedelta(minutes=25))
    past = (now - timedelta(minutes=1)).isoformat()
    ind = cycle._build_crypto15m_indicators("KXBTC15M-26JUL110345-45", past, now=now)
    assert ind["seconds_remaining"] is None


# ── route_crypto15m_events ─────────────────────────────────────────────────

def _crypto_event(ticker="KXBTC15M-26JUL110345-45", yes_price=0.50, resolution_seconds=600):
    now = datetime.now(timezone.utc)
    return {
        "external_id": f"kalshi:{ticker}",
        "platform": "kalshi",
        "category": "crypto",
        "title": "Will BTC be above $100000 at 11:45?",
        "resolution_date": (now + timedelta(seconds=resolution_seconds)).isoformat(),
        "yes_price": yes_price,
    }


def test_route_ignores_non_crypto15m_events_safely():
    wallet = _make_wallet()
    non_crypto = {
        "external_id": "kalshi:SOME-OTHER-MARKET-1", "platform": "kalshi",
        "category": "economics", "title": "Will X happen?",
        "resolution_date": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "yes_price": 0.5,
    }
    stats = cycle.route_crypto15m_events(wallet, [non_crypto], tick_provider=lambda t: [])
    assert stats["crypto15m_a_new_pending"] == 0
    assert stats["crypto15m_b_new_market_filled"] == 0
    assert SimulatedBet.select().count() == 0


def test_route_lateral_regime_admits_substrategy_a_candidate():
    now = datetime.now(timezone.utc)
    # Flat/oscillating prices -> low |momentum_zscore| -> lateral regime -> Sub-strategy A.
    _seed_snapshots("BTCUSDT", n=25, base_price=100.0, step=0.0, start=now - timedelta(minutes=25))
    wallet = _make_wallet()
    event = _crypto_event(yes_price=0.50, resolution_seconds=600)
    stats = cycle.route_crypto15m_events(wallet, [event], tick_provider=lambda t: [])
    assert stats["crypto15m_a_new_pending"] == 1
    bet = SimulatedBet.get(SimulatedBet.status == "crypto15m_A_leg1_pending")
    assert bet.strategy == cycle._S1_STRATEGY_NAME


def test_route_trending_regime_admits_substrategy_b_candidate():
    now = datetime.now(timezone.utc)
    # Strongly increasing prices -> high |momentum_zscore| -> trending -> Sub-strategy B.
    _seed_snapshots("BTCUSDT", n=25, base_price=100.0, step=5.0, start=now - timedelta(minutes=25))
    wallet = _make_wallet()
    event = _crypto_event(yes_price=0.60, resolution_seconds=600)
    stats = cycle.route_crypto15m_events(wallet, [event], tick_provider=lambda t: [])
    assert stats["crypto15m_b_new_market_filled"] == 1
    bet = SimulatedBet.get(SimulatedBet.status == "crypto15m_B_awaiting_asymmetric_hedge")
    assert bet.strategy == cycle._S1_STRATEGY_NAME


def test_route_insufficient_indicators_opens_nothing():
    wallet = _make_wallet()
    # No CryptoPriceSnapshot rows seeded at all -- indicators fail-closed to None.
    event = _crypto_event()
    stats = cycle.route_crypto15m_events(wallet, [event], tick_provider=lambda t: [])
    assert stats["crypto15m_a_new_pending"] == 0
    assert stats["crypto15m_b_new_market_filled"] == 0
    assert SimulatedBet.select().count() == 0


# ── run_paper_trading_cycle gate ───────────────────────────────────────────

def test_cycle_gate_off_by_default_never_opens_crypto15m_position():
    assert cycle.CRYPTO15M_LIVE_ENABLED is False
    now = datetime.now(timezone.utc)
    _seed_snapshots("BTCUSDT", n=25, base_price=100.0, step=0.0, start=now - timedelta(minutes=25))
    event = _crypto_event(yes_price=0.50, resolution_seconds=600)
    result = cycle.run_paper_trading_cycle(
        wallet_label="t-crypto15m-fase4-gate-off",
        _opportunities=[], _raw_events=[event],
    )
    assert result["crypto15m_live_enabled"] is False
    assert SimulatedBet.select().where(SimulatedBet.status == "crypto15m_A_leg1_pending").count() == 0


def test_cycle_gate_on_opens_crypto15m_position_when_forced(monkeypatch):
    monkeypatch.setattr(cycle, "CRYPTO15M_LIVE_ENABLED", True)
    now = datetime.now(timezone.utc)
    _seed_snapshots("BTCUSDT", n=25, base_price=100.0, step=0.0, start=now - timedelta(minutes=25))
    event = _crypto_event(yes_price=0.50, resolution_seconds=600)
    result = cycle.run_paper_trading_cycle(
        wallet_label="t-crypto15m-fase4-gate-on",
        _opportunities=[], _raw_events=[event],
    )
    assert result["crypto15m_live_enabled"] is True
    assert result["crypto15m_a_new_pending"] == 1
    assert SimulatedBet.select().where(SimulatedBet.status == "crypto15m_A_leg1_pending").count() == 1
