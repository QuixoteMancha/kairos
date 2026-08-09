"""Tests del backtest WTA de la estrategia final (2026-08-05, pedido
explícito del usuario -- "calcular por separado los resultados de esta
estrategia tanto en ATP como WTA").

2026-08-09: migrado de `TennisMatchHistory` (1 fila por jugadora, con
`opponent_name` abreviado -- requería resolución de abreviación) a
`MatchHistory` (1 fila por partido real, con nombre completo en AMBOS
lados) -- ver nota de migración en `writer.py`. Los tests de
`_build_abbrev_to_full_name_index`/`_resolve_abbreviated_name` se
eliminaron junto con esas funciones (ya no existen, la resolución de
abreviación era exclusivamente para el esquema viejo)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from database.db import init_db_historical
from database.historical.tennis import MatchHistory, WtaTournamentLevel
from scripts.tennis.wta_favorite_surface_winrate_filter_backtest import (
    _rank_at, _name_token_key, _build_surface_winrate_tracker, collect_wta_cases, run_backtest,
    _MIN_RANK_GAP, _BEST_OF,
    _self_won_set1, _simulate_bet_after_set1_win,
    _normalize_tournament_name, _is_main_tour_level, _WTA_MAIN_TOUR_LEVELS,
    _self_won_set_n, _self_set1_game_diff, run_all_variants_backtest,
)

_next_match_id = iter(range(1, 10_000))


def _idx(plain: dict[str, list]) -> dict:
    """Convierte un ranking_index de test escrito con nombres crudos
    ({"Ana Ivanovic": [...]}) al esquema real keyed por `_name_token_key`
    (2026-08-09, bug real: WtaPlayerRankingHistory usa "Nombre Apellido",
    MatchHistory/TennisExplorer usa "Apellido Nombre" -- ver docstring de
    `_name_token_key`)."""
    return {_name_token_key(name): entries for name, entries in plain.items()}


def setup_function(_fn):
    init_db_historical()
    MatchHistory.delete().execute()
    WtaTournamentLevel.delete().execute()


def _real_row(player, opponent, date, result, surface="Hard", score="6-4, 6-3", tournament=None):
    MatchHistory.create(
        match_id=f"test:{next(_next_match_id)}",
        player_a_name=player, player_b_name=opponent, match_date=date,
        winner_side=("a" if result == "W" else "b"),
        score=score, source="tennisexplorer", surface=surface, tournament=tournament,
        fetched_at=datetime.now(timezone.utc),
    )


def test_best_of_is_always_3():
    assert _BEST_OF == 3


def test_min_rank_gap_is_30_for_wta():
    # 2026-08-05, pedido explícito del usuario: "para wta mejoremos la
    # condición a 30 puntos de diferencia de ranking" -- diverge a
    # propósito de ATP (20), ya no es el mismo umbral.
    assert _MIN_RANK_GAP == 30


def test_rank_at_finds_most_recent_entry_leq_date():
    index = _idx({"Iga Swiatek": [("2020-01-01", 50), ("2021-01-01", 10), ("2022-01-01", 1)]})
    assert _rank_at("Iga Swiatek", "2021-06-01", index) == 10
    assert _rank_at("Iga Swiatek", "2022-06-01", index) == 1


def test_rank_at_fail_closed_before_first_entry():
    index = _idx({"Iga Swiatek": [("2021-01-01", 10)]})
    assert _rank_at("Iga Swiatek", "2019-01-01", index) is None


def test_rank_at_fail_closed_unknown_player():
    assert _rank_at("Nobody", "2021-01-01", {}) is None


def test_collect_wta_cases_excludes_players_outside_roster():
    ranking_index = {"Ana Ivanovic": [("2015-01-01", 5)]}  # "Beatriz Zonta" NO está en el roster de ranking
    _real_row("Ana Ivanovic", "Beatriz Zonta", "2016-01-01", "W")
    with patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest._load_ranking_index",
        return_value=_idx(ranking_index),
    ), patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest._is_main_tour_level", return_value=True,
    ):
        cases, meta = collect_wta_cases()
    assert cases == []
    assert meta["skipped_not_wta_roster"] == 1


def test_name_token_key_matches_regardless_of_first_last_order():
    # 2026-08-09, bug real encontrado en vivo: WtaPlayerRankingHistory
    # (API oficial WTA) guarda "Nombre Apellido" ("Serena Williams"),
    # MatchHistory (TennisExplorer, TODAS las fuentes) guarda "Apellido
    # Nombre" ("Williams Serena") -- el backtest daba 0 casos porque
    # comparaba las strings crudas.
    assert _name_token_key("Serena Williams") == _name_token_key("Williams Serena")
    # apellidos compuestos, cualquier posición
    assert _name_token_key("Pablo Carreno Busta") == _name_token_key("Carreno Busta Pablo")
    assert _name_token_key("Serena Williams") != _name_token_key("Venus Williams")


def test_collect_wta_cases_matches_roster_despite_reversed_name_order():
    # Reproduce el bug real: fila de MatchHistory en convención
    # TennisExplorer ("Apellido Nombre"), roster en convención WTA
    # oficial ("Nombre Apellido") -- deben matchear igual.
    ranking_index = {"Ana Ivanovic": [("2015-01-01", 5)], "Petra Kvitova": [("2015-01-01", 300)]}
    _real_row("Ivanovic Ana", "Kvitova Petra", "2016-01-01", "W")  # orden invertido, como TennisExplorer real
    with patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest._load_ranking_index",
        return_value=_idx(ranking_index),
    ), patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest._is_main_tour_level", return_value=True,
    ):
        cases, meta = collect_wta_cases()
    assert len(cases) == 1
    assert meta["skipped_not_wta_roster"] == 0


def test_collect_wta_cases_single_row_per_match_no_duplicate():
    # 2026-08-09: `MatchHistory` ya es 1 fila por partido real -- a
    # diferencia del esquema viejo (`TennisMatchHistory`, 1 fila por
    # perspectiva de jugadora), no hace falta deduplicar 2 filas del
    # mismo partido acá.
    ranking_index = {"Ana Ivanovic": [("2015-01-01", 5)], "Petra Kvitova": [("2015-01-01", 100)]}
    _real_row("Ana Ivanovic", "Petra Kvitova", "2016-01-01", "W")
    with patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest._load_ranking_index",
        return_value=_idx(ranking_index),
    ), patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest._is_main_tour_level", return_value=True,
    ):
        cases, meta = collect_wta_cases()
    assert len(cases) == 1
    assert meta["skipped_duplicate"] == 0


def test_surface_winrate_tracker_is_point_in_time_correct():
    cases = [
        {"date": "2016-01-01", "favorite_name": "A", "underdog_name": "Z", "favorite_rank": 5,
         "underdog_rank": 300, "rank_gap": 295, "hit": True, "surface": "Hard"},
        {"date": "2016-02-01", "favorite_name": "Z", "underdog_name": "C", "favorite_rank": 300,
         "underdog_rank": 100, "rank_gap": -200, "hit": False, "surface": "Hard"},
        {"date": "2018-01-01", "favorite_name": "A", "underdog_name": "C", "favorite_rank": 5,
         "underdog_rank": 100, "rank_gap": 95, "hit": True, "surface": "Hard"},
    ]
    result, meta = _build_surface_winrate_tracker(cases)
    case_2018 = next(c for c in result if c["date"] == "2018-01-01")
    assert case_2018["favorite_surface_winrate"] == 1.0  # A: 1-0 en Hard antes de este partido


def test_run_backtest_returns_expected_shape():
    with patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest.collect_wta_cases", return_value=([], {}),
    ):
        result = run_backtest()
    assert result["filtered_cases_n"] == 0
    assert "baseline" in result and "filtered" in result


def test_self_won_set1_uses_self_first_convention():
    # "6-4, 4-6, 6-3" -- self (primer número de cada set) ganó el set 1 6-4
    assert _self_won_set1("6-4, 4-6, 6-3") is True
    assert _self_won_set1("4-6, 6-4, 6-3") is False


def test_self_won_set1_fail_closed():
    assert _self_won_set1(None) is None
    assert _self_won_set1("") is None
    assert _self_won_set1("W/O") is None
    assert _self_won_set1("6-6, 6-3") is None  # set 1 empatado, dato inconsistente


def test_self_won_set1_handles_flashscore_space_separated_format():
    # 2026-08-06, bug real encontrado en vivo (usuario preguntó por el
    # winrate de Svitolina tras ganar el set1) -- `MatchHistory` (antes
    # `TennisMatchHistory`) mezcla filas `source="tennisexplorer"`
    # (comas: "7-6, 6-4") y `source="flashscore"` (espacios: "7-6 6-4",
    # pipeline distinto y más viejo, sigue activo) -- ambas deben
    # parsear igual.
    assert _self_won_set1("7-6 6-4") is True
    assert _self_won_set1("5-7 2-6") is False
    assert _self_won_set_n("7-6 4-6 6-2", 2) is False
    assert _self_set1_game_diff("7-6 6-4") == 1


def test_collect_wta_cases_translates_self_won_set1_to_favorite_won_set1():
    ranking_index = {"Ana Ivanovic": [("2015-01-01", 5)], "Petra Kvitova": [("2015-01-01", 300)]}
    # Ivanovic es favorita (rank 5 < 300) y es player_a ("self", score
    # orientado a ella) en esta fila -- ganó el set1 (6-4 self-first)
    _real_row("Ana Ivanovic", "Petra Kvitova", "2016-01-01", "W", score="6-4, 6-3")
    with patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest._load_ranking_index",
        return_value=_idx(ranking_index),
    ), patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest._is_main_tour_level", return_value=True,
    ):
        cases, meta = collect_wta_cases()
    assert cases[0]["favorite_won_set1"] is True


def test_collect_wta_cases_favorite_is_opponent_flips_set1_result():
    ranking_index = {"Ana Ivanovic": [("2015-01-01", 5)], "Petra Kvitova": [("2015-01-01", 300)]}
    # acá Kvitova (player_b, peor rank) es "self" (score orientado a
    # player_a=Kvitova en esta fila) -- pero la FAVORITA es Ivanovic
    # (player_b en la fila, mejor rank). self (Kvitova) ganó el set1
    # 6-4 -- pero la favorita (Ivanovic) NO es self, así que
    # favorite_won_set1 = False.
    _real_row("Petra Kvitova", "Ana Ivanovic", "2016-01-01", "W", score="6-4, 6-3")
    with patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest._load_ranking_index",
        return_value=_idx(ranking_index),
    ), patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest._is_main_tour_level", return_value=True,
    ):
        cases, meta = collect_wta_cases()
    assert cases[0]["favorite_name"] == "Ana Ivanovic"
    assert cases[0]["favorite_won_set1"] is False


def test_normalize_tournament_name_strips_tour_suffix_and_edition_number():
    assert _normalize_tournament_name("Rome 2 WTA") == "ROME"
    assert _normalize_tournament_name("Madrid WTA") == "MADRID"
    assert _normalize_tournament_name("Indian Wells") == "INDIAN WELLS"
    assert _normalize_tournament_name("Calvi ITF") == "CALVI"


def test_normalize_tournament_name_applies_known_alias():
    # 2026-08-05, bug real -- TennisExplorer dice "French Open", la API
    # oficial WTA usa "ROLAND GARROS" para el mismo evento.
    assert _normalize_tournament_name("French Open") == "ROLAND GARROS"


def test_normalize_tournament_name_fail_closed_on_empty():
    assert _normalize_tournament_name(None) is None
    assert _normalize_tournament_name("WTA 2") is None  # solo sufijo+número, nada real queda


def test_load_tournament_level_index_never_downgrades_on_post_normalization_collision():
    # 2026-08-05, bug real -- "MADRID" (Premier Mandatory) y "MADRID 2
    # ITF" normalizan AMBOS a "MADRID" (se quitan dígitos/sufijos), pero
    # son filas reales distintas en la tabla (no colisionan ahí). El
    # índice point-in-time no debe dejar que la ITF pise al main-tour.
    from scripts.tennis.wta_favorite_surface_winrate_filter_backtest import _load_tournament_level_index
    WtaTournamentLevel.create(year=2016, tournament_name="MADRID", level="Premier Mandatory", fetched_at=datetime.now(timezone.utc))
    WtaTournamentLevel.create(year=2016, tournament_name="MADRID 2 ITF", level="ITF", fetched_at=datetime.now(timezone.utc))
    index = _load_tournament_level_index()
    assert index[(2016, "MADRID")] == "Premier Mandatory"


def test_is_main_tour_level_true_for_main_tour_levels():
    for level in _WTA_MAIN_TOUR_LEVELS:
        index = {(2016, "MADRID"): level}
        assert _is_main_tour_level("Madrid WTA", "2016-05-01", index) is True


def test_is_main_tour_level_false_for_below_250():
    # 2026-08-05, revertido a "250 en adelante" (el intento de incluir
    # 125 se descartó el mismo día, pedido explícito del usuario) --
    # WTA 125/125K vuelven a quedar excluidos, igual que ITF.
    for level in ("WTA 125", "125K", "ITF", None):
        index = {(2016, "MAKARSKA"): level}
        assert _is_main_tour_level("Makarska", "2016-06-01", index) is False


def test_is_main_tour_level_fail_closed_when_not_in_catalog():
    # el torneo no existe en el catálogo real de ese año -- nunca se
    # asume main-tour por default.
    assert _is_main_tour_level("Unknown Event", "2016-06-01", {}) is False


def test_collect_wta_cases_excludes_below_250_level_tournament():
    ranking_index = {"Ana Ivanovic": [("2015-01-01", 5)], "Petra Kvitova": [("2015-01-01", 300)]}
    _real_row("Ana Ivanovic", "Petra Kvitova", "2016-01-01", "W", tournament="Makarska")
    level_index = {(2016, "MAKARSKA"): "WTA 125"}
    with patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest._load_ranking_index",
        return_value=_idx(ranking_index),
    ), patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest._load_tournament_level_index",
        return_value=level_index,
    ):
        cases, meta = collect_wta_cases()
    assert cases == []
    assert meta["skipped_below_250_level"] == 1


def test_simulate_bet_after_set1_win_only_includes_qualifying_cases():
    cases = [
        {"date": "2016-01-01", "favorite_rank": 5, "underdog_rank": 100, "hit": True,
         "surface": "Hard", "favorite_won_set1": True},
        {"date": "2016-01-02", "favorite_rank": 5, "underdog_rank": 100, "hit": False,
         "surface": "Hard", "favorite_won_set1": False},
        {"date": "2016-01-03", "favorite_rank": 5, "underdog_rank": 100, "hit": True,
         "surface": "Hard", "favorite_won_set1": None},
    ]
    result = _simulate_bet_after_set1_win(cases, initial_capital=50.0, max_stake_usd=None)
    assert result["n_cases"] == 1  # solo el primero (favorite_won_set1 True)
    assert result["n_trades"] == 1


def test_run_backtest_includes_set1_variant():
    with patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest.collect_wta_cases", return_value=([], {}),
    ):
        result = run_backtest()
    assert "filtered_bet_only_after_set1_win" in result
    assert result["filtered_bet_only_after_set1_win"]["n_trades"] == 0


def test_self_won_set_n_generalizes_self_won_set1():
    assert _self_won_set_n("6-4, 4-6, 6-3", 1) is _self_won_set1("6-4, 4-6, 6-3")
    assert _self_won_set_n("6-4, 4-6, 6-3", 2) is False
    assert _self_won_set_n("6-4, 4-6, 6-3", 3) is True


def test_self_won_set_n_fail_closed_when_set_missing():
    assert _self_won_set_n("6-4", 2) is None
    assert _self_won_set_n(None, 1) is None


def test_self_set1_game_diff_real_values():
    assert _self_set1_game_diff("6-4, 4-6, 6-3") == 2
    assert _self_set1_game_diff("4-6, 6-4, 6-3") == -2


def test_self_set1_game_diff_fail_closed_on_empty():
    assert _self_set1_game_diff(None) is None
    assert _self_set1_game_diff("garbage") is None


def test_collect_wta_cases_includes_fields_needed_for_all_variants():
    # 2026-08-05, pedido explícito del usuario: "apliquemos las mismas
    # estrategias que aplicamos en atp para buscar la óptima" --
    # `_simulate_realistic_all_strategies` necesita set2_favorite_win/
    # set1_diff/best_of/favorite_age además de lo ya usado.
    ranking_index = {"Ana Ivanovic": [("2015-01-01", 5)], "Petra Kvitova": [("2015-01-01", 300)]}
    _real_row("Ana Ivanovic", "Petra Kvitova", "2016-01-01", "W", score="6-4, 4-6, 6-3", tournament="Miami")
    with patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest._load_ranking_index",
        return_value=_idx(ranking_index),
    ), patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest._is_main_tour_level", return_value=True,
    ):
        cases, meta = collect_wta_cases()
    assert cases[0]["best_of"] == _BEST_OF
    assert cases[0]["favorite_age"] is None
    assert cases[0]["set1_favorite_win"] is True  # Ivanovic (favorita, self) ganó el set1 6-4
    assert cases[0]["set2_favorite_win"] is False  # perdió el set2 4-6
    assert cases[0]["set1_diff"] == 2


def test_run_all_variants_backtest_reuses_atp_simulator_with_wta_tour():
    fake_cases = [
        {"date": "2016-01-01", "favorite_name": "A", "underdog_name": "Z", "favorite_rank": 5,
         "underdog_rank": 300, "rank_gap": 295, "hit": True, "surface": "Hard",
         "favorite_won_set1": True, "set1_favorite_win": True, "set2_favorite_win": True,
         "set1_diff": 2, "best_of": 3, "favorite_age": None},
    ]
    with patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest.collect_wta_cases",
        return_value=(fake_cases, {}),
    ), patch(
        "scripts.tennis.wta_favorite_surface_winrate_filter_backtest._build_surface_winrate_tracker",
        return_value=([{**fake_cases[0], "favorite_surface_winrate": 1.0, "underdog_surface_winrate": 0.0}], {}),
    ):
        result = run_all_variants_backtest()
    assert "strategies" in result
    assert "1_simple_bet_favorite" in result["strategies"]
    assert result["filtered_cases_n"] == 1
