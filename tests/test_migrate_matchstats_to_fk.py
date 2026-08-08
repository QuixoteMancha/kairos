"""Tests de la migración FK de MatchStats (2026-08-08, pedido explícito
del usuario -- revisión del consolidado de modelos Copilot)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.match_models import Match, MatchStats
from scripts.tennis.migrate_matchstats_to_fk import run_migration


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        MatchStats.delete().execute()
        Match.delete().execute()
    yield
    with db_historical.connection_context():
        MatchStats.delete().execute()
        Match.delete().execute()


def _now():
    return datetime.now(timezone.utc)


def _real_match(market_id: str) -> Match:
    return Match.create(
        market_id=market_id, ticker_a="A", ticker_b="B", tour="ATP",
        player_a_name="X", player_b_name="Y", best_of=3, detected_at=_now(),
    )


def test_run_migration_links_matching_market_id():
    m = _real_match("MKT-1")
    MatchStats.create(market_id="MKT-1", flashscore_match_id="fs-1", raw_stats_json="{}", fetched_at=_now())

    summary = run_migration()
    assert summary["linked"] == 1
    assert summary["unmatched"] == 0
    row = MatchStats.get(MatchStats.market_id == "MKT-1")
    assert row.match_id == m.id


def test_run_migration_skips_already_linked_rows():
    m = _real_match("MKT-1")
    MatchStats.create(market_id="MKT-1", flashscore_match_id="fs-1", raw_stats_json="{}", fetched_at=_now(), match=m)

    summary = run_migration()
    assert summary["already_linked"] == 1
    assert summary["linked"] == 0


def test_run_migration_reports_unmatched_without_guessing():
    MatchStats.create(market_id="MKT-GHOST", flashscore_match_id="fs-1", raw_stats_json="{}", fetched_at=_now())

    summary = run_migration()
    assert summary["unmatched"] == 1
    assert "MKT-GHOST" in summary["unmatched_market_ids"]
    row = MatchStats.get(MatchStats.market_id == "MKT-GHOST")
    assert row.match_id is None


def test_run_migration_dry_run_does_not_write():
    m = _real_match("MKT-1")
    MatchStats.create(market_id="MKT-1", flashscore_match_id="fs-1", raw_stats_json="{}", fetched_at=_now())

    summary = run_migration(dry_run=True)
    assert summary["linked"] == 1  # se cuenta igual
    row = MatchStats.get(MatchStats.market_id == "MKT-1")
    assert row.match_id is None  # pero SIN escribir de verdad
