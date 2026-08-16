"""Tests del backfill del catálogo de categorías de torneo WTA
(2026-08-05, pedido explícito del usuario: "que solo tomen matches de
torneos 250 para arriba en nivel [también para WTA]"). Mockea red, usa
la DB histórica real (sqlite en memoria/test)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import Tournament
from scripts.tennis.wta_tournament_level_backfill import run_backfill, _priority


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        Tournament.delete().execute()
    yield
    with db_historical.connection_context():
        Tournament.delete().execute()


def test_priority_ranks_main_tour_above_itf():
    assert _priority("Grand Slam") > _priority("WTA 1000")
    assert _priority("WTA 250") > _priority("ITF")
    assert _priority("ITF") > _priority(None)


def test_run_backfill_inserts_new_rows():
    with patch(
        "scripts.tennis.wta_tournament_level_backfill.get_all_tournaments",
        return_value=[{"year": 2016, "tournament_name": "MADRID", "level": "Premier Mandatory"}],
    ):
        result = run_backfill()
    assert result == {"fetched": 1, "inserted": 1, "updated": 0}
    with db_historical.connection_context():
        row = Tournament.get(Tournament.tournament_name == "MADRID")
        assert row.level == "Premier Mandatory"


def test_run_backfill_never_downgrades_existing_higher_level():
    """2026-08-05, bug real encontrado en vivo -- la API oficial no es
    perfectamente estable entre requests paginados, y devolvió el mismo
    (year, tournament_name) con nivel distinto en corridas distintas
    (visto en producción: 'MADRID' Premier Mandatory en una corrida,
    'ITF' en otra). Nunca debe degradarse un torneo ya conocido como
    main-tour a un nivel inferior por una lectura posterior inestable."""
    with patch(
        "scripts.tennis.wta_tournament_level_backfill.get_all_tournaments",
        return_value=[{"year": 2016, "tournament_name": "MADRID", "level": "Premier Mandatory"}],
    ):
        run_backfill()

    with patch(
        "scripts.tennis.wta_tournament_level_backfill.get_all_tournaments",
        return_value=[{"year": 2016, "tournament_name": "MADRID", "level": "ITF"}],
    ):
        result = run_backfill()

    assert result == {"fetched": 1, "inserted": 0, "updated": 0}
    with db_historical.connection_context():
        row = Tournament.get(Tournament.tournament_name == "MADRID")
        assert row.level == "Premier Mandatory"


def test_run_backfill_upgrades_when_new_level_is_higher():
    with patch(
        "scripts.tennis.wta_tournament_level_backfill.get_all_tournaments",
        return_value=[{"year": 2016, "tournament_name": "MADRID", "level": "ITF"}],
    ):
        run_backfill()

    with patch(
        "scripts.tennis.wta_tournament_level_backfill.get_all_tournaments",
        return_value=[{"year": 2016, "tournament_name": "MADRID", "level": "Premier Mandatory"}],
    ):
        result = run_backfill()

    assert result == {"fetched": 1, "inserted": 0, "updated": 1}
    with db_historical.connection_context():
        row = Tournament.get(Tournament.tournament_name == "MADRID")
        assert row.level == "Premier Mandatory"
