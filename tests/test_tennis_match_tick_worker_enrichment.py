"""Tests del hilo de enriquecimiento async (`run_enrichment_once`) de
`tennis_match_tick_worker.py` -- 2026-08-09, pedido explícito del
usuario: "como los matches se fetchean antes que empiecen desde Kalshi
puedes poner delays para los requests de tennis explorer de manera
asíncrona, por un lado el guardado de los ticks, por otro el fill del
match con la data de tennis explorer, pero todo en el mismo worker".
Separado del tick de precio real de Kalshi (`run_once`, no tocado
acá)."""

from __future__ import annotations

from unittest.mock import patch

from workers.tennis_match_tick_worker import run_enrichment_once


_OPEN_MATCHES = [
    {"market_id": "M1", "player_a_name": "Alexandrova Ekaterina", "player_b_name": "Svitolina Elina"},
    {"market_id": "M2", "player_a_name": "Djokovic Novak", "player_b_name": "Alcaraz Carlos"},
]


def test_enrichment_skips_players_already_resolved():
    with patch(
        "workers.tennis_match_tick_worker.get_open_scanned_matches", return_value=_OPEN_MATCHES,
    ), patch(
        "workers.tennis_match_tick_worker.has_real_match_detail_between",
        side_effect=lambda a, b: (a, b) == ("Alexandrova Ekaterina", "Svitolina Elina"),
    ), patch(
        "workers.tennis_match_tick_worker.save_next_match", return_value=True,
    ) as mock_save, patch(
        "workers.tennis_match_tick_worker.time.sleep",
    ):
        result = run_enrichment_once()

    mock_save.assert_called_once_with("Djokovic Novak", expected_opponent_hint="Alcaraz Carlos")
    assert result == {"open": 2, "pending": 1, "resolved": 1, "failed": 0}


def test_enrichment_counts_failures_without_raising():
    with patch(
        "workers.tennis_match_tick_worker.get_open_scanned_matches", return_value=_OPEN_MATCHES,
    ), patch(
        "workers.tennis_match_tick_worker.has_real_match_detail_between", return_value=False,
    ), patch(
        "workers.tennis_match_tick_worker.save_next_match", side_effect=[True, False],
    ), patch(
        "workers.tennis_match_tick_worker.time.sleep",
    ):
        result = run_enrichment_once()

    assert result == {"open": 2, "pending": 2, "resolved": 1, "failed": 1}


def test_enrichment_no_open_matches_is_noop():
    with patch(
        "workers.tennis_match_tick_worker.get_open_scanned_matches", return_value=[],
    ), patch(
        "workers.tennis_match_tick_worker.save_next_match",
    ) as mock_save:
        result = run_enrichment_once()
    mock_save.assert_not_called()
    assert result == {"open": 0, "pending": 0, "resolved": 0, "failed": 0}


def test_enrichment_never_raises_on_save_next_match_exception():
    with patch(
        "workers.tennis_match_tick_worker.get_open_scanned_matches", return_value=_OPEN_MATCHES[:1],
    ), patch(
        "workers.tennis_match_tick_worker.has_real_match_detail_between", return_value=False,
    ), patch(
        "workers.tennis_match_tick_worker.save_next_match", side_effect=Exception("red caída"),
    ):
        result = run_enrichment_once()
    assert result["failed"] == 1
