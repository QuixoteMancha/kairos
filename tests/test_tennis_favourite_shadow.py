from datetime import datetime, timezone

import pytest

from database.db import db_historical, init_db_historical
from database.historical.tennis.matchtracking import (
    mark_match_settled, register_scanned_match, save_probability_tick,
)
from database.historical.tennis.models import (
    FavouriteShadowDecision, Match, MatchHistory, ProbabilityTick,
)
from database.historical.tennis.shadow import (
    STRATEGY_VERSION, record_shadow_decision, settle_shadow_decisions,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        FavouriteShadowDecision.delete().execute()
        ProbabilityTick.delete().execute()
        Match.delete().execute()
        MatchHistory.delete().execute()
    yield
    with db_historical.connection_context():
        FavouriteShadowDecision.delete().execute()
        ProbabilityTick.delete().execute()
        Match.delete().execute()
        MatchHistory.delete().execute()


def _market():
    return {
        "id": "KXATPMATCH-SHADOW", "ticker_a": "SHADOW-A", "ticker_b": "SHADOW-B",
        "tour": "ATP", "surface": "Hard", "player_a_name": "Player A",
        "player_b_name": "Player B", "player_a_rank": 20, "player_b_rank": 5,
        "best_of": 3, "close_time": "2026-08-18T00:00:00Z",
    }


def _proposal():
    return {
        "market_id": "KXATPMATCH-SHADOW", "ticker": "SHADOW-B", "fav_side": "b",
        "stake_usd": 1.0, "fav_name": "Player B", "dog_name": "Player A",
        "model_probability": 0.72, "model_probability_before_injury": 0.73,
        "market_probability": 0.65, "model_edge": 0.07,
        "favorite_surface_52w_winrate": 0.7, "underdog_surface_52w_winrate": 0.5,
    }


def test_shadow_decision_links_exact_tick_and_is_idempotent():
    register_scanned_match(_market())
    save_probability_tick("KXATPMATCH-SHADOW", 0.37, 0.30, 0.65, 0.70)
    first = record_shadow_decision(_proposal())
    second = record_shadow_decision(_proposal())
    assert first.id == second.id
    assert first.probability_tick.market_price_b == 0.65
    assert first.strategy_version == STRATEGY_VERSION
    assert FavouriteShadowDecision.select().count() == 1


def test_shadow_settlement_uses_latest_tick_for_clv_and_real_winner():
    register_scanned_match(_market())
    save_probability_tick("KXATPMATCH-SHADOW", 0.37, 0.30, 0.65, 0.70)
    decision = record_shadow_decision(_proposal())
    save_probability_tick("KXATPMATCH-SHADOW", 0.28, 0.25, 0.72, 0.75)
    mark_match_settled("KXATPMATCH-SHADOW", "b")

    result = settle_shadow_decisions()
    decision = FavouriteShadowDecision.get_by_id(decision.id)
    assert result == {"eligible": 1, "settled": 1, "errors": 0}
    assert decision.status == "won"
    assert decision.closing_market_probability == pytest.approx(0.72)
    assert decision.clv == pytest.approx(0.07)
    assert decision.simulated_pnl_usd == pytest.approx(round(1 / 0.65 - 1, 4))
