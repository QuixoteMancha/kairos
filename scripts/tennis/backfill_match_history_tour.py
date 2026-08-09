"""Rellena `MatchHistory.tour` ("ATP"|"WTA") -- 2026-08-09, pedido
explícito del usuario: "en MatchHistory está ATP y WTA, mezclados,
verdad? -- Sí, añadir tour".

CRITERIO (fail-closed, nunca se adivina "ATP por default"):
  - `source="sackmann"` -> siempre "ATP", con certeza: `TmlDatabaseMatch`
    (la fuente) es ATP-only por diseño (no existe equivalente WTA del
    mismo autor, ver `outer_apis/scrapers/tennis/tml_database_scraper.py`).
  - `source="tennisexplorer"` -> se resuelve por presencia real en
    `WtaPlayerRankingHistory` (mismo criterio ya usado por
    `wta_favorite_surface_winrate_filter_backtest.py`): si CUALQUIERA
    de los 2 jugadores aparece ahí, es WTA. Si no, se confirma ATP
    solo si alguno aparece como winner/loser real en `TmlDatabaseMatch`
    -- nunca se asume ATP por ausencia de evidencia WTA. Si ninguna de
    las 2 fuentes lo confirma, queda `None` (sin adivinar)."""

from __future__ import annotations


def resolve_tour(player_a_name: str, player_b_name: str, wta_names: set[str], atp_names: set[str]) -> str | None:
    """Función pura, testeable sin DB -- `wta_names`/`atp_names` son los
    universos ya cargados (ver `run_backfill` para cómo se arman)."""
    if player_a_name in wta_names or player_b_name in wta_names:
        return "WTA"
    if player_a_name in atp_names or player_b_name in atp_names:
        return "ATP"
    return None


def run_backfill() -> dict:
    from database.db import init_db_historical, db_historical
    from database.historical.tennis import MatchHistory, WtaPlayerRankingHistory, TmlDatabaseMatch

    init_db_historical()
    with db_historical.connection_context():
        wta_names = {r.player_name for r in WtaPlayerRankingHistory.select(WtaPlayerRankingHistory.player_name).distinct()}
        atp_names: set[str] = set()
        for r in TmlDatabaseMatch.select(TmlDatabaseMatch.winner_name, TmlDatabaseMatch.loser_name).distinct():
            atp_names.add(r.winner_name)
            atp_names.add(r.loser_name)

    updated_sackmann = updated_tennisexplorer = unresolved = 0
    with db_historical.connection_context():
        # Sackmann: ATP cierto por diseño de la fuente, sin necesidad de resolver nada.
        updated_sackmann = (
            MatchHistory.update(tour="ATP")
            .where((MatchHistory.source == "sackmann") & MatchHistory.tour.is_null())
            .execute()
        )

        rows = list(
            MatchHistory.select().where(
                (MatchHistory.source == "tennisexplorer") & MatchHistory.tour.is_null()
            )
        )
        for row in rows:
            tour = resolve_tour(row.player_a_name, row.player_b_name, wta_names, atp_names)
            if tour is None:
                unresolved += 1
                continue
            row.tour = tour
            row.save()
            updated_tennisexplorer += 1

    summary = {
        "updated_sackmann": updated_sackmann,
        "updated_tennisexplorer": updated_tennisexplorer,
        "unresolved": unresolved,
    }
    print(f"[backfill_match_history_tour] {summary}", flush=True)
    return summary


if __name__ == "__main__":
    import json
    print(json.dumps(run_backfill(), indent=2, ensure_ascii=False))
