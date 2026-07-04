"""
test_team_data.py — normalización de nombres + upsert/lookup de TeamRating.

No requiere red: prueba las funciones puras de matching y el ciclo
upsert→get sobre la DB en memoria.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(
    reason="Team ELO ratings (ClubElo/nflverse/NHL) fed sports value-betting "
    "models with no prediction-market equivalent — Polymarket/Kalshi/Manifold "
    "resolve via API/oracle status, not team strength ratings. Archived "
    "during the prediction-markets pivot (2026-07-03) along with the "
    "underlying sports bookies (src/scrapers/forgotten/)."
)

from src.scrapers.forgotten.team_data.common import (
    get_team_rating,
    normalize_team_name,
    upsert_team_rating,
)


@pytest.fixture(autouse=True)
def _db():
    from src.database.database import init_db
    init_db()
    yield


def test_normalize_basic():
    assert normalize_team_name("Manchester City") == "man city"
    assert normalize_team_name("Manchester United") == "man united"
    assert normalize_team_name("PSG") == "paris sg"
    assert normalize_team_name("Bayern Munich") == "bayern"


def test_normalize_strips_accents_and_parens():
    # acentos y "(usuario)" de bookies sociales
    assert normalize_team_name("Atlético Madrid (juanito)") == "atletico"
    assert normalize_team_name("Real Betis") == "betis"


def test_normalize_empty():
    assert normalize_team_name("") == ""
    assert normalize_team_name(None) == ""


def test_upsert_and_exact_lookup():
    upsert_team_rating(sport="football", name="Kairos Test FC", elo=1777.0,
                       league="TEST", source="unit")
    got = get_team_rating("football", "Kairos Test FC")
    assert got == pytest.approx(1777.0, abs=0.5)


def test_upsert_is_idempotent_update():
    upsert_team_rating(sport="football", name="Update Test", elo=1500.0, source="unit")
    upsert_team_rating(sport="football", name="Update Test", elo=1620.0, source="unit")
    got = get_team_rating("football", "Update Test")
    assert got == pytest.approx(1620.0, abs=0.5)


def test_fuzzy_token_match():
    upsert_team_rating(sport="football", name="Real Madrid", elo=1900.0, source="unit")
    # 'Madrid' solo debe resolver por solapamiento de tokens
    got = get_team_rating("football", "Madrid")
    assert got is not None
    assert got == pytest.approx(1900.0, abs=0.5)


def test_no_match_returns_none():
    assert get_team_rating("football", "Equipo Inexistente Zzz Qqq") is None


def test_sport_isolation():
    upsert_team_rating(sport="ice_hockey", name="Shared Name", elo=1400.0, source="unit")
    upsert_team_rating(sport="american_football", name="Shared Name", elo=1650.0, source="unit")
    assert get_team_rating("ice_hockey", "Shared Name") == pytest.approx(1400.0, abs=0.5)
    assert get_team_rating("american_football", "Shared Name") == pytest.approx(1650.0, abs=0.5)


def test_football_elo_adjusts_probs():
    """El modelo Dixon-Coles debe diferenciar partidos según el ELO real."""
    from src.core.sports.football import MatchProbs
    strong = MatchProbs.from_teams("A", "B", home_elo=1950.0, away_elo=1500.0)
    weak = MatchProbs.from_teams("A", "B", home_elo=1500.0, away_elo=1950.0)
    generic = MatchProbs.from_teams("A", "B")
    assert strong.p_home > generic.p_home > weak.p_home
    assert strong.p_home > 0.6
    assert weak.p_home < 0.25


def test_team_elo_lookup_tennis_returns_none():
    from src.core.orchestration.scanning import _team_elo_lookup
    assert _team_elo_lookup("Djokovic", "Nadal", "tennis") == (None, None)
