"""Tests de `build_tennis_player_wikidata_reference.py` -- 2026-08-10,
pedido explícito del usuario ("dejalo como base para que después no se
armen enredos"). Mockea el fetch de red, nunca pega a Wikidata."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.models import TennisPlayerWikidataReference
from scripts.tennis.build_tennis_player_wikidata_reference import build_reference


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisPlayerWikidataReference.delete().execute()
    yield
    with db_historical.connection_context():
        TennisPlayerWikidataReference.delete().execute()


def test_creates_one_row_per_distinct_wikidata_id():
    fake_rows = [
        {"wikidata_id": "Q38129", "given_name": "Barbara", "family_name": "Potter"},
        {"wikidata_id": "Q45010", "given_name": "Ricardo", "family_name": "Mello"},
    ]
    with patch(
        "scripts.tennis.build_tennis_player_wikidata_reference.fetch_all_tennis_players_given_family_names",
        return_value=fake_rows,
    ):
        result = build_reference()

    assert result["created"] == 2
    assert TennisPlayerWikidataReference.select().count() == 2
    row = TennisPlayerWikidataReference.get(TennisPlayerWikidataReference.wikidata_id == "Q38129")
    assert row.canonical_name == "Barbara Potter"


def test_dedupes_by_wikidata_id_keeps_first_variant():
    fake_rows = [
        {"wikidata_id": "Q1", "given_name": "First", "family_name": "Surname"},
        {"wikidata_id": "Q1", "given_name": "Nickname", "family_name": "Surname"},
    ]
    with patch(
        "scripts.tennis.build_tennis_player_wikidata_reference.fetch_all_tennis_players_given_family_names",
        return_value=fake_rows,
    ):
        result = build_reference()

    assert result["distinct_players"] == 1
    row = TennisPlayerWikidataReference.get(TennisPlayerWikidataReference.wikidata_id == "Q1")
    assert row.given_name == "First"


def test_rerun_updates_existing_row_instead_of_duplicating():
    fake_rows = [{"wikidata_id": "Q1", "given_name": "Given", "family_name": "Family"}]
    with patch(
        "scripts.tennis.build_tennis_player_wikidata_reference.fetch_all_tennis_players_given_family_names",
        return_value=fake_rows,
    ):
        build_reference()
        result = build_reference()

    assert result["created"] == 0
    assert result["updated"] == 1
    assert TennisPlayerWikidataReference.select().count() == 1
