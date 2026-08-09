"""Tests de la cola de prioridad + frescura de sync por jugador --
2026-08-09, pedido explícito del usuario: "el engine de scan sólo debe
usar la data de la base... debe haber una columna/campo de última
actualización... se podrá lanzar una orden al worker... indicarle
prioridad"."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import TennisPlayerSyncStatus
from database.historical.tennis.player_sync_status import (
    request_priority_sync, is_player_data_fresh, get_pending_priority_requests, mark_player_synced,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisPlayerSyncStatus.delete().execute()
    yield
    with db_historical.connection_context():
        TennisPlayerSyncStatus.delete().execute()


def test_never_synced_player_is_not_fresh():
    assert is_player_data_fresh("Novak Djokovic") is False


def test_request_priority_sync_creates_pending_request():
    request_priority_sync("Novak Djokovic")
    assert get_pending_priority_requests() == ["Novak Djokovic"]


def test_mark_player_synced_makes_data_fresh():
    request_priority_sync("Novak Djokovic")
    mark_player_synced("Novak Djokovic")
    assert is_player_data_fresh("Novak Djokovic") is True
    assert get_pending_priority_requests() == []


def test_data_synced_without_priority_request_is_still_fresh():
    # sync "de fondo" del worker (barrido normal, sin pedido explícito)
    mark_player_synced("Carlos Alcaraz")
    assert is_player_data_fresh("Carlos Alcaraz") is True


def test_stale_priority_request_after_sync_makes_it_not_fresh_again():
    request_priority_sync("Novak Djokovic")
    mark_player_synced("Novak Djokovic")
    assert is_player_data_fresh("Novak Djokovic") is True

    # el engine pide de nuevo (ej. reapareció en otro partido) -- hasta
    # que el worker no procese ESTE pedido nuevo, no está "fresco".
    request_priority_sync("Novak Djokovic")
    assert is_player_data_fresh("Novak Djokovic") is False
    assert get_pending_priority_requests() == ["Novak Djokovic"]


def test_data_older_than_max_age_is_not_fresh():
    mark_player_synced("Novak Djokovic")
    with db_historical.connection_context():
        row = TennisPlayerSyncStatus.get(TennisPlayerSyncStatus.player_name == "Novak Djokovic")
        row.match_history_updated_at = datetime.now(timezone.utc) - timedelta(hours=48)
        row.save()
    assert is_player_data_fresh("Novak Djokovic", max_age_hours=24.0) is False
    assert is_player_data_fresh("Novak Djokovic", max_age_hours=72.0) is True


def test_request_priority_sync_is_idempotent_while_pending():
    request_priority_sync("Novak Djokovic")
    with db_historical.connection_context():
        first = TennisPlayerSyncStatus.get(TennisPlayerSyncStatus.player_name == "Novak Djokovic")
        first_requested_at = first.priority_requested_at
    request_priority_sync("Novak Djokovic")  # ya pendiente, no debería duplicar la fila
    assert TennisPlayerSyncStatus.select().where(TennisPlayerSyncStatus.player_name == "Novak Djokovic").count() == 1


def test_get_pending_priority_requests_only_returns_unprocessed():
    request_priority_sync("Player A")
    request_priority_sync("Player B")
    mark_player_synced("Player A")
    assert get_pending_priority_requests() == ["Player B"]
