"""Tests de `scripts/tennis/initial_tennis_backfill.py` -- 2026-08-11,
pedido explícito del usuario: script de control único para el scrapeo
inicial + poblado de la base de tenis. Mockea cada paso por separado
(no reimplementa/mocka los scrapers reales, esos ya tienen su propia
cobertura) -- estos tests solo verifican la ORQUESTACIÓN: orden,
fail-closed por paso, resumen final correcto."""

from __future__ import annotations

from unittest.mock import patch

from scripts.tennis.initial_tennis_backfill import run_initial_tennis_backfill


def _patches(**overrides):
    """Devuelve los 5 patch targets con defaults 'todo OK' salvo overrides."""
    defaults = {
        "scripts.tennis.build_tennis_player_wikidata_reference.build_reference": {"created": 5, "updated": 0},
        "scripts.tennis.wta_tournament_level_backfill.run_backfill": {"created": 10, "updated": 2},
        "scripts.tennis.wta_player_ranking_backfill.run_backfill": {"created": 20},
        "scripts.tennis.wta_top200_historical_backfill.run_country_catalog_backfill": {"players_processed": 3},
        "scripts.tennis.compute_self_elo.run_elo_backfill": {"total_processed": 100, "total_skipped": 0},
    }
    defaults.update(overrides)
    return defaults


def test_runs_all_steps_in_order_and_reports_all_ok():
    patches = _patches()
    with patch("scripts.tennis.build_tennis_player_wikidata_reference.build_reference", return_value=patches["scripts.tennis.build_tennis_player_wikidata_reference.build_reference"]) as p1, \
         patch("scripts.tennis.wta_tournament_level_backfill.run_backfill", return_value=patches["scripts.tennis.wta_tournament_level_backfill.run_backfill"]) as p2, \
         patch("scripts.tennis.wta_player_ranking_backfill.run_backfill", return_value=patches["scripts.tennis.wta_player_ranking_backfill.run_backfill"]) as p3, \
         patch("scripts.tennis.wta_top200_historical_backfill.run_country_catalog_backfill", return_value=patches["scripts.tennis.wta_top200_historical_backfill.run_country_catalog_backfill"]) as p4, \
         patch("scripts.tennis.compute_self_elo.run_elo_backfill", return_value=patches["scripts.tennis.compute_self_elo.run_elo_backfill"]) as p5, \
         patch("scripts.tennis.initial_tennis_backfill.init_db_historical"):
        summary = run_initial_tennis_backfill(atp_n=50, wta_n=50)

    assert summary["all_ok"] is True
    step_names = [s["step"] for s in summary["steps"]]
    assert step_names == [
        "wikidata_player_reference", "wta_tournament_levels", "wta_ranking_history",
        "match_history_country_catalog", "self_elo",
    ]
    p1.assert_called_once()
    p2.assert_called_once()
    p3.assert_called_once()
    p4.assert_called_once_with(max_players=None)
    p5.assert_called_once()


def test_one_step_failing_does_not_block_the_rest():
    with patch(
        "scripts.tennis.build_tennis_player_wikidata_reference.build_reference",
        side_effect=Exception("Wikidata caído"),
    ), \
         patch("scripts.tennis.wta_tournament_level_backfill.run_backfill", return_value={"ok": True}) as p2, \
         patch("scripts.tennis.wta_player_ranking_backfill.run_backfill", return_value={"ok": True}) as p3, \
         patch("scripts.tennis.wta_top200_historical_backfill.run_country_catalog_backfill", return_value={}) as p4, \
         patch("scripts.tennis.compute_self_elo.run_elo_backfill", return_value={}), \
         patch("scripts.tennis.initial_tennis_backfill.init_db_historical"):
        summary = run_initial_tennis_backfill(atp_n=10, wta_n=10)

    assert summary["all_ok"] is False
    wikidata_step = next(s for s in summary["steps"] if s["step"] == "wikidata_player_reference")
    assert wikidata_step["ok"] is False
    assert "Wikidata caído" in wikidata_step["error"]
    # los pasos siguientes SÍ corrieron pese al fallo del primero.
    p2.assert_called_once()
    p3.assert_called_once()
    p4.assert_called_once_with(max_players=None)


def test_optional_steps_can_be_skipped():
    with patch("scripts.tennis.build_tennis_player_wikidata_reference.build_reference") as p1, \
         patch("scripts.tennis.wta_tournament_level_backfill.run_backfill") as p2, \
         patch("scripts.tennis.wta_player_ranking_backfill.run_backfill") as p3, \
         patch("scripts.tennis.wta_top200_historical_backfill.run_country_catalog_backfill", return_value={}) as p4, \
         patch("scripts.tennis.compute_self_elo.run_elo_backfill") as p5, \
         patch("scripts.tennis.initial_tennis_backfill.init_db_historical"):
        summary = run_initial_tennis_backfill(
            atp_n=10, wta_n=10,
            include_wikidata=False, include_wta_tournament_levels=False, include_wta_rankings=False,
            include_self_elo=False,
        )

    step_names = [s["step"] for s in summary["steps"]]
    assert step_names == ["match_history_country_catalog"]
    p1.assert_not_called()
    p2.assert_not_called()
    p3.assert_not_called()
    p4.assert_called_once_with(max_players=None)
    p5.assert_not_called()


def test_match_history_catalog_failure_is_reported():
    with patch("scripts.tennis.build_tennis_player_wikidata_reference.build_reference", return_value={}), \
         patch("scripts.tennis.wta_tournament_level_backfill.run_backfill", return_value={}), \
         patch("scripts.tennis.wta_player_ranking_backfill.run_backfill", return_value={}), \
         patch("scripts.tennis.wta_top200_historical_backfill.run_country_catalog_backfill", side_effect=Exception("TennisExplorer caído")), \
         patch("scripts.tennis.initial_tennis_backfill.init_db_historical"):
        summary = run_initial_tennis_backfill(atp_n=10, wta_n=10)

    catalog_step = next(s for s in summary["steps"] if s["step"] == "match_history_country_catalog")
    assert catalog_step["ok"] is False
    assert summary["all_ok"] is False
