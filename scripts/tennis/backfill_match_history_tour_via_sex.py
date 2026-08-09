"""Segunda pasada del backfill de `MatchHistory.tour` -- 2026-08-09,
pedido explícito del usuario: "para resolver el tour de los matches
que no lo tienen, usar el scraper de tennisexplorer para obtener el
sexo de los players".

`backfill_match_history_tour.py` (primera pasada, sin red) deja
`unresolved` los partidos donde ni Sackmann ni el roster WTA confirman
el tour -- típicamente jugadores nuevos/recientes que esas 2 fuentes
(ambas con retraso real) todavía no cubren. Esta segunda pasada SÍ usa
red (TennisExplorer, `get_tour_by_sex`) para resolverlos con certeza,
vía el campo real "Sex: man"|"Sex: woman" de la página de perfil.

EFICIENCIA: agrupa por JUGADOR único antes de fetchear -- un jugador
puede aparecer en decenas de partidos sin `tour`, un solo fetch por
persona alcanza para resolver todos sus partidos de una vez (nunca se
repite el mismo request). Throttle real ya compartido (mismo mecanismo
que el resto del scraper, `tennisexplorer_throttle`)."""

from __future__ import annotations

from outer_apis.scrapers.tennis.tennisexplorer_player_scraper import get_tour_by_sex


def run_backfill(limit_players: int | None = None) -> dict:
    from database.db import init_db_historical, db_historical
    from database.historical.tennis import MatchHistory

    init_db_historical()
    with db_historical.connection_context():
        rows = list(MatchHistory.select().where(MatchHistory.tour.is_null()))

    unique_players: dict[str, list] = {}
    for row in rows:
        unique_players.setdefault(row.player_a_name, []).append(row)
        unique_players.setdefault(row.player_b_name, []).append(row)

    player_names = list(unique_players.keys())
    if limit_players is not None:
        player_names = player_names[:limit_players]

    resolved_players = 0
    unresolved_players = 0
    updated_rows = 0
    tour_cache: dict[str, str | None] = {}

    for name in player_names:
        tour = get_tour_by_sex(name)
        tour_cache[name] = tour
        if tour is None:
            unresolved_players += 1
        else:
            resolved_players += 1

    with db_historical.connection_context():
        for row in rows:
            if row.tour is not None:
                continue  # ya se resolvió por el lado del otro jugador en esta misma corrida
            tour = tour_cache.get(row.player_a_name) or tour_cache.get(row.player_b_name)
            if tour is None:
                continue
            row.tour = tour
            row.save()
            updated_rows += 1

    summary = {
        "unique_players_total": len(unique_players), "players_processed": len(player_names),
        "players_resolved": resolved_players, "players_unresolved": unresolved_players,
        "match_history_rows_updated": updated_rows,
    }
    print(f"[backfill_match_history_tour_via_sex] {summary}", flush=True)
    return summary


if __name__ == "__main__":
    import json
    import sys
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    print(json.dumps(run_backfill(limit_players=limit), indent=2, ensure_ascii=False))
