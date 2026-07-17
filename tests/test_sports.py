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

from src.core.resolvers.sports import baseball, basketball, ice_hockey, american_football, football


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


class TestSettleFootball:
    """football.settle_bet -- el unico de los 5 deportes de equipo con empate
    real en 1X2 (los demas son two_outcome_only). Mismas convenciones que
    baseball/basketball/ice_hockey/american_football para asian_handicap
    (diff+linea>0) y total_goals (total>linea)."""

    def test_1x2_draw(self):
        assert football.settle_bet("1x2", "draw", None, 1, 1) == "won"
        assert football.settle_bet("1x2", "p1", None, 1, 1) == "lost"

    def test_1x2_home_win(self):
        assert football.settle_bet("1x2", "p1", None, 2, 1) == "won"
        assert football.settle_bet("1x2", "p2", None, 2, 1) == "lost"

    def test_dnb_draw_pushes(self):
        assert football.settle_bet("dnb", "p1", None, 1, 1) == "push"

    def test_asian_handicap_cover(self):
        # home -1.5, gana 3-1 (diff=2) -> adj=2-1.5=0.5>0 -> won
        assert football.settle_bet("asian_handicap", "p1", -1.5, 3, 1) == "won"

    def test_asian_handicap_not_cover(self):
        # home -1.5, gana 2-1 (diff=1) -> adj=1-1.5=-0.5<0 -> lost
        assert football.settle_bet("asian_handicap", "p1", -1.5, 2, 1) == "lost"

    def test_asian_handicap_push(self):
        assert football.settle_bet("asian_handicap", "p1", -1.0, 2, 1) == "push"

    def test_total_goals_over_under_push(self):
        assert football.settle_bet("total_goals", "over", 2.5, 2, 1) == "won"
        assert football.settle_bet("total_goals", "under", 2.5, 1, 1) == "won"
        assert football.settle_bet("total_goals", "over", 3.0, 2, 1) == "push"

    def test_btts(self):
        assert football.settle_bet("btts", "yes", None, 1, 1) == "won"
        assert football.settle_bet("btts", "yes", None, 1, 0) == "lost"
        assert football.settle_bet("btts", "no", None, 1, 0) == "won"


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

    def test_american_football_expected_total_none_without_real_points(self):
        # Fail-closed: sin points_for_pg/points_against_pg reales de AMBOS
        # equipos, expected_total debe ser None -- nunca una constante fabricada.
        probs = american_football.compute_basic_probs(1600.0, 1500.0)
        assert probs.expected_total is None
        assert probs.sigma_total is None

        # Falta solo uno de los 4 -> sigue siendo None (no basta parcial).
        probs_partial = american_football.compute_basic_probs(
            1600.0, 1500.0, home_pts_for_pg=24.0, home_pts_against_pg=20.0,
            away_pts_for_pg=22.0,
        )
        assert probs_partial.expected_total is None

    def test_american_football_expected_total_depends_on_all_four_inputs(self):
        # Dos pares de equipos con estadísticas distintas -> expected_total
        # distinto (no una constante de liga disfrazada de nuevo).
        low = american_football.compute_basic_probs(
            1600.0, 1500.0,
            home_pts_for_pg=15.0, home_pts_against_pg=15.0,
            away_pts_for_pg=15.0, away_pts_against_pg=15.0,
        )
        high = american_football.compute_basic_probs(
            1600.0, 1500.0,
            home_pts_for_pg=30.0, home_pts_against_pg=30.0,
            away_pts_for_pg=30.0, away_pts_against_pg=30.0,
        )
        assert low.expected_total is not None and high.expected_total is not None
        assert low.expected_total != high.expected_total
        assert high.expected_total > low.expected_total

        # Cambiar SOLO la defensa del rival (away_pts_against_pg) debe mover
        # expected_total en la dirección esperada (equipo local anota más
        # contra una defensa floja) -- prueba que depende realmente de los 4
        # números, no solo de un promedio superficial de ataque.
        weak_away_defense = american_football.compute_basic_probs(
            1600.0, 1500.0,
            home_pts_for_pg=20.0, home_pts_against_pg=20.0,
            away_pts_for_pg=20.0, away_pts_against_pg=35.0,
        )
        strong_away_defense = american_football.compute_basic_probs(
            1600.0, 1500.0,
            home_pts_for_pg=20.0, home_pts_against_pg=20.0,
            away_pts_for_pg=20.0, away_pts_against_pg=10.0,
        )
        assert weak_away_defense.expected_total > strong_away_defense.expected_total

    def test_american_football_total_restored_in_supported_leg_tipos(self):
        supported = american_football.get_supported_live_stat_leg_tipos()
        assert "total" in supported
        assert "total_points" in supported

    def test_ice_hockey_expected_total_none_without_team_names(self):
        # Fail-closed: sin home_team/away_team no hay forma de resolver goles
        # reales por equipo -> expected_total debe ser None, nunca fabricado.
        probs = ice_hockey.compute_match_probs(1600.0, 1500.0)
        assert probs.expected_total is None

    def test_ice_hockey_expected_total_none_when_real_data_missing(self, monkeypatch):
        # Team names presentes pero sin datos reales en TeamRating -> None.
        import src.core.resolvers.sports.ice_hockey as ih_mod

        def _fake_extra(sport, name):
            return None

        monkeypatch.setattr(
            "src.scrapers.team_data.common.get_team_rating_extra", _fake_extra
        )
        probs = ih_mod.compute_match_probs(
            1600.0, 1500.0, home_team="Colorado Avalanche", away_team="Dallas Stars"
        )
        assert probs.expected_total is None

    def test_ice_hockey_expected_total_none_when_only_one_team_has_data(self, monkeypatch):
        def _fake_extra(sport, name):
            if name == "Colorado Avalanche":
                return {"goals_for_pg": 3.7, "goals_against_pg": 2.5}
            return None

        monkeypatch.setattr(
            "src.scrapers.team_data.common.get_team_rating_extra", _fake_extra
        )
        probs = ice_hockey.compute_match_probs(
            1600.0, 1500.0, home_team="Colorado Avalanche", away_team="Dallas Stars"
        )
        assert probs.expected_total is None

    def test_ice_hockey_expected_total_depends_on_real_goals_data(self, monkeypatch):
        # Dos pares de equipos con estadísticas de goles distintas -> expected_total
        # distinto (no una constante de liga disfrazada de nuevo).
        low_data = {
            "Team A": {"goals_for_pg": 2.0, "goals_against_pg": 2.0},
            "Team B": {"goals_for_pg": 2.0, "goals_against_pg": 2.0},
        }
        high_data = {
            "Team C": {"goals_for_pg": 4.0, "goals_against_pg": 4.0},
            "Team D": {"goals_for_pg": 4.0, "goals_against_pg": 4.0},
        }

        def _fake_extra_low(sport, name):
            return low_data.get(name)

        def _fake_extra_high(sport, name):
            return high_data.get(name)

        monkeypatch.setattr(
            "src.scrapers.team_data.common.get_team_rating_extra", _fake_extra_low
        )
        low = ice_hockey.compute_match_probs(
            1600.0, 1500.0, home_team="Team A", away_team="Team B"
        )

        monkeypatch.setattr(
            "src.scrapers.team_data.common.get_team_rating_extra", _fake_extra_high
        )
        high = ice_hockey.compute_match_probs(
            1600.0, 1500.0, home_team="Team C", away_team="Team D"
        )

        assert low.expected_total is not None and high.expected_total is not None
        assert low.expected_total != high.expected_total
        assert high.expected_total > low.expected_total

    def test_ice_hockey_total_restored_in_supported_leg_tipos(self):
        supported = ice_hockey.get_supported_live_stat_leg_tipos()
        assert "total" in supported
        assert "total_goals" in supported


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
        from src.core.resolvers.sports import football
        # total con oponente igualando tras goles → danger
        is_d, _, _ = football.get_live_stat_danger("total_goals", 0, 1, in_play=True)
        assert is_d is True

    def test_tennis_set_total_trigger(self):
        from src.core.resolvers.sports import tennis
        is_d, is_w, _ = tennis.get_live_stat_danger(
            "set_total_juegos", 1, 0, in_play=True, leg_set_num=1
        )
        # set objetivo ya alcanzado (sets_played=1 >= target 1) → danger
        assert is_d is True



