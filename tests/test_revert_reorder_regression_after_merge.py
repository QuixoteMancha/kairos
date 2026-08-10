"""Tests de `revert_reorder_regression_after_merge.py` -- reparación
puntual de la regresión real de 2026-08-10 (ver docstring del módulo)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.models import Player
from scripts.tennis.revert_reorder_regression_after_merge import (
    _CORRECT_NAMES, run,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        Player.delete().execute()
    yield
    with db_historical.connection_context():
        Player.delete().execute()


def _now():
    return datetime.now(timezone.utc)


def test_dry_run_reports_without_mutating():
    pid = next(iter(_CORRECT_NAMES))
    correct = _CORRECT_NAMES[pid]
    Player.create(id=pid, canonical_name="Wrong Order Name", created_at=_now())

    result = run(apply=False)

    reverted_ids = [r["player_id"] for r in result["reverted"]]
    assert pid in reverted_ids
    reloaded = Player.get_by_id(pid)
    assert reloaded.canonical_name == "Wrong Order Name"  # sin cambios


def test_apply_reverts_to_correct_name():
    pid = next(iter(_CORRECT_NAMES))
    correct = _CORRECT_NAMES[pid]
    Player.create(id=pid, canonical_name="Wrong Order Name", created_at=_now())

    run(apply=True)

    reloaded = Player.get_by_id(pid)
    assert reloaded.canonical_name == correct


def test_skips_missing_player():
    result = run(apply=True)

    skipped_ids = [r["player_id"] for r in result["skipped"]]
    assert set(_CORRECT_NAMES.keys()) <= set(skipped_ids)


def test_skips_already_correct_player():
    pid = next(iter(_CORRECT_NAMES))
    correct = _CORRECT_NAMES[pid]
    Player.create(id=pid, canonical_name=correct, created_at=_now())

    result = run(apply=True)

    assert result["reverted"] == []
    skipped_ids = [r["player_id"] for r in result["skipped"]]
    assert pid in skipped_ids


def test_gatica_id_excluded_from_correction_map():
    # 24621 (Barbara Gatica/Gatica Barbara) fue el ÚNICO caso donde el
    # apply regresivo en realidad CORRIGIÓ el nombre -- no debe estar
    # en el mapa de reversión.
    assert 24621 not in _CORRECT_NAMES
