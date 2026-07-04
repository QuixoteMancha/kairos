"""Tests for run_dwell_time_strategy_worker's context-building and one
cycle of _run_once (mocked, no network/DB side effects beyond the row
write, no real orders placed).
"""
from unittest.mock import patch

from src.core.orchestration import workers as wk


def test_extract_two_sided_names_matches_vs_pattern():
    assert wk._extract_two_sided_names("Colombia vs. Ghana") == ("Colombia", "Ghana")
    assert wk._extract_two_sided_names("Team A vs Team B") == ("Team A", "Team B")


def test_extract_two_sided_names_returns_none_for_non_matchup_title():
    assert wk._extract_two_sided_names("NBA: LeBron James Next Team") is None


def test_build_context_football_needs_only_names():
    ctx = wk._build_dwell_time_context({"category": "football", "title": "Brazil vs Norway"})
    assert ctx == {"team_a": "Brazil", "team_b": "Norway"}


def test_build_context_football_skips_non_matchup_title():
    ctx = wk._build_dwell_time_context({"category": "football", "title": "World Cup: Golden Boot Winner"})
    assert ctx is None


def test_build_context_basketball_skips_without_team_rating():
    with patch("src.scrapers.forgotten.team_data.common.get_team_rating", return_value=None):
        ctx = wk._build_dwell_time_context({"category": "basketball", "title": "Lakers vs Celtics"})
    assert ctx is None


def test_build_context_basketball_builds_with_ratings():
    with patch("src.scrapers.forgotten.team_data.common.get_team_rating", side_effect=[1600.0, 1500.0]):
        ctx = wk._build_dwell_time_context({"category": "basketball", "title": "Lakers vs Celtics"})
    assert ctx == {"team_a": "Lakers", "team_b": "Celtics", "team_a_rating": 1600.0, "team_b_rating": 1500.0}


def test_build_context_tennis_and_weather_currently_skipped():
    assert wk._build_dwell_time_context({"category": "tennis", "title": "Sinner vs Alcaraz"}) is None
    assert wk._build_dwell_time_context({"category": "weather", "title": "Istanbul max temp"}) is None


def test_run_once_persists_a_proposal_row(monkeypatch):
    event = {
        "category": "football", "title": "Brazil vs Norway",
        "yes_price": 0.6, "no_price": 0.35, "clob_token_ids": [],
    }

    # The worker imports its collaborators lazily inside _run_once, so patch
    # the modules they're imported from and run exactly one cycle (time.sleep
    # raises to break out of the while-True loop after the first iteration).
    with patch("src.core.orchestration.scanning._fetch_prediction_market_events", return_value=[event]), \
         patch("src.core.strategies.dwell_time.get_model", return_value=object()), \
         patch("src.core.strategies.limit_order_formation.propose_and_check_fillable", return_value={
             "opportunity": True, "category": "football",
             "sides": {
                 "team_a": {"price": 0.5, "expected_time_fraction": 0.4, "band": (0.4, 0.5)},
                 "team_b": {"price": 0.3, "expected_time_fraction": 0.3, "band": (0.3, 0.4)},
             },
             "implied_edge": 0.2,
             "fillability": {},
         }), \
         patch("src.database.database.DwellTimeProposal") as mock_model:
        # Extract the inner _run_once by calling the worker loop once via a
        # sleep patch that raises to break out after one iteration.
        with patch("time.sleep", side_effect=StopIteration):
            try:
                wk.run_dwell_time_strategy_worker(interval_sec=0)
            except StopIteration:
                pass

    assert mock_model.create.call_count == 2  # one row per side
    kwargs_list = [c.kwargs for c in mock_model.create.call_args_list]
    sides = {k["side"] for k in kwargs_list}
    assert sides == {"team_a", "team_b"}
    for k in kwargs_list:
        assert k["category"] == "football"
        assert k["fixture"] == "Brazil vs Norway"
        assert k["implied_edge_pct"] == 0.2
