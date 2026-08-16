"""Tests del relleno reactivo de huecos -- 2026-08-16, pedido explícito
del usuario ("que escanee qué datos faltan y en base a los fetch que
tenemos vaya llenando los datos que faltan", sin rehacer el backfill)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import MatchHistory, Player
from scripts.tennis.fill_data_gaps import (
    count_data_gaps, fill_player_ages, fill_player_bio,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        Player.delete().execute()
    yield
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        Player.delete().execute()


def _player(name: str, **overrides) -> Player:
    base = {"canonical_name": name, "created_at": datetime.now(timezone.utc)}
    base.update(overrides)
    return Player.create(**base)


def _match(**overrides) -> MatchHistory:
    base = {
        "match_id": "m1", "match_date": "2024-06-01",
        "fetched_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return MatchHistory.create(**base)


# --- fill_player_ages (sin red) ---

def test_fills_winner_and_loser_age_from_birthdates():
    winner = _player("Winner One", birthdate=date(1994, 6, 1))
    loser = _player("Loser Two", birthdate=date(2004, 6, 1))
    match = _match(player_a=winner, player_b=loser, winner=winner)

    result = fill_player_ages()

    assert result["updated"] == 1
    row = MatchHistory.get_by_id(match.id)
    assert row.winner_age == pytest.approx(30.0, abs=0.05)
    assert row.loser_age == pytest.approx(20.0, abs=0.05)


def test_fills_ages_when_winner_is_player_b():
    """El ganador puede estar en cualquiera de los 2 lados -- la edad
    debe seguir al JUGADOR, no a la posición."""
    a = _player("Side A", birthdate=date(2000, 6, 1))
    b = _player("Side B", birthdate=date(1990, 6, 1))
    match = _match(player_a=a, player_b=b, winner=b)

    fill_player_ages()

    row = MatchHistory.get_by_id(match.id)
    assert row.winner_age == pytest.approx(34.0, abs=0.05)  # b
    assert row.loser_age == pytest.approx(24.0, abs=0.05)   # a


def test_ages_fail_closed_without_birthdate():
    winner = _player("No Birthdate")
    loser = _player("Also None")
    match = _match(player_a=winner, player_b=loser, winner=winner)

    fill_player_ages()

    row = MatchHistory.get_by_id(match.id)
    assert row.winner_age is None, "no debe fabricar una edad sin fecha de nacimiento"
    assert row.loser_age is None


def test_ages_discard_absurd_values():
    """Fecha de nacimiento POSTERIOR al partido -> edad negativa, se
    descarta en vez de guardarse (los feeds ya mandaron fechas absurdas
    antes en este proyecto)."""
    winner = _player("Time Traveler", birthdate=date(2030, 1, 1))
    loser = _player("Normal", birthdate=date(1995, 1, 1))
    match = _match(player_a=winner, player_b=loser, winner=winner)

    fill_player_ages()

    row = MatchHistory.get_by_id(match.id)
    assert row.winner_age is None
    assert row.loser_age == pytest.approx(29.4, abs=0.2)


def test_ages_are_idempotent_and_skip_already_filled():
    winner = _player("Winner One", birthdate=date(1994, 6, 1))
    loser = _player("Loser Two", birthdate=date(2004, 6, 1))
    _match(player_a=winner, player_b=loser, winner=winner)

    first = fill_player_ages()
    second = fill_player_ages()

    assert first["updated"] == 1
    assert second["updated"] == 0, "no debe re-escribir lo ya completo"


# --- fill_player_bio (usa match-detail existente) ---

def test_bio_reresolves_match_detail_for_incomplete_player():
    player = _player("Incomplete Player", birthdate=None)
    _match(match_id="real123", player_a=player)

    with patch(
        "database.historical.tennis.writer._resolve_and_save_via_match_detail",
        return_value=True,
    ) as resolver:
        result = fill_player_bio(limit=10)

    assert result["resolved"] == 1
    resolver.assert_called_once_with("Incomplete Player", "real123")


def test_bio_skips_players_whose_only_match_is_synthetic():
    """Los `recentform:` son ids sintéticos -- no existe una página de
    match-detail que resolver, pedirla sería un request garantizado a
    fallar."""
    player = _player("Only Synthetic", birthdate=None)
    _match(match_id="recentform:a|b|2024-06-01", player_a=player)

    with patch(
        "database.historical.tennis.writer._resolve_and_save_via_match_detail",
        return_value=True,
    ) as resolver:
        result = fill_player_bio(limit=10)

    resolver.assert_not_called()
    assert result["players_without_real_match"] == 1


def test_bio_respects_limit():
    for i in range(5):
        p = _player(f"Player {i}", birthdate=None)
        _match(match_id=f"real{i}", player_a=p)

    with patch(
        "database.historical.tennis.writer._resolve_and_save_via_match_detail",
        return_value=True,
    ) as resolver:
        result = fill_player_bio(limit=2)

    assert resolver.call_count == 2
    assert result["processed"] == 2


def test_bio_survives_a_failing_player_and_continues():
    for i in range(3):
        p = _player(f"Player {i}", birthdate=None)
        _match(match_id=f"real{i}", player_a=p)

    with patch(
        "database.historical.tennis.writer._resolve_and_save_via_match_detail",
        side_effect=[RuntimeError("red caída"), True, True],
    ):
        result = fill_player_bio(limit=3)

    assert result["processed"] == 3
    assert result["resolved"] == 2, "un fallo puntual no debe frenar la tanda"


# --- count_data_gaps ---

def test_count_data_gaps_reports_real_state():
    p = _player("Someone", birthdate=None)
    _match(player_a=p)

    gaps = count_data_gaps()

    assert gaps["matches"] == 1
    assert gaps["matches_sin_elo"] == 1
    assert gaps["matches_sin_duracion"] == 1
    assert gaps["players"] == 1
    assert gaps["players_sin_bio"] == 1
