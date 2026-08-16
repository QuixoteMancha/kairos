"""Tests de `has_real_match_detail_between` -- 2026-08-09, pedido
explícito del usuario: separar el fill de identidad/tour/level vía
TennisExplorer (async, con delay real) del guardado de ticks de precio
de Kalshi (síncrono) en `tennis_match_tick_worker.py` -- este chequeo
sin red es la parte barata que decide si vale la pena pagar el costo de
red de `save_next_match` en cada ciclo del hilo de enriquecimiento."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import MatchHistory, Player, has_real_match_detail_between


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


def _make_player(name):
    existing = Player.get_or_none(Player.canonical_name == name)
    if existing is not None:
        return existing
    return Player.create(canonical_name=name, created_at=datetime.now(timezone.utc))


def _row(match_id, player_a, player_b):
    return MatchHistory.create(
        match_id=match_id, player_a=_make_player(player_a), player_b=_make_player(player_b),
        match_date="2026-08-11", winner=None, source="tennisexplorer", fetched_at=datetime.now(timezone.utc),
    )


def test_false_when_no_row_exists():
    assert has_real_match_detail_between("Ekaterina Alexandrova", "Elina Svitolina") is False


def test_true_when_real_match_id_row_exists_either_order():
    _row("3290769", "Alexandrova Ekaterina", "Svitolina Elina")
    assert has_real_match_detail_between("Alexandrova Ekaterina", "Svitolina Elina") is True
    assert has_real_match_detail_between("Svitolina Elina", "Alexandrova Ekaterina") is True


def test_false_when_only_synthetic_recentform_row_exists():
    # fila creada por el fallback abreviado de save_match_history --
    # NO cuenta como "ya resuelto" para el hilo de enriquecimiento.
    _row("recentform:alexandrova|svitolina|2026-08-11", "Alexandrova Ekaterina", "Svitolina E.")
    assert has_real_match_detail_between("Alexandrova Ekaterina", "Svitolina E.") is False
