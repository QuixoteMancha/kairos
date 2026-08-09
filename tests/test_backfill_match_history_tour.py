"""Tests del backfill de `MatchHistory.tour` -- 2026-08-09, pedido
explícito del usuario."""

from __future__ import annotations

from scripts.tennis.backfill_match_history_tour import resolve_tour


def test_resolves_wta_when_either_player_in_wta_roster():
    wta = {"Aryna Sabalenka"}
    atp = set()
    assert resolve_tour("Aryna Sabalenka", "Amanda Anisimova", wta, atp) == "WTA"
    assert resolve_tour("Amanda Anisimova", "Aryna Sabalenka", wta, atp) == "WTA"


def test_resolves_atp_when_confirmed_in_tml_database_and_not_wta():
    wta = set()
    atp = {"Novak Djokovic", "Carlos Alcaraz"}
    assert resolve_tour("Novak Djokovic", "Carlos Alcaraz", wta, atp) == "ATP"


def test_wta_takes_priority_if_somehow_in_both_sets():
    # no debería pasar en la práctica real, pero si pasara, WTA gana
    # (es la señal más específica -- TmlDatabaseMatch es ATP-only, así
    # que un nombre real ahí NUNCA debería coincidir con el roster WTA).
    wta = {"Player X"}
    atp = {"Player X"}
    assert resolve_tour("Player X", "Player Y", wta, atp) == "WTA"


def test_fail_closed_when_neither_source_confirms():
    assert resolve_tour("Nobody Known", "Also Nobody", set(), set()) is None
