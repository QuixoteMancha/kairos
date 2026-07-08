"""Tests for src/core/orchestration/daily_portfolio_rotation.py — rotación
diaria conservador/agresivo del portafolio (SIMULATED money only)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from src.database.database import db, init_db, AppConfig
from src.database.models.paper_wallet import SimulatedWallet, SimulatedLedger, SimulatedBet
from src.core.orchestration import daily_portfolio_rotation as rot
from src.core.orchestration.portfolio import (
    TIER_B_MAX_WEIGHT, TIER_C_MAX_WEIGHT, _BUCKET_MAX_WEIGHT,
)

WALLET_LABEL = "test-rotation-wallet"


@pytest.fixture(autouse=True)
def _clean_tables():
    init_db()
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()
    AppConfig.delete().where(AppConfig.key == rot.ROTATION_STATE_KEY).execute()
    yield
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()
    AppConfig.delete().where(AppConfig.key == rot.ROTATION_STATE_KEY).execute()


def _make_wallet(initial=200.0, equity=200.0):
    return SimulatedWallet.create(
        label=WALLET_LABEL, platform="paper",
        initial_balance=initial, balance=equity, equity=equity,
    )


def _ledger_row(wallet, day_bucket, equity_after, amount=0.0):
    SimulatedLedger.create(
        wallet=wallet, timestamp="2026-07-08T00:00:00+00:00",
        op_type="settle_win", amount=amount, balance_after=equity_after,
        equity_after=equity_after, strategy="strategy_1_universal",
        day_bucket=day_bucket,
    )


# ── 1. Cálculo de PnL diario realizado ─────────────────────────────────

def test_compute_daily_realized_pnl_uses_prior_day_snapshot():
    wallet = _make_wallet(initial=200.0, equity=210.0)
    yesterday = "2026-07-07"
    today = "2026-07-08"
    _ledger_row(wallet, yesterday, equity_after=200.0)
    _ledger_row(wallet, today, equity_after=210.0)

    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    snap = rot.compute_daily_realized_pnl(WALLET_LABEL, now=now)

    assert snap.sufficient_data is True
    assert snap.starting_equity == 200.0
    assert snap.current_equity == 210.0
    assert snap.realized_pnl == pytest.approx(10.0)
    assert snap.realized_pnl_pct == pytest.approx(0.05)


def test_compute_daily_realized_pnl_no_wallet_is_insufficient():
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    snap = rot.compute_daily_realized_pnl("wallet-does-not-exist", now=now)
    assert snap.sufficient_data is False


def test_compute_daily_realized_pnl_brand_new_wallet_no_ledger():
    _make_wallet(initial=200.0, equity=200.0)
    now = datetime(2026, 7, 8, 1, 0, tzinfo=timezone.utc)
    snap = rot.compute_daily_realized_pnl(WALLET_LABEL, now=now)
    # No prior-day ledger row and equity == initial_balance -> weak/no signal.
    assert snap.sufficient_data is False


# ── 2. Selección de preset ──────────────────────────────────────────────

def test_select_preset_reached_2pct_goes_conservative():
    wallet = _make_wallet(initial=200.0, equity=205.0)  # +2.5%
    _ledger_row(wallet, "2026-07-07", equity_after=200.0)
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)

    decision = rot.select_daily_preset(WALLET_LABEL, now=now)
    assert decision["preset"] == rot.PRESET_CONSERVATIVE


def test_select_preset_not_reached_with_time_left_goes_aggressive():
    wallet = _make_wallet(initial=200.0, equity=201.0)  # +0.5%, below 2%
    _ledger_row(wallet, "2026-07-07", equity_after=200.0)
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)  # plenty of hours left

    decision = rot.select_daily_preset(WALLET_LABEL, now=now)
    assert decision["preset"] == rot.PRESET_AGGRESSIVE


def test_select_preset_no_data_goes_conservative():
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    decision = rot.select_daily_preset("nonexistent-wallet", now=now)
    assert decision["preset"] == rot.PRESET_CONSERVATIVE


def test_select_preset_end_of_day_forces_conservative_even_if_not_reached():
    wallet = _make_wallet(initial=200.0, equity=201.0)  # below 2%
    _ledger_row(wallet, "2026-07-07", equity_after=200.0)
    now = datetime(2026, 7, 8, 23, 45, tzinfo=timezone.utc)  # 15 min left in UTC day

    decision = rot.select_daily_preset(WALLET_LABEL, now=now)
    assert decision["preset"] == rot.PRESET_CONSERVATIVE


# ── 3. El tope de tier nunca se excede en ningún preset ────────────────

@pytest.mark.parametrize("preset_name", [rot.PRESET_CONSERVATIVE, rot.PRESET_AGGRESSIVE])
def test_preset_never_exceeds_existing_bucket_caps(preset_name):
    caps = rot.PRESETS[preset_name]
    assert caps["low_risk_arb"] <= _BUCKET_MAX_WEIGHT["low_risk_arb"]
    assert caps["directional_calibrated"] <= TIER_B_MAX_WEIGHT
    assert caps["directional_uncalibrated"] <= TIER_C_MAX_WEIGHT
    # Also never negative.
    assert all(v >= 0.0 for v in caps.values())


def test_conservative_tier_c_is_fully_disabled():
    assert rot.PRESETS[rot.PRESET_CONSERVATIVE]["directional_uncalibrated"] == 0.0


def test_aggressive_hits_existing_ceilings_exactly_never_more():
    caps = rot.PRESETS[rot.PRESET_AGGRESSIVE]
    assert caps["directional_calibrated"] == TIER_B_MAX_WEIGHT
    assert caps["directional_uncalibrated"] == TIER_C_MAX_WEIGHT


# ── 4. Ciclo real de paper-trading consulta y aplica el preset activo ──

def test_get_strategy1_stake_scale_conservative_reduces_uncalibrated_to_zero():
    AppConfig.set_value(rot.ROTATION_STATE_KEY, __import__("json").dumps({
        "preset": rot.PRESET_CONSERVATIVE,
        "caps": rot.PRESETS[rot.PRESET_CONSERVATIVE],
    }))
    scale = rot.get_strategy1_stake_scale("tennis")  # tennis is NOT calibrated today
    assert scale == 0.0


def test_get_strategy1_stake_scale_aggressive_is_full_ratio():
    AppConfig.set_value(rot.ROTATION_STATE_KEY, __import__("json").dumps({
        "preset": rot.PRESET_AGGRESSIVE,
        "caps": rot.PRESETS[rot.PRESET_AGGRESSIVE],
    }))
    scale = rot.get_strategy1_stake_scale("tennis")
    assert scale == pytest.approx(1.0)


def test_get_strategy1_stake_scale_no_state_defaults_neutral_full_ratio():
    # Nothing persisted yet (cold start, rotation worker never ran) -> NEUTRAL
    # preset, i.e. respects portfolio.py's existing tier caps as-is (ratio=1.0)
    # rather than silently disabling Strategy 1 until the worker's first tick.
    # Only an explicit conservative DECISION (2% reached / EOD / real error)
    # should zero the uncalibrated tier -- absence of data is not that.
    scale = rot.get_strategy1_stake_scale("football")
    assert scale == pytest.approx(1.0)


def test_strategy1_new_position_stake_respects_conservative_scale(monkeypatch):
    """Injects a conservative preset and verifies the REAL paper-trading
    cycle (_run_strategy1_pass via run_paper_trading_cycle) actually shrinks
    the stake of a brand-new Strategy 1 position accordingly -- not just
    that the scaling function exists in isolation."""
    from src.core.orchestration import paper_trading_cycle as cycle

    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()

    wallet = cycle.get_or_create_wallet("test-s1-scale-wallet", initial_balance=200.0)

    # Force conservative preset (tier C fully disabled == uncalibrated
    # category, which is every Strategy1 category today).
    AppConfig.set_value(rot.ROTATION_STATE_KEY, __import__("json").dumps({
        "preset": rot.PRESET_CONSERVATIVE,
        "caps": rot.PRESETS[rot.PRESET_CONSERVATIVE],
    }))

    raw_events = [{
        "external_id": "evt-s1-scale-1", "platform": "polymarket",
        "category": "tennis", "title": "Player A vs Player B",
        "resolution_date": "2026-07-10T00:00:00+00:00",
        "yes_price": 0.45,
    }]

    result = cycle.run_paper_trading_cycle(
        wallet_label="test-s1-scale-wallet",
        _opportunities=[], _raw_events=raw_events, run_strategy1=True,
    )

    # Conservative preset -> tier C (uncalibrated) scale is 0.0 -> stake_usd
    # computed as 0 -> the candidate must be skipped entirely this cycle
    # (no SimulatedBet created), proving the cap was actually consulted and
    # applied, not just available.
    bets = list(SimulatedBet.select().where(SimulatedBet.wallet == wallet))
    assert len(bets) == 0

    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()
