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
from unittest.mock import patch

from scripts.tennis.dedupe_triplicated_match_history import (
    run_dedup, prune_resolved_candidates_from_queue, dedupe_unvalidated_queue,
    _surname_guess, _canonical_key, _names_confirm_identity,
    _approx_identity_key, _is_legit_mixed_exhibition, find_tour_sex_mismatches, fix_tour_sex_mismatches,
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


def test_names_confirm_identity_requires_surname_and_given_name():
    assert _names_confirm_identity("Elina Svitolina", "Svitolina Elina") is True
    assert _names_confirm_identity("Alexandrova E.", "Alexandrova Ekaterina") is True
    assert _names_confirm_identity("Ekaterina Alexandrova", "Alexandrova E.") is True


def test_names_confirm_identity_rejects_same_surname_different_person():
    # 2026-08-10, pedido explícito del usuario ("verificación más
    # avanzada") -- mismo bug real que kairos-critical-misattribution-
    # bug-2026-08-06: "Casper Ruud" vs "Christian Ruud" (padre/hijo,
    # mismo apellido exacto, personas DISTINTAS). Solo apellido NO
    # alcanza para confirmar identidad.
    assert _names_confirm_identity("Casper Ruud", "Christian Ruud") is False
    assert _names_confirm_identity("Svitolina Elina", "Svitolina Anna") is False


def test_names_confirm_identity_fail_closed_on_single_token_name():
    assert _names_confirm_identity("Ruud", "Casper Ruud") is False
    assert _names_confirm_identity("", "Casper Ruud") is False


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


def test_dedupe_unvalidated_queue_merges_reciprocal_candidates():
    # el mismo partido real, sin resolver, visto desde la página de
    # CADA jugador -- 2 filas distintas en la cola.
    c1 = _candidate("Elina Svitolina", "Alexandrova E.", is_full_name=False)
    c2 = _candidate("Ekaterina Alexandrova", "Svitolina E.", is_full_name=False, match_date="2026-08-11")

    result = dedupe_unvalidated_queue()

    assert result["reciprocal_pairs_merged"] == 1
    assert result["rows_deleted"] == 1
    assert TennisMatchHistoryUnvalidated.select().count() == 1


def test_dedupe_unvalidated_queue_prefers_full_name_opponent_over_abbreviated():
    c1 = _candidate("Elina Svitolina", "Alexandrova E.", is_full_name=False)
    c2 = _candidate("Ekaterina Alexandrova", "Svitolina Elina", is_full_name=True)

    dedupe_unvalidated_queue()

    remaining = TennisMatchHistoryUnvalidated.get()
    assert remaining.player_b_is_full_name is True  # se quedó el candidato con más información


def test_dedupe_unvalidated_queue_leaves_unrelated_candidates_untouched():
    _candidate("Nobody Real", "Someone Else", is_full_name=False)
    _candidate("Other Person", "Another One", is_full_name=False, match_date="2026-08-12")

    result = dedupe_unvalidated_queue()

    assert result["reciprocal_pairs_merged"] == 0
    assert TennisMatchHistoryUnvalidated.select().count() == 2


def test_dedupe_unvalidated_queue_does_not_merge_same_surname_different_person():
    # verificación avanzada: NO debe fusionar por apellido solo.
    _candidate("Casper Ruud", "Some Rival R.", is_full_name=False)
    _candidate("Christian Ruud", "Some Other Rival", is_full_name=False)

    result = dedupe_unvalidated_queue()

    assert result["reciprocal_pairs_merged"] == 0
    assert TennisMatchHistoryUnvalidated.select().count() == 2


def test_approx_identity_key_groups_name_variants():
    # nota: la heurística "token más largo = apellido" puede fallar en
    # casos reales donde el nombre de pila es más largo que el apellido
    # (ej. "Sebastian" > "Baez") -- lo importante es que la clave sea
    # CONSISTENTE entre variantes del mismo nombre, no cuál mitad elige.
    key_full_1 = _approx_identity_key("Baez Sebastian")
    key_full_2 = _approx_identity_key("Sebastian Baez")
    assert key_full_1 == key_full_2 == "sebastian|b"


def test_approx_identity_key_fail_closed_single_token():
    assert _approx_identity_key("Baez") is None


def test_is_legit_mixed_exhibition_matches_known_patterns():
    assert _is_legit_mixed_exhibition("Battle of the Sexes") is True
    assert _is_legit_mixed_exhibition("Mixed Exhibition Match") is True
    assert _is_legit_mixed_exhibition("Toronto WTA") is False
    assert _is_legit_mixed_exhibition(None) is False


def test_find_tour_sex_mismatches_detects_real_contradiction():
    # 2026-08-10, bug real encontrado en producción: "Baez Sebastian vs
    # Brooksby Jenson" (ambos ATP reales) con una fila tour="ATP"
    # (Sackmann, fuente de verdad) y otra tour="WTA" (colisión real de
    # catálogo de torneo ambiguo, ej. "Rome" ATP+WTA la misma semana).
    _row("sackmann:1", "Jenson Brooksby", "Sebastian Baez", tour="ATP", tournament="Rome")
    _row("999", "Baez Sebastian", "Brooksby Jenson", tour="WTA", tournament="Rome")

    mismatches = find_tour_sex_mismatches()

    assert "sebastian|b" in mismatches
    assert len(mismatches["sebastian|b"]) == 2


def test_find_tour_sex_mismatches_no_contradiction_when_tour_consistent():
    _row("sackmann:1", "Jenson Brooksby", "Sebastian Baez", tour="ATP", tournament="Rome")
    _row("2", "Baez Sebastian", "Brooksby Jenson", tour="ATP", tournament="Rome")

    assert find_tour_sex_mismatches() == {}


def test_fix_tour_sex_mismatches_corrects_real_mismatch_via_confirmed_sex():
    _row("sackmann:1", "Jenson Brooksby", "Sebastian Baez", tour="ATP", tournament="Rome")
    row_wta = _row("999", "Baez Sebastian", "Brooksby Jenson", tour="WTA", tournament="Rome")

    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_tour_by_sex", return_value="ATP",
    ):
        result = fix_tour_sex_mismatches()

    assert result["rows_fixed"] == 1
    fixed = MatchHistory.get(MatchHistory.match_id == "999")
    assert fixed.tour == "ATP"


