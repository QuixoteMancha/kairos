"""Tests del registro completo de partidos escaneados (con o sin edge) +
ticks de probabilidad -- 2026-07-31, pedido explícito del usuario para
calibración futura del modelo de volatilidad."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.tennisscannedmatch import TennisScannedMatch
from database.historical.tennis.tennismatchprobabilitytick import TennisMatchProbabilityTick
from database.historical.tennis.matchtracking import (
    register_scanned_match, register_scanned_matches, get_open_scanned_matches,
    save_probability_tick, mark_match_settled,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisMatchProbabilityTick.delete().execute()
        TennisScannedMatch.delete().execute()
    yield
    with db_historical.connection_context():
        TennisMatchProbabilityTick.delete().execute()
        TennisScannedMatch.delete().execute()


def _fake_market(market_id="KXATPMATCH-TEST", **overrides):
    base = {
        "id": market_id, "ticker_a": f"{market_id}-A", "ticker_b": f"{market_id}-B",
        "tour": "ATP", "surface": "Hard", "player_a_name": "Player A", "player_b_name": "Player B",
        "player_a_rank": 10, "player_b_rank": 20, "best_of": 3, "close_time": "2026-08-01T00:00:00Z",
    }
    base.update(overrides)
    return base


def test_register_scanned_match_is_idempotent():
    register_scanned_match(_fake_market())
    register_scanned_match(_fake_market())  # segunda vez, mismo market_id
    assert TennisScannedMatch.select().count() == 1


def test_register_scanned_match_skips_incomplete_data():
    register_scanned_match(_fake_market(player_a_name=None))
    assert TennisScannedMatch.select().count() == 0


def test_register_scanned_matches_registers_regardless_of_edge():
    """El caso que motivó el pedido: NO filtra por edge/volatilidad, se
    registra CUALQUIER partido detectado."""
    register_scanned_matches([_fake_market("KXATPMATCH-A"), _fake_market("KXATPMATCH-B")])
    assert TennisScannedMatch.select().count() == 2


def test_get_open_scanned_matches_excludes_settled():
    register_scanned_match(_fake_market("KXATPMATCH-OPEN"))
    register_scanned_match(_fake_market("KXATPMATCH-SETTLED"))
    mark_match_settled("KXATPMATCH-SETTLED", "a")

    open_matches = get_open_scanned_matches()
    assert len(open_matches) == 1
    assert open_matches[0]["market_id"] == "KXATPMATCH-OPEN"


def test_save_probability_tick_requires_registered_match():
    save_probability_tick("NOT_REGISTERED", 0.5, 0.52)
    assert TennisMatchProbabilityTick.select().count() == 0


def test_save_probability_tick_persists_both_prices():
    register_scanned_match(_fake_market("KXATPMATCH-TICK"))
    save_probability_tick("KXATPMATCH-TICK", market_price_a=0.55, model_fair_a=0.58)

    tick = TennisMatchProbabilityTick.select().first()
    assert tick.market_price_a == 0.55
    assert tick.model_fair_a == 0.58
    assert tick.match.market_id == "KXATPMATCH-TICK"


def test_mark_match_settled_only_accepts_a_or_b():
    register_scanned_match(_fake_market("KXATPMATCH-BAD"))
    mark_match_settled("KXATPMATCH-BAD", "invalid")
    match = TennisScannedMatch.get(TennisScannedMatch.market_id == "KXATPMATCH-BAD")
    assert match.winner is None
    assert match.settled_at is None


def test_tick_worker_winner_logic_and_batching():
    from workers import tennis_match_tick_worker as worker

    register_scanned_match(_fake_market("KXATPMATCH-W1"))
    register_scanned_match(_fake_market("KXATPMATCH-W2"))

    fake_cache = {
        "KXATPMATCH-W1-A": {"current_a_price": None, "current_b_price": None, "raw": {"result": "yes"}},
        "KXATPMATCH-W1-B": {"current_a_price": None, "current_b_price": None, "raw": {"result": "no"}},
        "KXATPMATCH-W2-A": {"current_a_price": 0.61, "current_b_price": 0.42, "raw": {}},
        "KXATPMATCH-W2-B": {"current_a_price": 0.42, "current_b_price": 0.61, "raw": {}},
    }

    with patch("workers.tennis_match_tick_worker.KalshiAPI") as mock_api_cls, \
         patch.object(worker._fmodel, "calculate_fair_price", return_value={"fair_a_price": 0.6, "fair_b_price": 0.4}), \
         patch("workers.tennis_match_tick_worker.find_match_id", return_value=None) as mock_find_match_id, \
         patch("workers.tennis_match_tick_worker.check_and_save_set1_signal", return_value="skipped") as mock_set1:
        mock_api = mock_api_cls.return_value
        mock_api.get_markets_by_tickers.return_value = fake_cache
        result = worker.run_once()

    # el partido que resolvió (W1) dispara el intento de fetch de stats
    # (best-effort, ver _fetch_and_save_match_stats) -- acá no encuentra
    # match_id (mockeado a None) así que no guarda nada, pero confirma
    # que el settlement SIEMPRE intenta el fetch, sin tumbar el ciclo.
    mock_find_match_id.assert_called_once()

    # 2026-08-02, fusión de set1_favorite_reaction_worker: se llama para
    # AMBOS partidos abiertos (W1 y W2), sin importar si después
    # resuelven o no en este mismo ciclo.
    assert mock_set1.call_count == 2

    assert result["settled"] == 1
    assert result["ticked"] == 1
    assert result["errors"] == 0
    assert result["set1_processed"] == 0
    assert result["set1_errors"] == 0

    w1 = TennisScannedMatch.get(TennisScannedMatch.market_id == "KXATPMATCH-W1")
    assert w1.winner == "a"  # ticker_a resolvió 'yes' -> ganó player_a

    tick = TennisMatchProbabilityTick.select().first()
    assert tick.match.market_id == "KXATPMATCH-W2"
    assert tick.market_price_a == 0.61


def test_tick_worker_set1_failure_never_blocks_price_tick():
    """2026-08-02, pedido explícito del usuario: un fallo de
    TennisExplorer (scraping no oficial, sin SLA) NUNCA debe bloquear ni
    degradar el tick de precio real de Kalshi, que es más crítico."""
    from workers import tennis_match_tick_worker as worker

    register_scanned_match(_fake_market("KXATPMATCH-SF1"))

    fake_cache = {
        "KXATPMATCH-SF1-A": {"current_a_price": 0.5, "current_b_price": 0.5, "raw": {}},
        "KXATPMATCH-SF1-B": {"current_a_price": 0.5, "current_b_price": 0.5, "raw": {}},
    }

    with patch("workers.tennis_match_tick_worker.KalshiAPI") as mock_api_cls, \
         patch.object(worker._fmodel, "calculate_fair_price", return_value={"fair_a_price": 0.5, "fair_b_price": 0.5}), \
         patch(
             "workers.tennis_match_tick_worker.check_and_save_set1_signal",
             side_effect=Exception("TennisExplorer caído"),
         ):
        mock_api = mock_api_cls.return_value
        mock_api.get_markets_by_tickers.return_value = fake_cache
        result = worker.run_once()

    assert result["ticked"] == 1  # el tick de precio real se guardó igual
    assert result["errors"] == 0  # no cuenta como error del tick principal
    assert result["set1_processed"] == 0
    assert result["set1_errors"] == 1  # el fallo se contó por separado
