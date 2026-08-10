"""Tests de `dedupe_triplicated_match_history.py` -- 2026-08-10, bug
real encontrado en vivo consultando el H2H/winrate de Svitolina vs
Alexandrova: cada partido resuelto vía match-detail quedaba triplicado
(1 fila real + 2 sintéticas "recentform:" con formato de nombre
distinto entre sí, nunca deduplicadas por el chequeo existente de
igualdad de string exacta)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.models import MatchHistory, TennisMatchHistoryUnvalidated
from scripts.tennis.dedupe_triplicated_match_history import (
    run_dedup, prune_resolved_candidates_from_queue, _surname_guess, _canonical_key,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        TennisMatchHistoryUnvalidated.delete().execute()
    yield
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        TennisMatchHistoryUnvalidated.delete().execute()


def _now():
    return datetime.now(timezone.utc)


def _row(match_id, player_a, player_b, **overrides):
    fields = dict(
        match_id=match_id, player_a_name=player_a, player_b_name=player_b,
        match_date="2026-08-11", source="tennisexplorer", fetched_at=_now(),
    )
    fields.update(overrides)
    return MatchHistory.create(**fields)


def test_surname_guess_picks_longest_token():
    assert _surname_guess("Alexandrova E.") == "alexandrova"
    assert _surname_guess("Elina Svitolina") == "svitolina"
    assert _surname_guess("Svitolina Elina") == "svitolina"


def test_canonical_key_ignores_side_order():
    a = _row("1", "Alexandrova Ekaterina", "Svitolina Elina")
    b = _row("2", "Elina Svitolina", "Alexandrova E.")
    assert _canonical_key(a) == _canonical_key(b)


def test_run_dedup_merges_real_row_with_synthetic_duplicates_and_deletes_them():
    real = _row(
        "3290769", "Alexandrova Ekaterina", "Svitolina Elina", tour="WTA",
        tournament="Toronto WTA", player_a_ranking=19,
    )
    synth1 = _row("recentform:elina svitolina|alexandrova e.|2026-08-11", "Elina Svitolina", "Alexandrova E.")
    synth2 = _row(
        "recentform:svitolina e.|ekaterina alexandrova|2026-08-11", "Ekaterina Alexandrova", "Svitolina E.",
        winner_side="a", score="6-4, 6-2",
    )

    result = run_dedup()

    assert result["rows_deleted"] == 2
    assert result["groups_deduped"] == 1
    assert MatchHistory.select().count() == 1
    winner = MatchHistory.get(MatchHistory.match_id == "3290769")
    # se consolidaron campos de las 2 sintéticas hacia la real
    assert winner.winner_side == "a"
    assert winner.score == "6-4, 6-2"
    assert winner.tour == "WTA"  # ya lo tenía, no se sobreescribió


def test_run_dedup_collapses_pure_synthetic_duplicates_when_no_real_row_yet():
    _row("recentform:aaa|2026-08-11", "Elina Svitolina", "Alexandrova E.", score=None)
    _row("recentform:bbb|2026-08-11", "Ekaterina Alexandrova", "Svitolina E.", winner_side="b", score="4-6, 2-6")

    result = run_dedup()

    assert result["rows_deleted"] == 1
    assert MatchHistory.select().count() == 1
    remaining = MatchHistory.get()
    assert remaining.match_id == "recentform:aaa|2026-08-11"  # lexicográficamente menor, determinístico
    assert remaining.winner_side == "b"  # consolidado desde la otra fila
    assert remaining.score == "4-6, 2-6"


def test_run_dedup_leaves_ambiguous_groups_with_2_real_match_ids_untouched():
    _row("111", "Alexandrova Ekaterina", "Svitolina Elina")
    _row("222", "Ekaterina Alexandrova", "Elina Svitolina")  # 2 match_id REALES distintos, mismo par+fecha

    result = run_dedup()

    assert result["groups_ambiguous_skipped"] == 1
    assert result["rows_deleted"] == 0
    assert MatchHistory.select().count() == 2


def test_run_dedup_no_op_when_no_duplicates():
    _row("1", "Player A", "Player B")
    _row("2", "Player C", "Player D", match_date="2026-08-12")

    result = run_dedup()

    assert result["rows_deleted"] == 0
    assert MatchHistory.select().count() == 2


def _candidate(player_a, opponent, is_full_name, match_date="2026-08-11"):
    return TennisMatchHistoryUnvalidated.create(
        match_date=match_date, tournament="Toronto WTA", player_a_name=player_a,
        player_b_name_or_abbrev=opponent, player_b_is_full_name=is_full_name,
        reason="no_sackmann_match", fetched_at=_now(),
    )


def test_prune_resolved_candidates_deletes_queue_entries_matching_real_row():
    _row("3290769", "Alexandrova Ekaterina", "Svitolina Elina")
    c = _candidate("Elina Svitolina", "Alexandrova E.", is_full_name=False)

    result = prune_resolved_candidates_from_queue()

    assert result["already_resolved_pruned"] == 1
    assert TennisMatchHistoryUnvalidated.select().where(TennisMatchHistoryUnvalidated.id == c.id).count() == 0


def test_prune_resolved_candidates_keeps_entries_matching_only_synthetic_row():
    # una fila sintética NO cuenta como "ya resuelto" -- todavía hace
    # falta el fetch real para conseguir identidad completa.
    _row("recentform:aaa|2026-08-11", "Elina Svitolina", "Alexandrova E.")
    c = _candidate("Elina Svitolina", "Alexandrova E.", is_full_name=False)

    result = prune_resolved_candidates_from_queue()

    assert result["already_resolved_pruned"] == 0
    assert TennisMatchHistoryUnvalidated.select().where(TennisMatchHistoryUnvalidated.id == c.id).count() == 1


def test_prune_resolved_candidates_keeps_entries_with_no_matching_row():
    c = _candidate("Nobody Real", "Someone Else", is_full_name=False)

    result = prune_resolved_candidates_from_queue()

    assert result["already_resolved_pruned"] == 0
    assert TennisMatchHistoryUnvalidated.select().where(TennisMatchHistoryUnvalidated.id == c.id).count() == 1
