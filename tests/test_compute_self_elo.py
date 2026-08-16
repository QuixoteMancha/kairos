"""Tests de `scripts/tennis/compute_self_elo.py::run_elo_backfill` --
2026-08-11, pedido explícito del usuario ("sigamos con el Elo propio en
base al historial de matches"). Usa partidos reales sintéticos con
secuencia cronológica conocida para verificar que el Elo point-in-time
se calcula en el ORDEN correcto (no solo que el número final da bien)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import MatchHistory, Player
from models.volatility.sports.tennis.tennis_elo import DEFAULT_INITIAL_ELO, update_elo
from scripts.tennis.compute_self_elo import run_elo_backfill

_next_id = iter(range(1, 100_000))


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
    return Player.create(canonical_name=name, created_at=datetime.now(timezone.utc))


def _make_match(player_a, player_b, winner, match_date, tour="ATP"):
    MatchHistory.create(
        match_id=f"test:{next(_next_id)}",
        source="tennisexplorer", tour=tour, match_date=match_date,
        player_a=player_a, player_b=player_b, winner=winner,
        fetched_at=datetime.now(timezone.utc),
    )


def test_first_match_starts_at_default_elo():
    a, b = _make_player("Player A"), _make_player("Player B")
    _make_match(a, b, winner=a, match_date="2024-01-01")

    run_elo_backfill(tours=("ATP",))

    row = MatchHistory.get(MatchHistory.player_a == a)
    assert row.player_a_elo == DEFAULT_INITIAL_ELO
    assert row.player_b_elo == DEFAULT_INITIAL_ELO


def test_elo_carries_forward_chronologically():
    # A gana el 1ro (contra B), después juega un 2do partido (contra C)
    # -- el Elo de A en el 2do partido debe reflejar el resultado del
    # 1ro, NO arrancar de nuevo en 1500.
    a, b, c = _make_player("A"), _make_player("B"), _make_player("C")
    _make_match(a, b, winner=a, match_date="2024-01-01")
    _make_match(a, c, winner=c, match_date="2024-02-01")

    run_elo_backfill(tours=("ATP",))

    match1 = MatchHistory.get((MatchHistory.player_a == a) & (MatchHistory.player_b == b))
    match2 = MatchHistory.get((MatchHistory.player_a == a) & (MatchHistory.player_b == c))

    expected_a_elo_after_match1, _ = update_elo(DEFAULT_INITIAL_ELO, DEFAULT_INITIAL_ELO, a_won=True)
    assert match1.player_a_elo == DEFAULT_INITIAL_ELO  # antes del 1er partido, default
    assert match2.player_a_elo == expected_a_elo_after_match1  # ya refleja la victoria anterior


def test_processes_in_match_date_order_not_insertion_order():
    # se insertan en orden INVERSO al cronológico -- el backfill debe
    # procesarlos por match_date, no por orden de creación en DB.
    a, b, c = _make_player("A"), _make_player("B"), _make_player("C")
    _make_match(a, c, winner=c, match_date="2024-02-01")  # se inserta primero, pero es el 2do cronológico
    _make_match(a, b, winner=a, match_date="2024-01-01")  # se inserta segundo, pero es el 1ro cronológico

    run_elo_backfill(tours=("ATP",))

    match_jan = MatchHistory.get(MatchHistory.match_date == "2024-01-01")
    match_feb = MatchHistory.get(MatchHistory.match_date == "2024-02-01")
    assert match_jan.player_a_elo == DEFAULT_INITIAL_ELO
    # el de febrero (cronológicamente 2do) ya debe reflejar el resultado de enero.
    expected_a_elo_after_jan, _ = update_elo(DEFAULT_INITIAL_ELO, DEFAULT_INITIAL_ELO, a_won=True)
    assert match_feb.player_a_elo == expected_a_elo_after_jan


def test_atp_and_wta_elo_are_computed_independently():
    a, b = _make_player("A"), _make_player("B")
    _make_match(a, b, winner=a, match_date="2024-01-01", tour="ATP")
    c, d = _make_player("C"), _make_player("D")
    _make_match(c, d, winner=d, match_date="2024-01-01", tour="WTA")

    summary = run_elo_backfill(tours=("ATP", "WTA"))

    assert summary["ATP"]["processed"] == 1
    assert summary["WTA"]["processed"] == 1
    assert summary["total_processed"] == 2


def test_match_without_winner_is_excluded_not_fatal():
    # sin winner, la query de `run_elo_backfill` ni siquiera trae la
    # fila (excluida a nivel SQL, ver `MatchHistory.winner.is_null(False)`)
    # -- no cuenta como "processed" ni rompe el resto del cálculo.
    a, b = _make_player("A"), _make_player("B")
    _make_match(a, b, winner=None, match_date="2024-01-01")

    summary = run_elo_backfill(tours=("ATP",))

    assert summary["ATP"]["processed"] == 0


def test_match_without_parseable_date_is_skipped():
    a, b = _make_player("A"), _make_player("B")
    _make_match(a, b, winner=a, match_date=None)

    summary = run_elo_backfill(tours=("ATP",))

    assert summary["ATP"]["skipped"] == 1


def test_first_ever_match_gives_rank_1_for_both_tied():
    # 2 jugadores nuevos, mismo Elo default -- ambos empatan en 1er lugar.
    a, b = _make_player("A"), _make_player("B")
    _make_match(a, b, winner=a, match_date="2024-01-01")

    run_elo_backfill(tours=("ATP",))

    row = MatchHistory.get(MatchHistory.player_a == a)
    assert row.player_a_elo_rank == 1
    assert row.player_b_elo_rank == 1  # empate real, misma posición


def test_rank_reflects_real_elo_standing_among_3_players():
    # A y B juegan primero (A gana, sube). C aparece después contra A
    # -- en ese momento el pool tiene 3 jugadores reales: A (subió),
    # B (bajó), C (recién entra al default). El ranking de C debe
    # reflejar su posición REAL contra A y B, no solo contra A.
    a, b, c = _make_player("A"), _make_player("B"), _make_player("C")
    _make_match(a, b, winner=a, match_date="2024-01-01")
    _make_match(a, c, winner=a, match_date="2024-02-01")

    run_elo_backfill(tours=("ATP",))

    match2 = MatchHistory.get((MatchHistory.player_a == a) & (MatchHistory.player_b == c))
    # A ya le ganó a B antes -- su Elo subió, debería ser #1 del pool
    # de 3 (A, B, C) en ese momento.
    assert match2.player_a_elo_rank == 1
    # C recién entra con el Elo default (1500) -- empata con B (que
    # bajó de 1500 tras perder contra A, así que B < C en Elo) -- C
    # debería estar por delante de B (rank 2), y A por delante de
    # ambos (rank 1).
    assert match2.player_b_elo_rank == 2


def test_rank_1_always_matches_the_highest_elo_in_the_pool():
    # invariante real: en cualquier partido, el jugador con Elo MÁS ALTO
    # de los 2 debe tener rank <= el del otro (nunca al revés).
    a, b, c = _make_player("A"), _make_player("B"), _make_player("C")
    _make_match(a, b, winner=a, match_date="2024-01-01")
    _make_match(a, c, winner=a, match_date="2024-01-02")
    _make_match(b, c, winner=b, match_date="2024-01-03")

    run_elo_backfill(tours=("ATP",))

    for row in MatchHistory.select():
        if row.player_a_elo > row.player_b_elo:
            assert row.player_a_elo_rank <= row.player_b_elo_rank
        elif row.player_b_elo > row.player_a_elo:
            assert row.player_b_elo_rank <= row.player_a_elo_rank
        else:
            assert row.player_a_elo_rank == row.player_b_elo_rank  # empate real


def test_rerun_is_deterministic():
    a, b = _make_player("A"), _make_player("B")
    _make_match(a, b, winner=a, match_date="2024-01-01")
    _make_match(a, b, winner=b, match_date="2024-03-01")

    run_elo_backfill(tours=("ATP",))
    row1 = MatchHistory.get(MatchHistory.match_date == "2024-03-01")
    elo_first_run = row1.player_a_elo

    run_elo_backfill(tours=("ATP",))
    row2 = MatchHistory.get(MatchHistory.match_date == "2024-03-01")
    assert row2.player_a_elo == elo_first_run
