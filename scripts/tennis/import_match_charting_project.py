"""
Import completo del Match Charting Project de Jeff Sackmann (2026-08-02,
pedido explícito del usuario: "usemos las fuentes de sackmann el de
charting e importemos todos los matches. Con estadísticas si es que los
csv las tienen").

ALCANCE: últimos 10 años (`_SINCE_DATE`), ambos tours (ATP/WTA). Por
cada partido:
  1. Ganador real derivado del último punto jugado
     (`match_charting_scraper.derive_match_winners` -- NO de "Player 1"/
     "Player 2", que no indica ganador, ver docstring de
     `get_matches_index`).
  2. Estadísticas reales por set y totales (`get_overview_stats`).
  3. Si el partido YA existe en `TennisScannedMatch` (Kalshi lo vio en
     vivo -- caso raro dado que Kalshi solo retiene ~2 meses vs. estos
     10 años), se ENRIQUECE esa fila en vez de duplicar (pedido
     explícito del usuario: "revisar si es posible complementar esta
     data con la que ya está en la otra tabla"). Si no, se crea un
     `ChartedMatch` nuevo.

CAVEAT DE SESGO DE SELECCIÓN (documentado, no oculto, ver
`database/historical/tennis/chartedmatch.py`): el Match Charting
Project NO es un registro exhaustivo de todos los partidos del tour --
son los que un voluntario decidió cargar, probablemente sesgados hacia
partidos reñidos/famosos. Cualquier estadística agregada calculada sobre
esta data (ej. tasa de acierto del favorito) hereda ese sesgo.

CLIMA: NO incluido en este script -- requiere backfill separado (ver
pendiente documentado en memoria), ya que `populate_weather_for_match`
está diseñado para `TennisScannedMatch` (necesita al menos un tick real
de `TennisMatchProbabilityTick`), que los `ChartedMatch` nuevos no
tienen. Los partidos que SÍ se enriquecen sobre un `TennisScannedMatch`
existente ya tienen su clima poblado por el backfill anterior."""

from __future__ import annotations

import json

from database.db import init_db_historical
from database.historical.tennis.chartedmatchimport import import_or_merge_match
from outer_apis.scrapers.tennis.match_charting_scraper import (
    get_matches_index, get_overview_stats, derive_match_winners,
)

_SINCE_DATE = "2016-01-01"  # ~10 años atrás, pedido explícito del usuario
_TOUR_LABEL = {"m": "ATP", "w": "WTA"}


def run_import(since_date: str = _SINCE_DATE) -> dict:
    init_db_historical()

    summary = {
        "created_new": 0, "merged_existing": 0, "created_stats_for_existing": 0,
        "errors": 0, "no_winner_derivable": 0, "no_stats": 0, "total_matches": 0,
    }

    for tour_code, tour_label in _TOUR_LABEL.items():
        matches = get_matches_index(tour_code, since_date=since_date)
        winners = derive_match_winners(tour_code, since_date=since_date)
        overview = get_overview_stats(tour_code)

        for entry in matches:
            summary["total_matches"] += 1
            winner_side = winners.get(entry["match_id"])
            if winner_side is None:
                summary["no_winner_derivable"] += 1

            stats = overview.get(entry["match_id"], {})
            if not stats:
                summary["no_stats"] += 1

            result = import_or_merge_match(entry, winner_side, tour_label, stats)
            mode = result.get("mode")
            if mode in summary:
                summary[mode] += 1
            elif mode == "error":
                summary["errors"] += 1

    return summary


if __name__ == "__main__":
    print(json.dumps(run_import(), indent=2, ensure_ascii=False))
