"""Backfill masivo del historial real de partidos + cuotas históricas
del top-200 ATP actual, vía TennisExplorer -- 2026-08-03, pedido
explícito del usuario: "puedes sacar las cuotas históricas de los
matches de ATP?".

Wrapper delgado sobre `scripts/wta_top200_historical_backfill.py`
(generalizado ese mismo día para aceptar `tour` -- el scraper de
TennisExplorer no depende del tour, misma URL/estructura por
jugador/a) -- ver ese módulo para el detalle completo de rate limiting
e idempotencia."""

from __future__ import annotations

import sys

from scripts.tennis.wta_top200_historical_backfill import run_backfill

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    run_backfill(n_players=n, tour="ATP")
