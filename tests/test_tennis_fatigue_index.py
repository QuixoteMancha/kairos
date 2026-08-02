"""Tests del índice de fatiga (2026-08-01, pedido explícito del usuario:
horas jugadas en el torneo actual + torneo anterior + días de descanso +
edad como peso)."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from outer_apis.sports.tennis.fatigue import calculate_fatigue_index


def _row(tournament, dt, duration_minutes=90, subject_won=True):
    return {
        "tournament": tournament, "date": dt, "duration_minutes": duration_minutes,
        "opponent": "X", "subject_won": subject_won, "retired_or_walkover": False, "round": "R32",
    }


def test_fail_closed_without_current_tournament_data():
    with patch(
        "outer_apis.sports.tennis.fatigue.fetch_and_cache",
        return_value=[_row("OtherTournament", "2026-07-20")],
    ):
        result = calculate_fatigue_index("Player X", "Washington", date(2026, 8, 1))
    assert result is None


def test_fail_closed_without_any_duration_data():
    rows = [_row("Washington", "2026-07-30", duration_minutes=None)]
    with patch("outer_apis.sports.tennis.fatigue.fetch_and_cache", return_value=rows):
        result = calculate_fatigue_index("Player X", "Washington", date(2026, 8, 1))
    assert result is None


def test_sums_hours_current_tournament():
    rows = [
        _row("Washington", "2026-07-31", duration_minutes=120),
        _row("Washington", "2026-07-29", duration_minutes=90),
        _row("Miami", "2026-06-01", duration_minutes=100),
    ]
    with patch("outer_apis.sports.tennis.fatigue.fetch_and_cache", return_value=rows), \
         patch("outer_apis.sports.tennis.fatigue.get_age", return_value=None):
        result = calculate_fatigue_index("Player X", "Washington", date(2026, 8, 1))
    assert result["hours_current_tournament"] == 3.5  # (120+90)/60


def test_last_tournament_contributes_with_decay():
    rows = [
        _row("Washington", "2026-07-31", duration_minutes=60),
        _row("Miami", "2026-07-28", duration_minutes=120),  # 3 dias antes -> decay alto
    ]
    with patch("outer_apis.sports.tennis.fatigue.fetch_and_cache", return_value=rows), \
         patch("outer_apis.sports.tennis.fatigue.get_age", return_value=None):
        result = calculate_fatigue_index("Player X", "Washington", date(2026, 8, 1))
    assert result["hours_last_tournament"] == 2.0
    assert result["rest_days_since_last_tournament"] == 4
    assert result["raw_fatigue"] > result["hours_current_tournament"]  # el torneo anterior sí aporta


def test_last_tournament_fully_decayed_after_14_days():
    rows = [
        _row("Washington", "2026-07-31", duration_minutes=60),
        _row("Miami", "2026-07-01", duration_minutes=120),  # >14 dias antes
    ]
    with patch("outer_apis.sports.tennis.fatigue.fetch_and_cache", return_value=rows), \
         patch("outer_apis.sports.tennis.fatigue.get_age", return_value=None):
        result = calculate_fatigue_index("Player X", "Washington", date(2026, 8, 1))
    assert result["raw_fatigue"] == result["hours_current_tournament"]  # sin aporte del torneo anterior


def test_density_penalty_higher_for_back_to_back_days():
    rows_dense = [
        _row("Washington", "2026-07-31", duration_minutes=60),
        _row("Washington", "2026-07-30", duration_minutes=60),
    ]
    rows_spread = [
        _row("Washington", "2026-07-31", duration_minutes=60),
        _row("Washington", "2026-07-25", duration_minutes=60),
    ]
    with patch("outer_apis.sports.tennis.fatigue.get_age", return_value=None):
        with patch("outer_apis.sports.tennis.fatigue.fetch_and_cache", return_value=rows_dense):
            dense = calculate_fatigue_index("Player X", "Washington", date(2026, 8, 1))
        with patch("outer_apis.sports.tennis.fatigue.fetch_and_cache", return_value=rows_spread):
            spread = calculate_fatigue_index("Player X", "Washington", date(2026, 8, 1))
    assert dense["density_penalty"] > spread["density_penalty"]


def test_age_multiplier_amplifies_fatigue_over_30():
    rows = [_row("Washington", "2026-07-31", duration_minutes=120)]
    with patch("outer_apis.sports.tennis.fatigue.fetch_and_cache", return_value=rows):
        with patch("outer_apis.sports.tennis.fatigue.get_age", return_value=25):
            young = calculate_fatigue_index("Young Player", "Washington", date(2026, 8, 1))
        with patch("outer_apis.sports.tennis.fatigue.get_age", return_value=35):
            old = calculate_fatigue_index("Old Player", "Washington", date(2026, 8, 1))
    assert young["age_multiplier"] == 1.0
    assert old["age_multiplier"] > 1.0
    assert old["fatigue_index"] > young["fatigue_index"]


def test_age_multiplier_capped():
    rows = [_row("Washington", "2026-07-31", duration_minutes=60)]
    with patch("outer_apis.sports.tennis.fatigue.fetch_and_cache", return_value=rows), \
         patch("outer_apis.sports.tennis.fatigue.get_age", return_value=60):
        result = calculate_fatigue_index("Very Old Player", "Washington", date(2026, 8, 1))
    assert result["age_multiplier"] == 1.5


def test_explicit_age_param_overrides_lookup():
    rows = [_row("Washington", "2026-07-31", duration_minutes=60)]
    with patch("outer_apis.sports.tennis.fatigue.fetch_and_cache", return_value=rows), \
         patch("outer_apis.sports.tennis.fatigue.get_age") as mock_get_age:
        result = calculate_fatigue_index("Player X", "Washington", date(2026, 8, 1), age=40)
    mock_get_age.assert_not_called()
    assert result["age"] == 40
