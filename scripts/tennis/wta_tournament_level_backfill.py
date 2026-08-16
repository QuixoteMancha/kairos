"""Backfill del catálogo real de categorías de torneo WTA (Grand Slam /
WTA 1000 / WTA 500 / WTA 250 / Premier / Premier 5 / Premier Mandatory /
International / Finals / WTA 125 / 125K / ITF) -- 2026-08-05, pedido
explícito del usuario: "me faltó agregar que solo tomen matches de
torneos 250 para arriba en nivel [también para WTA]".

ATP ya filtra esto por defecto (`TOUR_LEVELS_250_PLUS`, embebido en cada
fila de TML-Database). WTA no tiene ese dato por partido -- este backfill
separado, contra `api.wtatennis.com/tennis/tournaments` (mismo API
oficial ya usado para ranking), es el paso necesario para poder aplicar
el mismo umbral del lado WTA.

Un solo fetch paginado (no por-jugadora, no necesita pausas largas --
~188 páginas de 100 filas, catálogo completo de torneos, no de
partidos). Idempotente: upsert por (year, tournament_name)."""

from __future__ import annotations

from datetime import datetime, timezone

from database.db import init_db_historical, db_historical
from database.historical.tennis import Tournament
from outer_apis.scrapers.tennis.wta_scraper import get_all_tournaments

# 2026-08-05, bug real encontrado en vivo: la API oficial devuelve, para
# el mismo año, entradas con el MISMO `tournamentGroup.name` (ej.
# "MADRID") pero niveles distintos entre corridas del fetch paginado
# (observado: "Premier Mandatory" en una corrida, "ITF" en otra, mismo
# nombre exacto -- la fuente no es perfectamente estable/determinística
# entre requests). Con upsert "último gana", una colisión así podía
# DEGRADAR silenciosamente un torneo real de tour principal a nivel
# ITF. Fix: nunca degradar -- ante colisión, se conserva/asigna siempre
# el nivel de MAYOR jerarquía real.
_LEVEL_PRIORITY = {
    "Grand Slam": 6, "WTA 1000": 5, "Premier Mandatory": 5, "Premier 5": 5,
    "WTA 500": 4, "Premier": 4, "Finals": 4,
    "WTA 250": 3, "International": 3,
    "WTA 125": 2, "125K": 2,
    "ITF": 1,
}


def _priority(level: str | None) -> int:
    return _LEVEL_PRIORITY.get(level, 0)


def run_backfill() -> dict:
    """2026-08-10, `WtaTournamentLevel` (tabla propia) eliminada,
    reemplazada por `Tournament` (catálogo unificado ATP+WTA, ver
    models.py). Match por (year, tournament_name, tour="WTA") -- el
    índice único real de `Tournament` es solo (year, tournament_name),
    así que se filtra `tour` a mano en Python para nunca pisar una
    fila ATP co-alojada con el mismo nombre+año (ej. Indian Wells,
    Miami -- eventos combinados reales). `city` no se conoce desde esta
    fuente -- se deja vacío ("", NO None -- el campo es NOT NULL) hasta
    que exista un backfill de geocoding real basado en TennisExplorer."""
    init_db_historical()
    tournaments = get_all_tournaments()
    now = datetime.now(timezone.utc)

    inserted = updated = 0
    with db_historical.connection_context():
        for t in tournaments:
            existing = Tournament.get_or_none(
                (Tournament.year == t["year"])
                & (Tournament.tournament_name == t["tournament_name"])
                & (Tournament.tour == "WTA")
            )
            if existing is None:
                Tournament.create(
                    year=t["year"], tournament_name=t["tournament_name"],
                    level=t["level"], tour="WTA", city="", fetched_at=now,
                )
                inserted += 1
            elif existing.level != t["level"] and _priority(t["level"]) > _priority(existing.level):
                existing.level = t["level"]
                existing.fetched_at = now
                existing.save()
                updated += 1

    summary = {"fetched": len(tournaments), "inserted": inserted, "updated": updated}
    print(f"[wta_tournament_level_backfill] TERMINADO: {summary}", flush=True)
    return summary


if __name__ == "__main__":
    run_backfill()