# ── compute_q4_hedge: pata de apuesta YA colocada debe usar SU odd original ───

class TestComputeQ4HedgeExistingBet:
    """
    Bug fijado: al cubrir una apuesta existente, la pata que representa esa
    apuesta ya colocada mostraba la odd LIVE actual (no la odd a la que
    realmente se apostó) y `guaranteed_return` se calculaba con esa odd live
    en vez de con `eb_odd` — el retorno bruto real es el mismo gane quien
    gane (por construcción del hedge), pero solo si se usa la odd ORIGINAL.
    """

    def _fake_probs(self):
        from unittest.mock import MagicMock
        return MagicMock(p_home_win=0.6, p_away_win=0.4, sigma_total=20.0, expected_total=200.0)

    def test_existing_bet_p1_uses_original_odd(self):
        from unittest.mock import patch
        from src.core.resolvers.sports import basketball as bb

        existing_bet = {"selection": "p1", "stake": 10.0, "odd": 1.5}
        live_odds = {
            "bookA": {"moneyline_home": 2.0, "moneyline_away": 1.0},
            "bookB": {"moneyline_home": 1.0, "moneyline_away": 2.5},
        }
        with patch.object(bb, "compute_match_probs", return_value=self._fake_probs()), \
             patch("scipy.stats.norm.sf", return_value=0.55):
            out = bb.compute_q4_hedge(
                current_score=(80, 60), quarter=4,
                home_elo=1600.0, away_elo=1500.0,
                available_live_odds=live_odds,
                existing_bet=existing_bet, base_stake=10.0,
            )

        assert out is not None
        eb_return = 10.0 * 1.5  # 15.0 — el retorno real de la apuesta ya colocada
        leg_p1 = next(leg for leg in out["legs"] if leg["side"] == "p1")
        assert leg_p1["odd"] == pytest.approx(1.5)   # odd ORIGINAL, no la live (2.0)
        assert out["guaranteed_return"] == pytest.approx(eb_return)  # no 20.0 (bug)

    def test_existing_bet_p2_uses_original_odd(self):
        from unittest.mock import patch
        from src.core.resolvers.sports import basketball as bb

        existing_bet = {"selection": "p2", "stake": 10.0, "odd": 1.5}
        live_odds = {
            "bookA": {"moneyline_home": 2.0, "moneyline_away": 1.0},
            "bookB": {"moneyline_home": 1.0, "moneyline_away": 2.5},
        }
        with patch.object(bb, "compute_match_probs", return_value=self._fake_probs()), \
             patch("scipy.stats.norm.sf", return_value=0.55):
            out = bb.compute_q4_hedge(
                current_score=(60, 80), quarter=4,
                home_elo=1500.0, away_elo=1600.0,
                available_live_odds=live_odds,
                existing_bet=existing_bet, base_stake=10.0,
            )

        assert out is not None
        eb_return = 10.0 * 1.5
        leg_p2 = next(leg for leg in out["legs"] if leg["side"] == "p2")
        assert leg_p2["odd"] == pytest.approx(1.5)   # odd ORIGINAL, no la live (2.5)
        assert out["guaranteed_return"] == pytest.approx(eb_return)
