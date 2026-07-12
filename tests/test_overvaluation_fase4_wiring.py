"""Tests for Fase 4 (docs/pre-event-overvaluation-model-plan-2026-07-11.md):
real-data context construction (`_build_overvaluation_context`) and the
single entry point (`route_overvaluation_events`) + its hard gate
(`OVERVALUATION_MODEL_ENABLED`) inside `run_paper_trading_cycle()`.

Uses synthetic events/DB rows only (never real production data), per task
instruction."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from src.database.database import init_db
from src.database.models.paper_wallet import SimulatedWallet, SimulatedLedger, SimulatedBet
from src.core.orchestration import paper_trading_cycle as cycle
from src.core.orchestration import portfolio


@pytest.fixture(autouse=True)
def _clean_tables():
    init_db()
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()
    yield
    SimulatedBet.delete().execute()
    SimulatedLedger.delete().execute()
    SimulatedWallet.delete().execute()


def _make_wallet(balance=200.0, label="t-overvaluation-fase4"):
    return SimulatedWallet.create(
        label=label, platform="paper", initial_balance=balance,
        balance=balance, equity=balance, started_at=cycle._now_iso(),
        strategy_scope="all", reset_count=0, is_active=True,
    )


def _pre_built_event(category, ext_id, favorite_price=0.65, underdog_price=0.35, fair_prob=0.60):
    """A raw event that ALREADY carries favorite_price/underdog_price/
    fair_prob -- i.e. bypasses `_build_overvaluation_context` entirely, the
    same way a real caller-supplied context would. Used to exercise
    `_run_strategy1_overvaluation_pass`'s state machine directly across
    categories that don't (yet) have a wired real-data source, per the
    task's test (b) instruction (tennis + football fixtures)."""
    return {
        "external_id": ext_id, "platform": "kalshi", "category": category,
        "title": f"{category} fixture", "favorite_price": favorite_price,
        "underdog_price": underdog_price, "fair_prob": fair_prob,
        "book_depth_at_level": 1000.0, "avg_volume": 5000.0,
    }


# ── portfolio.py wiring sanity (per task instruction #1) ───────────────────

def test_strategy1_categories_already_include_all_six_no_change_needed():
    for cat in ("tennis", "football", "soccer", "basketball", "baseball", "weather"):
        assert cat in portfolio.STRATEGY1_CATEGORIES


def test_strategy1_calibrated_categories_still_empty():
    assert portfolio.STRATEGY1_CALIBRATED_CATEGORIES == set()


# ── _build_overvaluation_context ────────────────────────────────────────────

def test_context_skips_tennis_documented_gap():
    event = {"category": "tennis", "title": "Alcaraz vs Sinner", "yes_price": 0.6, "no_price": 0.4}
    assert cycle._build_overvaluation_context(event) is None


def test_context_skips_weather_documented_gap():
    event = {"category": "weather", "title": "NYC temp 20-21C", "yes_price": 0.3, "no_price": 0.7}
    assert cycle._build_overvaluation_context(event) is None


def test_context_skips_football_event_without_two_sided_title():
    event = {"category": "football", "title": "Next World Cup winner", "yes_price": 0.5, "no_price": 0.5}
    assert cycle._build_overvaluation_context(event) is None


def test_context_skips_football_event_with_unknown_teams_no_crash(monkeypatch):
    # Real clubelo_snapshot() call happens inside _build_overvaluation_context;
    # force it to return an empty snapshot (as if neither team is on file) --
    # must fail closed (None), never crash, never invent an Elo.
    import src.scrapers.team_data.clubelo as clubelo_mod
    monkeypatch.setattr(clubelo_mod, "clubelo_snapshot", lambda d: {})
    event = {
        "category": "football", "title": "Totally Unknown FC vs Another Unknown FC",
        "yes_price": 0.5, "no_price": 0.5,
    }
    assert cycle._build_overvaluation_context(event) is None


