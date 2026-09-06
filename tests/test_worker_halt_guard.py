"""Tests del guard de freno global en los workers que generan
recomendaciones/señales nuevas -- 2026-08-11, pedido explícito del
usuario: "si efectivamente se comprueba que no hay fondos, se deben
interrumpir todas las operaciones, para evitar errores por trabajar con
datos desactualizados". No cubre `kairos.py` (import chain rota por un
archivo pre-existente sin tocar, `strategies/bet_on_favourite.py`, fuera
de alcance -- ver memoria de sesión)."""

from __future__ import annotations

import pytest
from unittest.mock import patch

from database.db import init_db, db
from database.accounting.systemhaltstate import SystemHaltState, halt_operations


@pytest.fixture(autouse=True)
def clean_state():
    init_db()
    with db.connection_context():
        SystemHaltState.delete().execute()
    yield
    with db.connection_context():
        SystemHaltState.delete().execute()


def test_tennis_match_tick_worker_run_once_skips_when_halted():
    from workers.tennis_match_tick_worker import run_once

    halt_operations("brightdata_account_broken")
    with patch("workers.tennis_match_tick_worker.get_open_scanned_matches") as mock_get:
        result = run_once()

    mock_get.assert_not_called()
    assert result == {"halted": True, "reason": "brightdata_account_broken"}


