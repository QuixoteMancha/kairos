"""Tests de `save_match_detail` -- 2026-08-08, pedido explícito del
usuario: "si el match ya existe en las tablas, no hay que agregarlo
nuevamente, sólo actualizar si se verifica que es el mismo match y los
datos han cambiado" (idempotente por `match_id`, 1 fila por partido
real, NO 2 como `TennisMatchHistory`)."""

from __future__ import annotations

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import TennisMatchDetail, save_match_detail


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisMatchDetail.delete().execute()
    yield
    with db_historical.connection_context():
        TennisMatchDetail.delete().execute()


def _detail(**overrides) -> dict:
    base = {
        "match_id": "2692781",
        "match_date": "2024-08-10",
        "tournament": "Toronto WTA",
        "round": "quarterfinal",
        "surface": "hard",
        "score": "6-4, 6-2",
        "winner_side": "a",
        "player_a_name": "Anisimova Amanda",
        "player_a_slug": "anisimova-3be62",
        "player_a_ranking": 10,
        "player_a_birthdate": "2001-08-31",
        "player_a_height_cm": 180,
        "player_a_weight_kg": 59,
        "player_a_plays": "right",
        "player_b_name": "Sabalenka Aryna",
        "player_b_slug": "sabalenka",
        "player_b_ranking": 1,
        "player_b_birthdate": "1998-05-05",
        "player_b_height_cm": 182,
        "player_b_weight_kg": 80,
        "player_b_plays": "right",
    }
    base.update(overrides)
    return base


def test_creates_new_row_on_first_save():
    status = save_match_detail(_detail())
    assert status == "created"
    row = TennisMatchDetail.get(TennisMatchDetail.match_id == "2692781")
    assert row.player_a_name == "Anisimova Amanda"
    assert row.player_b_name == "Sabalenka Aryna"
    assert row.player_a_ranking == 10
    assert row.player_a_birthdate.isoformat() == "2001-08-31"
    assert row.updated_at is None


def test_does_not_duplicate_on_second_save_with_identical_data():
    save_match_detail(_detail())
    status = save_match_detail(_detail())
    assert status == "unchanged"
    assert TennisMatchDetail.select().where(TennisMatchDetail.match_id == "2692781").count() == 1


def test_updates_only_when_a_real_field_changed():
    save_match_detail(_detail())
    status = save_match_detail(_detail(player_a_ranking=8))  # ranking "corregido" por la fuente
    assert status == "updated"
    row = TennisMatchDetail.get(TennisMatchDetail.match_id == "2692781")
    assert row.player_a_ranking == 8
    assert row.updated_at is not None
    assert TennisMatchDetail.select().count() == 1  # sigue siendo 1 sola fila, no se duplicó


def test_invalid_without_match_id():
    assert save_match_detail(_detail(match_id=None)) == "invalid"
    assert TennisMatchDetail.select().count() == 0


def test_invalid_without_player_names():
    assert save_match_detail(_detail(player_a_name=None)) == "invalid"
    assert save_match_detail(_detail(player_b_name=None)) == "invalid"
    assert TennisMatchDetail.select().count() == 0


def test_tolerates_missing_optional_bio_fields():
    minimal = {
        "match_id": "42",
        "player_a_name": "Player A",
        "player_b_name": "Player B",
    }
    status = save_match_detail(minimal)
    assert status == "created"
    row = TennisMatchDetail.get(TennisMatchDetail.match_id == "42")
    assert row.player_a_ranking is None
    assert row.player_a_birthdate is None
