"""Tests del rediseño de caching de ratings de deportes en equipo
(2026-07-28, mismo patrón aplicado en tenis, ver memoria
kairos-tennis-brightdata-db-cache-2026-07-28): los 5 dicts en memoria
(`_ratings_cache`, uno por deporte) se eliminaron -- cada facade consulta
`TeamRating` (SQLite WAL, upsert current-state) antes de pegarle a la
API externa correspondiente (MLB Stats/NBA Stats/NHL Stats/nflverse/
ClubElo/ESPN).

Usa la DB SQLite temporal/aislada de tests/conftest.py. Mockea SOLO los
scrapers, nunca pega a las APIs reales."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.team_sports.teamrating import TeamRating
from database.historical.team_sports.writer import get_current_ratings, get_current_ratings_detailed


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TeamRating.delete().execute()
    yield
    with db_historical.connection_context():
        TeamRating.delete().execute()


def _insert_rating(sport, name, elo, source, updated_at, extra_json="{}"):
    with db_historical.connection_context():
        TeamRating.create(
            created_at=updated_at, sport=sport, name=name, name_norm=name.strip().lower(),
            league="", elo=elo, n_games=0, source=source,
            updated_at=updated_at.isoformat(), extra_json=extra_json,
        )


def test_get_current_ratings_none_when_no_rows():
    assert get_current_ratings("baseball", source="kairos_elo_engine") is None


def test_get_current_ratings_returns_fresh_dict():
    _insert_rating("baseball", "Yankees", 1550.0, "kairos_elo_engine",
                    datetime.now(timezone.utc) - timedelta(minutes=10))
    result = get_current_ratings("baseball", source="kairos_elo_engine", max_age_hours=6.0)
    assert result == {"Yankees": 1550.0}


def test_get_current_ratings_none_when_stale():
    _insert_rating("baseball", "Yankees", 1550.0, "kairos_elo_engine",
                    datetime.now(timezone.utc) - timedelta(hours=8))
    assert get_current_ratings("baseball", source="kairos_elo_engine", max_age_hours=6.0) is None


def test_get_current_ratings_filters_by_source():
    """football = ClubElo + MLS comparten el mismo `sport` bucket --
    filtrar por source es obligatorio para no mezclar ratings de las 2
    fuentes (ver mls_ratings.py docstring)."""
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    _insert_rating("football", "Real Madrid", 2000.0, "clubelo", now)
    _insert_rating("football", "LA Galaxy", 1500.0, "mls_elo", now)

    clubelo_only = get_current_ratings("football", source="clubelo")
    mls_only = get_current_ratings("football", source="mls_elo")

    assert clubelo_only == {"Real Madrid": 2000.0}
    assert mls_only == {"LA Galaxy": 1500.0}


def test_get_current_ratings_detailed_reconstructs_level_from_extra_json():
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    _insert_rating("football", "Real Madrid", 2000.0, "clubelo", now,
                    extra_json=json.dumps({"level": 1}))
    result = get_current_ratings_detailed("football", source="clubelo")
    assert result == [{"team_name": "Real Madrid", "country": "", "elo": 2000.0, "level": 1}]


def test_get_current_ratings_detailed_sorted_by_elo_desc():
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    _insert_rating("football", "Weak Team", 1200.0, "clubelo", now)
    _insert_rating("football", "Strong Team", 2100.0, "clubelo", now)
    result = get_current_ratings_detailed("football", source="clubelo")
    assert [t["team_name"] for t in result] == ["Strong Team", "Weak Team"]


def test_football_get_team_ratings_skips_live_fetch_when_db_fresh():
    from outer_apis.sports.soccer import ratings as football_ratings

    _insert_rating("football", "Real Madrid", 2000.0, "clubelo",
                    datetime.now(timezone.utc) - timedelta(minutes=1),
                    extra_json=json.dumps({"level": 1}))

    with patch("outer_apis.sports.soccer.ratings.clubelo_scraper.get_current_ratings") as mock_fetch:
        result = football_ratings.get_team_ratings()

    mock_fetch.assert_not_called()
    assert result == [{"team_name": "Real Madrid", "country": "", "elo": 2000.0, "level": 1}]


