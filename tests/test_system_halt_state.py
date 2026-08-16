"""Tests de `database/accounting/systemhaltstate.py` -- circuit-breaker
global, 2026-08-11, pedido explícito del usuario: "si efectivamente se
comprueba que no hay fondos, se deben interrumpir todas las
operaciones, para evitar errores por trabajar con datos
desactualizados"."""

from __future__ import annotations

import pytest

from database.db import init_db, db
from database.accounting.systemhaltstate import (
    SystemHaltState, halt_operations, clear_halt, is_halted,
)


@pytest.fixture(autouse=True)
def clean_state():
    init_db()
    with db.connection_context():
        SystemHaltState.delete().execute()
    yield
    with db.connection_context():
        SystemHaltState.delete().execute()


def test_is_halted_defaults_to_false_before_any_row_exists():
    halted, reason = is_halted()
    assert halted is False
    assert reason is None


def test_halt_operations_sets_the_flag():
    halt_operations("brightdata_account_broken")
    halted, reason = is_halted()
    assert halted is True
    assert reason == "brightdata_account_broken"


def test_clear_halt_lifts_it_when_reason_matches():
    halt_operations("brightdata_account_broken")
    clear_halt("brightdata_account_broken")
    halted, reason = is_halted()
    assert halted is False


def test_clear_halt_does_not_lift_a_different_reason():
    halt_operations("brightdata_account_broken")
    clear_halt("some_other_reason")
    halted, reason = is_halted()
    assert halted is True
    assert reason == "brightdata_account_broken"


def test_clear_halt_is_noop_when_not_halted():
    clear_halt("brightdata_account_broken")  # no explota, no crea nada raro
    halted, _ = is_halted()
    assert halted is False


def test_halt_then_clear_then_halt_again_works():
    halt_operations("r1")
    clear_halt("r1")
    assert is_halted() == (False, None)
    halt_operations("r2")
    assert is_halted() == (True, "r2")
