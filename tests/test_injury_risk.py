"""Tests de la señal de riesgo de lesión (retiro/no-presentación real
reciente, pedido explícito del usuario 2026-07-31 tras el caso real de
Svitolina). Mockea toda request HTTP real."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from outer_apis.scrapers.tennis.tennisabstract_player_scraper import (
    _parse_date, get_recent_results as scraper_get_recent_results,
)
from outer_apis.sports.tennis.injury_risk import (
    get_injury_risk_flag, INJURY_LOOKBACK_DAYS, calculate_post_injury_penalty,
    get_post_injury_performance_penalty,
)


def test_post_injury_penalty_combines_recency_and_measured_declines():
    result = calculate_post_injury_penalty(
        days_since_recovery=30, elo_delta=-50,
        service_delta=-5, return_delta=-0.05,
    )
    assert result == {
        "total": 0.055, "recency": 0.02, "elo_decline": 0.015,
        "service_decline": 0.01, "return_decline": 0.01,
    }


def test_post_injury_penalty_never_rewards_improvement_and_is_capped():
    improved = calculate_post_injury_penalty(
        days_since_recovery=100, elo_delta=50, service_delta=5, return_delta=0.1,
    )
    assert improved["total"] == 0.0
    severe = calculate_post_injury_penalty(
        days_since_recovery=0, injury_active=True,
        elo_delta=-1000, service_delta=-100, return_delta=-1,
    )
    assert severe["total"] == 0.10


def test_live_post_injury_signal_is_cached_but_historical_is_not():
    from outer_apis.sports.tennis import injury_risk

    injury_risk._signal_cache.clear()
    with patch.object(
        injury_risk, "_compute_post_injury_performance_penalty",
        return_value={"penalty": 0.02},
    ) as compute:
        assert get_post_injury_performance_penalty("Player", surface="Hard")["penalty"] == 0.02
        assert get_post_injury_performance_penalty("Player", surface="Hard")["penalty"] == 0.02
        get_post_injury_performance_penalty("Player", as_of="2025-01-01", surface="Hard")
    assert compute.call_count == 2


def test_parse_date_iso_conversion():
    assert _parse_date("21-Jun-2026") == "2026-06-21"
    assert _parse_date("garbage") is None


def test_get_recent_results_detects_real_walkover_not_opponents():
    """Caso real que motivó la feature (verificado contra
    tennisabstract.com real durante la sesión, Svitolina 2026-06-21 Bad
    Homburg): la propia jugadora se retira (walkover) en un partido, y en
    OTRO fue el RIVAL quien se retiró contra ella -- solo el primero debe
    marcar retired_or_walkover=True."""
    from outer_apis.scrapers.tennis import tennisabstract_player_scraper as scraper

    fake_section = (
        '<h1 id="recent-results-h">Recent Results</h1>'
        '<table id="recent-results"><tbody>'
        '<tr><td>21-Jun-2026</td><td>Bad Homburg</td><td>Clay</td><td>1R</td><td>1</td><td>50</td>'
        '<td><a href="...">Xin Yu Wang</a> d. (3)<b>Svitolina</b></td><td>W/O</td></tr>'
        '<tr><td>15-Jun-2026</td><td>Berlin</td><td>Grass</td><td>1R</td><td>1</td><td>50</td>'
        '<td>(6)<b>Svitolina</b> d. <a href="...">Anna Kalinskaya</a></td><td>6-1 4-1 RET</td></tr>'
        '</tbody></table>'
    )

    with patch.object(scraper, "_fetch_player_js", return_value=fake_section):
        results = scraper.get_recent_results("Elina Svitolina")

    walkover_row = next(r for r in results if r["opponent"] == "Xin Yu Wang")
    opponent_retired_row = next(r for r in results if r["opponent"] == "Anna Kalinskaya")
    assert walkover_row["retired_or_walkover"] is True
    assert opponent_retired_row["retired_or_walkover"] is False  # el RIVAL se retiró, no el sujeto


def test_get_recent_results_exposes_real_score():
    """2026-08-02, pedido explícito del usuario -- necesario para saber
    si el favorito perdió el 1er set y si fue por tiebreak."""
    from outer_apis.scrapers.tennis import tennisabstract_player_scraper as scraper

    fake_section = (
        '<h1 id="recent-results-h">Recent Results</h1>'
        '<table id="recent-results"><tbody>'
        '<tr><td>21-Jun-2026</td><td>Bad Homburg</td><td>Clay</td><td>SF</td><td>1</td><td>50</td>'
        '<td>(2)<b>Sinner</b> d. <a href="...">Alcaraz</a></td><td>7-6(5) 6-3</td></tr>'
        '</tbody></table>'
    )

    with patch.object(scraper, "_fetch_player_js", return_value=fake_section):
        results = scraper.get_recent_results("Jannik Sinner")

    assert results[0]["score"] == "7-6(5) 6-3"


def test_get_injury_risk_flag_none_without_any_data():
    with patch("outer_apis.sports.tennis.injury_risk._fetch_and_cache", return_value=[]):
        assert get_injury_risk_flag("Nobody") is None


def test_get_injury_risk_flag_false_when_no_recent_retirement():
    fake_rows = [
        {"date": "2026-07-27", "tournament": "Washington Dc", "opponent": "Kudermetova", "subject_won": True, "retired_or_walkover": False},
    ]
    with patch("outer_apis.sports.tennis.injury_risk._fetch_and_cache", return_value=fake_rows):
        result = get_injury_risk_flag("Elina Svitolina")
    assert result == {"flagged": False, "date": None, "opponent": None, "tournament": None}


def test_get_injury_risk_flag_true_within_lookback_window():
    recent_date = (date.today() - timedelta(days=10)).isoformat()
    fake_rows = [
        {"date": recent_date, "tournament": "Bad Homburg", "opponent": "Xin Yu Wang", "subject_won": False, "retired_or_walkover": True},
    ]
    with patch("outer_apis.sports.tennis.injury_risk._fetch_and_cache", return_value=fake_rows):
        result = get_injury_risk_flag("Elina Svitolina")
    assert result["flagged"] is True
    assert result["opponent"] == "Xin Yu Wang"


def test_get_injury_risk_flag_false_when_retirement_too_old():
    old_date = (date.today() - timedelta(days=INJURY_LOOKBACK_DAYS + 5)).isoformat()
    fake_rows = [
        {"date": old_date, "tournament": "Bad Homburg", "opponent": "Xin Yu Wang", "subject_won": False, "retired_or_walkover": True},
    ]
    with patch("outer_apis.sports.tennis.injury_risk._fetch_and_cache", return_value=fake_rows):
        result = get_injury_risk_flag("Elina Svitolina")
    assert result["flagged"] is False


def test_get_injury_risk_flag_ignores_opponent_retirements():
    """El caso real que distingue esta feature de un falso positivo:
    Kalinskaya se retiró CONTRA Svitolina -- eso ya viene filtrado por el
    scraper (retired_or_walkover solo es True cuando el SUJETO se
    retiró), pero se verifica acá el contrato completo del facade."""
    recent_date = (date.today() - timedelta(days=5)).isoformat()
    fake_rows = [
        {"date": recent_date, "tournament": "Berlin", "opponent": "Anna Kalinskaya", "subject_won": True, "retired_or_walkover": False},
    ]
    with patch("outer_apis.sports.tennis.injury_risk._fetch_and_cache", return_value=fake_rows):
        result = get_injury_risk_flag("Elina Svitolina")
    assert result["flagged"] is False
