"""Tests del worker persistente de backfill de catálogo -- 2026-08-16,
pedido explícito del usuario ("un worker que tenga threads para
backfillear los players que corra constantemente"). Solo verifica el
wiring (env vars -> parámetros reales pasados a
`run_country_catalog_backfill`); el comportamiento del backfill en sí
ya está cubierto por `test_wta_top200_historical_backfill.py`."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _reload_worker_module():
    """Recarga el módulo del worker entre tests -- lee sus constantes
    de env var a nivel de módulo (mismo patrón que los demás workers de
    `workers/`), así que cada test necesita su propio import fresco."""
    import importlib
    import workers.tennis_player_catalog_backfill_worker as mod
    importlib.reload(mod)
    yield mod


def test_run_once_uses_default_max_workers(_reload_worker_module, monkeypatch):
    monkeypatch.delenv("TENNIS_CATALOG_BACKFILL_MAX_WORKERS", raising=False)
    monkeypatch.delenv("TENNIS_CATALOG_BACKFILL_MAX_PLAYERS", raising=False)
    import importlib
    import workers.tennis_player_catalog_backfill_worker as mod
    importlib.reload(mod)

    with patch.object(mod, "run_country_catalog_backfill", return_value={"players_processed": 0}) as fn:
        mod._run_once()

    fn.assert_called_once_with(max_players=None, max_workers=4)


def test_run_once_respects_env_overrides(monkeypatch):
    monkeypatch.setenv("TENNIS_CATALOG_BACKFILL_MAX_WORKERS", "8")
    monkeypatch.setenv("TENNIS_CATALOG_BACKFILL_MAX_PLAYERS", "50")
    import importlib
    import workers.tennis_player_catalog_backfill_worker as mod
    importlib.reload(mod)

    with patch.object(mod, "run_country_catalog_backfill", return_value={"players_processed": 50}) as fn:
        mod._run_once()

    fn.assert_called_once_with(max_players=50, max_workers=8)