def test_fix_tour_sex_mismatches_leaves_legit_mixed_exhibition_untouched():
    # "un jugador se hombre y el otro femenino por un amistoso (Battle
    # of the Sexes), pero se debe confirmar el tournament" -- pedido
    # explícito del usuario.
    _row("sackmann:1", "Jenson Brooksby", "Sebastian Baez", tour="ATP", tournament="Rome")
    _row("999", "Baez Sebastian", "Brooksby Jenson", tour="WTA", tournament="Battle of the Sexes")

    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_tour_by_sex", return_value="ATP",
    ):
        result = fix_tour_sex_mismatches()

    assert result["rows_fixed"] == 0
    assert result["rows_skipped_legit_mixed_exhibition"] == 1
    untouched = MatchHistory.get(MatchHistory.match_id == "999")
    assert untouched.tour == "WTA"  # sin tocar, exhibición legítima


def test_fix_tour_sex_mismatches_fail_closed_when_sex_not_confirmed():
    _row("sackmann:1", "Jenson Brooksby", "Sebastian Baez", tour="ATP", tournament="Rome")
    _row("999", "Baez Sebastian", "Brooksby Jenson", tour="WTA", tournament="Rome")

    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_tour_by_sex", return_value=None,
    ), patch(
        "outer_apis.scrapers.tennis.wikidata_scraper.get_tennis_player_tour_by_gender", return_value=None,
    ):
        result = fix_tour_sex_mismatches()

    assert result["rows_fixed"] == 0
    assert result["groups_unresolved_no_sex_confirmed"] == 2  # 2 grupos, 1 por cada jugador contradictorio
    untouched = MatchHistory.get(MatchHistory.match_id == "999")
    assert untouched.tour == "WTA"  # sin confirmación real, no se toca


def test_fix_tour_sex_mismatches_falls_back_to_wikidata_when_tennisexplorer_fails():
    # 2026-08-10, pedido explícito del usuario: "verifiquemos todos los
    # que estén con data ambigua [contra Wikidata]... para no hitear
    # tanto tennisexplorer" -- si TennisExplorer no confirma, se intenta
    # Wikidata (fuente externa independiente) antes de rendirse.
    _row("sackmann:1", "Jenson Brooksby", "Sebastian Baez", tour="ATP", tournament="Rome")
    _row("999", "Baez Sebastian", "Brooksby Jenson", tour="WTA", tournament="Rome")

    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_tour_by_sex", return_value=None,
    ), patch(
        "outer_apis.scrapers.tennis.wikidata_scraper.get_tennis_player_tour_by_gender", return_value="ATP",
    ):
        result = fix_tour_sex_mismatches()

    assert result["rows_fixed"] == 1
    assert result["groups_confirmed_via_wikidata_fallback"] >= 1
    fixed = MatchHistory.get(MatchHistory.match_id == "999")
    assert fixed.tour == "ATP"


def test_fix_tour_sex_mismatches_can_disable_wikidata_fallback():
    _row("sackmann:1", "Jenson Brooksby", "Sebastian Baez", tour="ATP", tournament="Rome")
    _row("999", "Baez Sebastian", "Brooksby Jenson", tour="WTA", tournament="Rome")

    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_tour_by_sex", return_value=None,
    ), patch(
        "outer_apis.scrapers.tennis.wikidata_scraper.get_tennis_player_tour_by_gender",
    ) as mock_wikidata:
        result = fix_tour_sex_mismatches(use_wikidata_fallback=False)

    mock_wikidata.assert_not_called()
    assert result["rows_fixed"] == 0
