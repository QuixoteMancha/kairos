#!/usr/bin/env python3
"""
Tests de la lógica sport-specific (src/core/sports/*).

Cubre la NUEVA lógica multi-deporte que el engine autónomo usa:
  - settle_bet(): liquidación determinista por deporte.
  - compute_match_probs() / compute_basic_probs(): modelos (probs acotadas y normalizadas).
  - get_live_stat_danger(): triggers de live-hedge (danger/watch) por deporte + leg_tipo.

Son funciones puras (sin DB ni red), ideales para tests unitarios rápidos.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.sports import baseball, basketball, ice_hockey, american_football


# ── settle_bet: deportes con liquidación determinista ─────────────────────────

class TestSettleMoneyline:
    @pytest.mark.parametrize("mod", [baseball, ice_hockey, american_football])
    def test_home_win(self, mod):
        assert mod.settle_bet("moneyline", "p1", None, 5, 2) == "won"

    @pytest.mark.parametrize("mod", [baseball, ice_hockey, american_football])
    def test_away_win(self, mod):
        assert mod.settle_bet("moneyline", "p1", None, 2, 5) == "lost"

    @pytest.mark.parametrize("mod", [baseball, ice_hockey, american_football])
    def test_p2_wins_when_away_ahead(self, mod):
        assert mod.settle_bet("moneyline", "p2", None, 2, 5) == "won"


class TestSettleSpreadLines:
    def test_baseball_run_line_cover(self):
        # P1 -1.5: gana por 3 → cubre (-1.5) → won
        assert baseball.settle_bet("run_line", "p1", -1.5, 5, 2) == "won"

    def test_baseball_run_line_not_cover(self):
        # P1 -1.5: gana solo por 1 → no cubre → lost
        assert baseball.settle_bet("run_line", "p1", -1.5, 3, 2) == "lost"

    def test_baseball_run_line_void_without_line(self):
        assert baseball.settle_bet("run_line", "p1", None, 3, 2) == "void"

    def test_basketball_spread_push(self):
        # P1 -5 con margen exacto de 5 → push
        assert basketball.settle_bet("spread", "p1", -5.0, 105, 100) == "push"


class TestSettleTotals:
    def test_baseball_total_over(self):
        assert baseball.settle_bet("total_runs", "over", 7.5, 5, 4) == "won"   # 9 > 7.5

    def test_baseball_total_under(self):
        assert baseball.settle_bet("total_runs", "under", 9.5, 4, 4) == "won"  # 8 < 9.5

    def test_baseball_total_push(self):
        assert baseball.settle_bet("total_runs", "over", 8.0, 4, 4) == "push"  # 8 == 8

    def test_ice_hockey_total_goals_over(self):
        assert ice_hockey.settle_bet("total_goals", "over", 5.5, 4, 3) == "won"  # 7 > 5.5


# ── compute_match_probs / compute_basic_probs: sanity de modelos ──────────────

class TestModelProbs:
    @pytest.mark.parametrize("mod,attr_win", [
        (baseball, "p_home_win"),
        (ice_hockey, "p_home_win"),
    ])
    def test_probs_normalized_and_bounded(self, mod, attr_win):
        probs = mod.compute_match_probs(1600.0, 1500.0)
        p_home = getattr(probs, attr_win)
        assert 0.0 <= p_home <= 1.0
        # home con más ELO debe ser favorito
        assert p_home > 0.5

    @pytest.mark.parametrize("mod", [baseball, ice_hockey])
    def test_higher_elo_more_favored(self, mod):
        strong = mod.compute_match_probs(1700.0, 1400.0).p_home_win
        even   = mod.compute_match_probs(1500.0, 1500.0).p_home_win
        assert strong > even

    def test_american_football_basic_probs_bounded(self):
        probs = american_football.compute_basic_probs(1600.0, 1500.0)
        assert 0.0 <= probs.p_home_win <= 1.0
        assert probs.p_home_win > 0.5


# ── get_live_stat_danger: triggers por deporte ────────────────────────────────

class TestLiveStatDanger:
    def test_pre_match_never_danger(self):
        # in_play=False → nunca danger ni watch
        for mod in (baseball, basketball, ice_hockey, american_football):
            is_d, is_w, _ = mod.get_live_stat_danger("moneyline", 0, 0, in_play=False)
            assert (is_d, is_w) == (False, False)

    def test_moneyline_danger_when_opp_leads(self):
        is_d, is_w, _ = baseball.get_live_stat_danger("moneyline", 2, 5, in_play=True)
        assert is_d is True and is_w is False

    def test_moneyline_watch_when_we_lead(self):
        is_d, is_w, _ = baseball.get_live_stat_danger("moneyline", 5, 2, in_play=True)
        assert is_d is False and is_w is True

    def test_football_total_pressure(self):
        from src.core.sports import football
        # total con oponente igualando tras goles → danger
        is_d, _, _ = football.get_live_stat_danger("total_goals", 0, 1, in_play=True)
        assert is_d is True

    def test_tennis_set_total_trigger(self):
        from src.core.sports import tennis
        is_d, is_w, _ = tennis.get_live_stat_danger(
            "set_total_juegos", 1, 0, in_play=True, leg_set_num=1
        )
        # set objetivo ya alcanzado (sets_played=1 >= target 1) → danger
        assert is_d is True
