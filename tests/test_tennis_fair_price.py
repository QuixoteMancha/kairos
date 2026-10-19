"""Regression coverage for every configured tennis fair-price blend."""

from models.fairprice.sports.tennis.tennisfmodel import TennisFModel
import models.fairprice.sports.tennis.tennisfmodel as tennis_module


def test_season_yelo_blend_is_part_of_the_fair_price_pipeline(monkeypatch):
    monkeypatch.setattr(tennis_module, "pre_match_win_prob", lambda *args, **kwargs: 0.5)
    for name in (
        "_blend_recent_form", "_blend_head_to_head", "_blend_set_sequence_resilience",
        "_blend_surface_form", "_blend_own_surface_elo",
        "_blend_tennisabstract_career_surface_winpct", "_blend_style_peer_group",
        "_apply_injury_risk_discount", "_blend_tournament_momentum",
    ):
        monkeypatch.setattr(TennisFModel, name, staticmethod(lambda fair_a, market: fair_a))

    calls = []

    def season_blend(fair_a, market):
        calls.append((fair_a, market["tour"]))
        return fair_a + 0.01

    monkeypatch.setattr(TennisFModel, "_blend_tennisabstract_season_form", staticmethod(season_blend))

    result = TennisFModel().calculate_fair_price({
        "player_a_rank": 10, "player_b_rank": 20, "best_of": 3,
        "tour": "ATP", "surface": "Hard",
    })

    assert calls == [(0.5, "ATP")]
    assert result == {"fair_a_price": 0.51, "fair_b_price": 0.49}
