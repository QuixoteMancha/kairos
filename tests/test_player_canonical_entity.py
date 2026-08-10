"""Tests de la entidad canónica `Player` (Fase 1 de la migración,
2026-08-10, pedido explícito del usuario: "los datos de player deben
ir en el modelo de player, y simplemente referenciar a los players en
los matches"). `_resolve_player_id` (writer.py) es el punto de
resolución-o-creación usado por `save_match_detail` -- estos tests
verifican el resolver en aislamiento; `test_save_match_detail.py`
verifica el wiring end-to-end dentro de `save_match_detail`."""

from __future__ import annotations

from datetime import date

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import MatchHistory, Player
from database.historical.tennis.writer import _resolve_player_id


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


def test_resolve_player_id_creates_new_player():
    pid = _resolve_player_id("Alexandrova Ekaterina", tour="WTA", slug="alexandrova-3181a")
    assert pid is not None
    player = Player.get_by_id(pid)
    assert player.canonical_name == "Alexandrova Ekaterina"
    assert player.tour == "WTA"
    assert player.slug == "alexandrova-3181a"


def test_resolve_player_id_reuses_existing_player_exact_name():
    first = _resolve_player_id("Alexandrova Ekaterina", tour="WTA")
    second = _resolve_player_id("Alexandrova Ekaterina", tour="WTA")
    assert first == second
    assert Player.select().count() == 1


def test_resolve_player_id_reuses_existing_player_different_name_format():
    # mismo jugador real, formato de nombre distinto -- "Elina
    # Svitolina" vs "Svitolina Elina" -- no debe crear un Player
    # duplicado (mismo criterio _names_confirm_identity que ya usa
    # _find_existing_match_history_row).
    first = _resolve_player_id("Elina Svitolina")
    second = _resolve_player_id("Svitolina Elina")
    assert first == second
    assert Player.select().count() == 1


def test_resolve_player_id_does_not_merge_same_surname_different_person():
    # "Casper Ruud" vs "Christian Ruud" -- mismo apellido, personas
    # DISTINTAS -- deben quedar como 2 Players separados.
    casper = _resolve_player_id("Casper Ruud")
    christian = _resolve_player_id("Christian Ruud")
    assert casper != christian
    assert Player.select().count() == 2


def test_resolve_player_id_enriches_null_fields_progressively():
    first = _resolve_player_id("Alexandrova Ekaterina", tour="WTA")
    # segunda llamada trae birthdate/height que la primera no tenía --
    # debe completarse (enriquecimiento progresivo), no crear otro Player.
    second = _resolve_player_id(
        "Alexandrova Ekaterina", tour="WTA", birthdate=date(1994, 11, 15), height_cm=180,
    )
    assert first == second
    player = Player.get_by_id(first)
    assert player.birthdate == date(1994, 11, 15)
    assert player.height_cm == 180


def test_resolve_player_id_never_overwrites_confirmed_field_with_different_value():
    first = _resolve_player_id("Alexandrova Ekaterina", height_cm=180)
    _resolve_player_id("Alexandrova Ekaterina", height_cm=999)  # valor distinto, no debe pisar
    player = Player.get_by_id(first)
    assert player.height_cm == 180


def test_resolve_player_id_fail_closed_on_empty_name():
    assert _resolve_player_id("") is None
    assert _resolve_player_id("   ") is None
    assert Player.select().count() == 0
