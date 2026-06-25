"""
test_resolve_total_leg.py — _resolve_total_leg_final_value (settlement.py).

Helper nuevo que obtiene el valor final del total (goles/puntos/runs/juegos)
para liquidar patas 'total' (over/under), via Flashscore (unica fuente con
total_games para tenis y home_score+away_score para deportes de equipo --
Sofascore no expone esto hoy).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.orchestration.settlement import _resolve_total_leg_final_value


def test_team_sport_sums_home_and_away_score():
    with patch("src.scrapers.flashscore_scraper.find_team_match_result") as mock_team:
        mock_team.return_value = {"home_score": 110, "away_score": 100, "resultado": "110-100"}
        val, resultado, source = _resolve_total_leg_final_value("basketball", "Lakers", "Celtics", "2026-06-20")
    assert val == 210.0
    assert resultado == "110-100"
    assert source == "flashscore"


def test_tennis_uses_total_games_directly():
    with patch("src.scrapers.flashscore_scraper.find_match_result") as mock_player:
        mock_player.return_value = {"total_games": 24, "resultado": "2-1"}
        val, resultado, source = _resolve_total_leg_final_value("tennis", "Alcaraz", "Sinner", "2026-06-20")
    assert val == 24.0
    assert source == "flashscore"


def test_returns_none_when_source_has_no_result_yet():
    with patch("src.scrapers.flashscore_scraper.find_team_match_result", return_value=None):
        val, resultado, source = _resolve_total_leg_final_value("baseball", "Yankees", "Red Sox", "2026-06-20")
    assert val is None


def test_returns_none_on_scraper_exception_does_not_raise():
    with patch("src.scrapers.flashscore_scraper.find_team_match_result", side_effect=RuntimeError("network")):
        val, resultado, source = _resolve_total_leg_final_value("baseball", "Yankees", "Red Sox", "2026-06-20")
    assert val is None
