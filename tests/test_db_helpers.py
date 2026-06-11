#!/usr/bin/env python3
"""
Tests de los helpers ORM reconstruidos en src/database/database.py.

Estos helpers (get_or_create_player, get_or_create_tournament, save_match_stats,
save_h2h_record, save_recent_matches) se PERDIERON en el split de Grok y fueron
reconstruidos. Son críticos para el daily pipeline (orchestration/main.py) y los
scrapers, así que se prueban contra una SQLite en memoria.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from peewee import SqliteDatabase

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.database.database import (
    H2HRecord,
    Match,
    MatchStats,
    Player,
    RecentMatchHistory,
    Tournament,
    get_or_create_player,
    get_or_create_tournament,
    save_h2h_record,
    save_match_stats,
    save_recent_matches,
)

_MODELS = [Player, Tournament, Match, MatchStats, H2HRecord, RecentMatchHistory]


@pytest.fixture
def memdb():
    """Bindea los modelos a una SQLite en memoria durante el test."""
    test_db = SqliteDatabase(":memory:")
    with test_db.bind_ctx(_MODELS):
        test_db.create_tables(_MODELS)
        yield test_db
        test_db.drop_tables(_MODELS)


# ── get_or_create_player ──────────────────────────────────────────────────────

def test_get_or_create_player_creates(memdb):
    p = get_or_create_player(nombre="Carlos Alcaraz", ranking=2, elo_score=2100)
    assert p.id is not None
    assert p.nombre == "Carlos Alcaraz"
    assert p.ranking == 2


def test_get_or_create_player_is_idempotent(memdb):
    p1 = get_or_create_player(nombre="Jannik Sinner", ranking=1)
    p2 = get_or_create_player(nombre="Jannik Sinner", ranking=1)
    assert p1.id == p2.id
    assert Player.select().count() == 1


def test_get_or_create_player_updates_ranking(memdb):
    get_or_create_player(nombre="Iga Swiatek", ranking=3)
    updated = get_or_create_player(nombre="Iga Swiatek", ranking=1)
    assert updated.ranking == 1
    assert Player.select().count() == 1


# ── get_or_create_tournament ──────────────────────────────────────────────────

def test_get_or_create_tournament_parses_str_date(memdb):
    t = get_or_create_tournament(
        nombre="Roland Garros", superficie="Clay", ubicacion="Paris", fecha="2026-05-25"
    )
    assert t.id is not None
    assert t.superficie == "Clay"


def test_get_or_create_tournament_idempotent(memdb):
    t1 = get_or_create_tournament(nombre="US Open", fecha="2026-08-31")
    t2 = get_or_create_tournament(nombre="US Open", fecha="2026-08-31")
    assert t1.id == t2.id
    assert Tournament.select().count() == 1


# ── save_match_stats / save_h2h_record / save_recent_matches ──────────────────

def _make_match(memdb):
    p1 = get_or_create_player(nombre="Player One")
    p2 = get_or_create_player(nombre="Player Two")
    t = get_or_create_tournament(nombre="Test Cup", superficie="Hard", fecha="2026-06-01")
    match = Match.create(
        player1=p1, player2=p2, tournament=t,
        fecha="2026-06-01", superficie="Hard",
        scraper_url="p1|p2|2026-06-01",
    )
    return match, p1, p2


def test_save_match_stats_upsert(memdb):
    match, p1, _ = _make_match(memdb)
    save_match_stats(match=match, player=p1, stats_dict={"match_wins": 0.7, "set_1_win": 0.6})
    save_match_stats(match=match, player=p1, stats_dict={"match_wins": 0.8})
    rows = list(MatchStats.select().where(MatchStats.match == match, MatchStats.player == p1))
    assert len(rows) == 1
    assert rows[0].match_wins == 0.8  # actualizado, no duplicado


def test_save_match_stats_ignores_unknown_fields(memdb):
    match, p1, _ = _make_match(memdb)
    # campo inexistente no debe romper
    save_match_stats(match=match, player=p1, stats_dict={"campo_falso": 99, "set_2_win": 0.5})
    row = MatchStats.get(MatchStats.match == match, MatchStats.player == p1)
    assert row.set_2_win == 0.5


def test_save_h2h_record_upsert(memdb):
    _, p1, p2 = _make_match(memdb)
    save_h2h_record(p1, p2, "Hard", {"h2h_wins": 3, "h2h_sets_won": 7})
    save_h2h_record(p1, p2, "Hard", {"h2h_wins": 4})
    rows = list(H2HRecord.select().where(H2HRecord.player1 == p1, H2HRecord.player2 == p2))
    assert len(rows) == 1
    assert rows[0].h2h_wins == 4


def test_save_recent_matches(memdb):
    _, p1, _ = _make_match(memdb)
    n = save_recent_matches(p1, [
        {"fecha": "2026-05-20", "oponente": "Rival A", "score": "6-4 6-3",
         "superficie": "Hard", "resultado": "W"},
        {"fecha": "2026-05-22", "oponente": "Rival B", "score": "7-6 6-4",
         "superficie": "Hard", "resultado": "W"},
    ])
    assert n == 2
    assert RecentMatchHistory.select().where(RecentMatchHistory.player == p1).count() == 2


def test_save_recent_matches_skips_bad_rows(memdb):
    _, p1, _ = _make_match(memdb)
    n = save_recent_matches(p1, [
        {"oponente": "Sin fecha"},          # sin fecha → se salta
        "no es dict",                        # tipo inválido → se salta
        {"fecha": "2026-05-22", "oponente": "Rival B", "score": "7-6",
         "superficie": "Hard", "resultado": "W"},
    ])
    assert n == 1
