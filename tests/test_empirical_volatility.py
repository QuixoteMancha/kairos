"""Tests de la volatilidad empírica por bucket de ranking/superficie/tour
(2026-07-31, pedido explícito del usuario) y del fallback seguro de
TennisVModel a la simulación Monte Carlo cuando no hay muestra real
suficiente."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.models import Match as TennisScannedMatch
from database.historical.tennis.models import ProbabilityTick as TennisMatchProbabilityTick
from database.historical.tennis.archive_db import ArchiveNotMountedError
from outer_apis.sports.tennis.volatility_calibration import (
    _bucket_for_rank, _matchup_buckets_match, get_empirical_volatility_for_matchup, MIN_MATCHES_FOR_STAT,
)
from models.volatility.sports.tennis.tennisvmodel import TennisVModel


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisMatchProbabilityTick.delete().execute()
        TennisScannedMatch.delete().execute()
    yield
    with db_historical.connection_context():
        TennisMatchProbabilityTick.delete().execute()
        TennisScannedMatch.delete().execute()


def _insert_resolved_match(market_id, rank_a, rank_b, surface, tour, prices, winner="a"):
    now = datetime.now(timezone.utc)
    m = TennisScannedMatch.create(
        market_id=market_id, ticker_a=f"{market_id}-A", ticker_b=f"{market_id}-B",
        tour=tour, surface=surface, player_a_name="A", player_b_name="B",
        player_a_rank_at_match=rank_a, player_b_rank_at_match=rank_b, best_of=3,
        close_time="2026-08-01T00:00:00Z", detected_at=now, winner=winner, settled_at=now,
    )
    for i, p in enumerate(prices):
        TennisMatchProbabilityTick.create(match=m, observed_at=now + timedelta(minutes=i), market_price_a=p, model_fair_a=None)
    return m


def test_bucket_for_rank_matches_known_edges():
    assert _bucket_for_rank(5) == (1, 10)
    assert _bucket_for_rank(15) == (11, 20)
    assert _bucket_for_rank(500) == (201, 100_000)


def test_matchup_buckets_match_fail_closed_without_rank():
    class FakeMatch:
        player_a_rank_at_match = None
        player_b_rank_at_match = 20
    assert _matchup_buckets_match(FakeMatch(), (1, 10), (11, 20)) is False


def test_get_empirical_volatility_none_when_insufficient_matches():
    # solo 2 partidos con el mismo perfil -- por debajo de MIN_MATCHES_FOR_STAT (5)
    _insert_resolved_match("M1", 5, 15, "Hard", "ATP", [0.6, 0.5, 0.7])
    _insert_resolved_match("M2", 3, 18, "Hard", "ATP", [0.55, 0.65])

    with patch("outer_apis.sports.tennis.volatility_calibration._ensure_archive_db_initialized",
               side_effect=ArchiveNotMountedError("no disk")):
        result = get_empirical_volatility_for_matchup(4, 16, "Hard", "ATP")
    assert result is None


def test_get_empirical_volatility_computes_real_mean_with_enough_matches():
    for i in range(MIN_MATCHES_FOR_STAT):
        _insert_resolved_match(f"M{i}", 5, 15, "Hard", "ATP", [0.4, 0.6])  # std = 0.1 cada uno

    with patch("outer_apis.sports.tennis.volatility_calibration._ensure_archive_db_initialized",
               side_effect=ArchiveNotMountedError("no disk mounted")):
        result = get_empirical_volatility_for_matchup(4, 16, "Hard", "ATP")

    assert result is not None
    assert result["mean_volatility"] == pytest.approx(0.1)
    assert result["sources_used"] == "recent_only"


def test_get_empirical_volatility_none_for_different_surface():
    for i in range(MIN_MATCHES_FOR_STAT):
        _insert_resolved_match(f"M{i}", 5, 15, "Clay", "ATP", [0.4, 0.6])

    with patch("outer_apis.sports.tennis.volatility_calibration._ensure_archive_db_initialized",
               side_effect=ArchiveNotMountedError("no disk")):
        result = get_empirical_volatility_for_matchup(4, 16, "Hard", "ATP")  # pide Hard, la data es Clay
    assert result is None


def test_tennisvmodel_falls_back_to_simulation_when_no_empirical_data():
    """Sin historial real (caso normal hoy) -- debe seguir devolviendo un
    número simulado, nunca 0.0 por falta de historia."""
    model = TennisVModel()
    market = {
        "current_a_price": 0.6, "current_b_price": 0.4,
        "player_a_rank": 5, "player_b_rank": 50, "best_of": 3, "tour": "ATP", "surface": "Hard",
    }
    with patch("models.volatility.sports.tennis.tennisvmodel.TennisVModel._empirical_volatility", return_value=None):
        vol = model.calculate_volatility(market)
    assert vol > 0.0  # cayó a la simulación, no a 0.0 fabricado


def test_tennisvmodel_uses_empirical_when_available():
    model = TennisVModel()
    market = {
        "current_a_price": 0.6, "current_b_price": 0.4,
        "player_a_rank": 5, "player_b_rank": 50, "best_of": 3, "tour": "ATP", "surface": "Hard",
    }
    with patch("models.volatility.sports.tennis.tennisvmodel.TennisVModel._empirical_volatility", return_value=0.1234):
        vol = model.calculate_volatility(market)
    assert vol == pytest.approx(0.1234)
