"""Tests de edad real por jugador (2026-08-01, pedido explícito del
usuario para el índice de fatiga: 'jugadores sobre 30 no toleran bien
tanta fatiga acumulada')."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.tennisplayerbio import TennisPlayerBio
from outer_apis.sports.tennis.player_age import get_date_of_birth_cached, get_age
from outer_apis.scrapers.tennis.tennisabstract_player_scraper import get_date_of_birth


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisPlayerBio.delete().execute()
    yield
    with db_historical.connection_context():
        TennisPlayerBio.delete().execute()


def _fake_html_with_dob(dob_digits: str) -> str:
    return f"<html>...var dob = {dob_digits};...</html>"


def test_get_date_of_birth_parses_real_format():
    class _FakeResp:
        status_code = 200
        text = _fake_html_with_dob("20030505")

    with patch(
        "outer_apis.scrapers.tennis.tennisabstract_player_scraper.requests.get",
        return_value=_FakeResp(),
    ):
        assert get_date_of_birth("Carlos Alcaraz") == "2003-05-05"


def test_get_date_of_birth_fail_closed_no_match():
    class _FakeResp:
        status_code = 200
        text = "<html>no dob here</html>"

    with patch(
        "outer_apis.scrapers.tennis.tennisabstract_player_scraper.requests.get",
        return_value=_FakeResp(),
    ):
        assert get_date_of_birth("Nobody") is None


def test_get_date_of_birth_fail_closed_on_http_error():
    class _FakeResp:
        status_code = 404
        text = ""

    with patch(
        "outer_apis.scrapers.tennis.tennisabstract_player_scraper.requests.get",
        return_value=_FakeResp(),
    ):
        assert get_date_of_birth("Whoever") is None


def test_get_date_of_birth_cached_persists_and_reuses():
    with patch(
        "outer_apis.sports.tennis.player_age._fetch_dob", return_value="2004-04-02",
    ) as mock_fetch:
        dob1 = get_date_of_birth_cached("Diana Shnaider")
        dob2 = get_date_of_birth_cached("Diana Shnaider")

    assert dob1 == date(2004, 4, 2)
    assert dob2 == date(2004, 4, 2)
    mock_fetch.assert_called_once()  # 2do llamado sirvió del cache, no refetcheó


def test_get_age_computes_correctly_before_and_after_birthday():
    with patch(
        "outer_apis.sports.tennis.player_age._fetch_dob", return_value="2000-08-15",
    ):
        age_before = get_age("Someone", as_of=date(2026, 8, 1))
        age_after = get_age("Someone", as_of=date(2026, 8, 20))

    assert age_before == 25
    assert age_after == 26


def test_get_age_fail_closed_without_real_dob():
    with patch("outer_apis.sports.tennis.player_age._fetch_dob", return_value=None):
        assert get_age("Unknown Player") is None