def test_context_skips_basketball_event_with_no_rating_on_file(monkeypatch):
    import src.scrapers.team_data.common as common_mod
    monkeypatch.setattr(common_mod, "get_team_rating", lambda sport, name: None)
    event = {
        "category": "basketball", "title": "Made Up Team A vs Made Up Team B",
        "yes_price": 0.5, "no_price": 0.5,
    }
    assert cycle._build_overvaluation_context(event) is None


def test_context_builds_favorite_underdog_from_ratings(monkeypatch):
    import src.scrapers.team_data.common as common_mod
    monkeypatch.setattr(
        common_mod, "get_team_rating",
        lambda sport, name: 1700.0 if name == "Team A" else 1500.0,
    )
    event = {
        "category": "basketball", "title": "Team A vs Team B",
        "yes_price": 0.7, "no_price": 0.3,
    }
    ctx = cycle._build_overvaluation_context(event)
    assert ctx is not None
    # Team A (higher rating) is the model's favorite -> "yes" (team_a) side
    # maps to favorite_price per this function's documented assumption.
    assert ctx["favorite_price"] == 0.7
    assert ctx["underdog_price"] == 0.3
    assert 0.5 < ctx["fair_prob"] < 1.0


# ── route_overvaluation_events / _run_strategy1_overvaluation_pass ─────────

def test_route_forced_gate_opens_positions_for_tennis_and_football_fixtures():
    wallet = _make_wallet()
    events = [
        _pre_built_event("tennis", "kalshi:TENNIS-1"),
        _pre_built_event("football", "kalshi:FOOTBALL-1"),
    ]
    stats = cycle.route_overvaluation_events(wallet, events, threshold=0.10)
    assert stats["overvaluation_new_market_filled"] == 2
    assert SimulatedBet.select().where(
        SimulatedBet.status == "overvaluation_market_leg_filled_awaiting_hedge"
    ).count() == 2


def test_route_event_missing_ranking_data_is_skipped_not_crashed():
    wallet = _make_wallet()
    # Real football event with a two-sided title but no favorite_price/
    # underdog_price/fair_prob pre-supplied, and no live team on file ->
    # _build_overvaluation_context returns None -> event passed through
    # unmodified -> _run_strategy1_overvaluation_pass fail-closed skips it.
    event = {
        "external_id": "kalshi:FOOTBALL-UNKNOWN", "platform": "kalshi",
        "category": "football", "title": "Some Unrated FC vs Another Unrated FC",
        "resolution_date": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        "yes_price": 0.5, "no_price": 0.5,
    }
    stats = cycle.route_overvaluation_events(wallet, [event], threshold=0.10)
    assert stats["overvaluation_new_market_filled"] == 0
    assert SimulatedBet.select().count() == 0


# ── run_paper_trading_cycle gate ───────────────────────────────────────────

def test_cycle_gate_off_by_default_never_opens_overvaluation_position():
    assert cycle.OVERVALUATION_MODEL_ENABLED is False
    wallet = _make_wallet(label="t-overvaluation-fase4-gate-off")
    event = _pre_built_event("football", "kalshi:FOOTBALL-GATE-OFF")
    result = cycle.run_paper_trading_cycle(
        wallet_label="t-overvaluation-fase4-gate-off",
        _opportunities=[], _raw_events=[event],
    )
    assert result["overvaluation_model_enabled"] is False
    assert SimulatedBet.select().where(
        SimulatedBet.status == "overvaluation_market_leg_filled_awaiting_hedge"
    ).count() == 0


def test_cycle_gate_on_opens_overvaluation_position_when_forced(monkeypatch):
    monkeypatch.setattr(cycle, "OVERVALUATION_MODEL_ENABLED", True)
    wallet = _make_wallet(label="t-overvaluation-fase4-gate-on")
    event = _pre_built_event("tennis", "kalshi:TENNIS-GATE-ON")
    result = cycle.run_paper_trading_cycle(
        wallet_label="t-overvaluation-fase4-gate-on",
        _opportunities=[], _raw_events=[event],
    )
    assert result["overvaluation_model_enabled"] is True
    assert result["overvaluation_new_market_filled"] == 1
    assert SimulatedBet.select().where(
        SimulatedBet.status == "overvaluation_market_leg_filled_awaiting_hedge"
    ).count() == 1
