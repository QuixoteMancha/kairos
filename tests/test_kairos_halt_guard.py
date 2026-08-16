"""Tests del guard de freno global en `kairos.py::_run_once` -- 2026-08-11,
pedido explícito del usuario: "si efectivamente se comprueba que no hay
fondos, se deben interrumpir todas las operaciones". Solo pudo escribirse
DESPUÉS de arreglar `strategies/bet_on_favourite.py` (SyntaxError real
que bloqueaba el import de `engine.py`/`kairos.py` por completo, ver
memoria de sesión) -- antes de ese fix, este guard solo se había
verificado por lectura de código."""

from __future__ import annotations

import pytest
from unittest.mock import patch

from database.db import init_db, db
from database.accounting.systemhaltstate import SystemHaltState, halt_operations
from kairos import _run_once


@pytest.fixture(autouse=True)
def clean_state():
    init_db()
    with db.connection_context():
        SystemHaltState.delete().execute()
    yield
    with db.connection_context():
        SystemHaltState.delete().execute()


def test_run_once_skips_scan_when_halted():
    halt_operations("brightdata_account_broken")
    with patch("kairos.Kairos.run_service") as mock_scan:
        result = _run_once()

    mock_scan.assert_not_called()
    assert result == {"halted": True, "reason": "brightdata_account_broken"}


def test_run_once_scans_normally_when_not_halted():
    with patch("kairos.Kairos.run_service", return_value={"ok": True}) as mock_scan:
        result = _run_once()

    mock_scan.assert_called_once()
    assert result == {"ok": True}
