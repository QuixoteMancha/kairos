"""Reparación dirigida: identifica jugadores/as con partidos que
quedaron con `score` (y por lo tanto cuota) vacíos en `TennisMatchHistory`,
y re-corre el backfill real de TennisExplorer solo para ellos --
2026-08-06, pedido explícito del usuario: "junta todos los matches sin
score y sin cuotas y arma un script que haga fill de ambos campos".

Alcance real medido antes de este script: 184 filas con `score` vacío
Y sin cuota (0 filas tenían cuota sin score -- consistente, la cuota
solo se inserta cuando el partido trajo un score real parseable),
124 jugadores/as distintos afectados.

Reusa `backfill_player` (ya extendido en `wta_top200_historical_backfill.py`
para reparar `score` vacío, además del `tournament=NULL` que ya
reparaba) -- este script solo se encarga de encontrar el universo real
a re-procesar, no duplica lógica de fetch/parseo/reparación."""

from __future__ import annotations

from database.db import init_db_historical, db_historical
from database.historical.tennis import TennisMatchHistory, TennisHistoricalOdds
from scripts.tennis.wta_top200_historical_backfill import _run_backfill_for_names


def find_players_missing_score_and_odds() -> list[str]:
    init_db_historical()
    with db_historical.connection_context():
        odds_keys = {
            (o.player_name, o.opponent_name, o.match_date)
            for o in TennisHistoricalOdds.select(TennisHistoricalOdds.player_name, TennisHistoricalOdds.opponent_name, TennisHistoricalOdds.match_date)
        }
        players: set[str] = set()
        for r in TennisMatchHistory.select(TennisMatchHistory.player_name, TennisMatchHistory.opponent_name, TennisMatchHistory.match_date, TennisMatchHistory.score):
            if r.score:
                continue
            if (r.player_name, r.opponent_name, r.match_date) not in odds_keys:
                players.add(r.player_name)
    return sorted(players)


def run_fill(max_workers: int = 1) -> list[dict]:
    names = find_players_missing_score_and_odds()
    return _run_backfill_for_names(names, tag="fill_missing_score_and_odds", max_workers=max_workers)


if __name__ == "__main__":
    import sys
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    run_fill(max_workers=workers)
