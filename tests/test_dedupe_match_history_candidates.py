"""Tests de `find_match_candidates` -- 2026-08-08, pedido explícito del
usuario: dedup de `TennisMatchHistory` (2 filas/partido) SIN red, como
filtro de eficiencia previo al backfill de `MatchHistory`."""

from __future__ import annotations

from scripts.tennis.dedupe_match_history_candidates import find_match_candidates


def _row(player, opponent, date="2024-08-10", tournament="Toronto WTA", **kw):
    return {"player_name": player, "opponent_name": opponent, "match_date": date, "tournament": tournament, **kw}


def test_pairs_reciprocal_rows_into_one_candidate():
    # `player_name` en `TennisMatchHistory` siempre viene "Nombre
    # Apellido" (confirmado contra datos reales de producción, ej.
    # "Aryna Sabalenka", "Ayla Aksu" -- NO el orden "Apellido Nombre"
    # que usa la página match-detail).
    rows = [
        _row("Amanda Anisimova", "Sabalenka A."),
        _row("Aryna Sabalenka", "Anisimova A."),
    ]
    result = find_match_candidates(rows)
    assert len(result["paired"]) == 1
    assert result["singleton"] == []
    assert result["conflict"] == []
    pair = result["paired"][0]
    assert {pair["player_a"], pair["player_b"]} == {"Amanda Anisimova", "Aryna Sabalenka"}


def test_handles_compound_surnames_correctly():
    rows = [
        _row("Adrian Menendez-Maceiras", "Doe J."),
        _row("John Doe", "Menendez-Maceiras A."),
    ]
    result = find_match_candidates(rows)
    assert len(result["paired"]) == 1


def test_known_limitation_compound_given_names_fall_back_to_singleton():
    # LIMITACIÓN DOCUMENTADA de `expected_opponent_abbreviation`: nombre
    # de pila compuesto ("Alan Fernando" es el nombre completo real de
    # pila, no solo "Alan") rompe la regla simple -- la real abreviación
    # de TennisExplorer es "Rubio Fierros A.", pero la regla calcula
    # "Fernando Rubio Fierros A." -- no reciproca, cae a singleton en
    # vez de emparejar mal. Comportamiento esperado y seguro (fail-closed),
    # no un bug a arreglar acá.
    rows = [
        _row("Alan Fernando Rubio Fierros", "Doe J."),
        _row("John Doe", "Rubio Fierros A."),
    ]
    result = find_match_candidates(rows)
    assert result["paired"] == []
    assert len(result["singleton"]) == 2


def test_singleton_when_opponent_never_backfilled():
    rows = [_row("Ayla Aksu", "Costoulas S.")]
    result = find_match_candidates(rows)
    assert len(result["singleton"]) == 1
    assert result["singleton"][0]["player_b_abbrev"] == "Costoulas S."
    assert result["paired"] == []


def test_does_not_pair_across_different_dates():
    rows = [
        _row("Amanda Anisimova", "Sabalenka A.", date="2024-08-10"),
        _row("Aryna Sabalenka", "Anisimova A.", date="2024-08-11"),  # otro día -- NO es el mismo partido
    ]
    result = find_match_candidates(rows)
    assert result["paired"] == []
    assert len(result["singleton"]) == 2


def test_does_not_pair_across_different_tournaments():
    rows = [
        _row("Amanda Anisimova", "Sabalenka A.", tournament="Toronto WTA"),
        _row("Aryna Sabalenka", "Anisimova A.", tournament="Cincinnati WTA"),
    ]
    result = find_match_candidates(rows)
    assert result["paired"] == []
    assert len(result["singleton"]) == 2


def test_requires_reciprocity_in_both_directions():
    # A dice que jugó contra "Sabalenka A.", pero la fila de Sabalenka
    # dice que jugó contra otra persona ("Otra X.") -- NO reciproco,
    # nunca se empareja a ciegas.
    rows = [
        _row("Amanda Anisimova", "Sabalenka A."),
        _row("Aryna Sabalenka", "Otra X."),
    ]
    result = find_match_candidates(rows)
    assert result["paired"] == []
    assert len(result["singleton"]) == 2


def test_fail_closed_on_ambiguous_multiple_candidates():
    # 2 personas distintas con la MISMA abreviación reciproca en el
    # mismo bucket -- caso raro pero real posible (torneos por equipos
    # con múltiples partidos el mismo día). Nunca se adivina cuál es.
    rows = [
        _row("Amanda Anisimova", "Smith J."),
        _row("John Smith", "Anisimova A."),
        _row("Jane Smith", "Anisimova A."),
    ]
    result = find_match_candidates(rows)
    assert len(result["conflict"]) >= 1


def test_does_not_double_count_consumed_rows():
    rows = [
        _row("Amanda Anisimova", "Sabalenka A."),
        _row("Aryna Sabalenka", "Anisimova A."),
    ]
    result = find_match_candidates(rows)
    total = len(result["paired"]) * 2 + len(result["singleton"]) + len(result["conflict"])
    assert total == len(rows)


def test_single_word_names_never_pair_but_dont_crash():
    rows = [_row("Cristian", "Doe J.")]  # nombre de una sola palabra, caso real visto en producción
    result = find_match_candidates(rows)
    assert len(result["singleton"]) == 1
