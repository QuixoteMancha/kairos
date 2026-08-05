from peewee import CharField, DateTimeField, IntegerField, Model

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class WtaTournamentLevel(HistoricalBaseModel):
    """Categoría real del torneo (Grand Slam / WTA 1000 / WTA 500 /
    WTA 250 / Premier / Premier 5 / Premier Mandatory / International /
    Finals / WTA 125 / 125K / ITF) por (año, nombre) -- 2026-08-05,
    pedido explícito del usuario: "me faltó agregar que solo tomen
    matches de torneos 250 para arriba en nivel [también para WTA]".

    Fuente: `api.wtatennis.com/tennis/tournaments` (paginado, 100/página,
    18,738 entradas totales 1960-2026, verificado en vivo) -- el mismo
    endpoint oficial ya usado para ranking (`wta_scraper.py`). A
    diferencia de ATP (TML-Database trae `tourney_level` embebido en
    cada fila de partido, ver `TOUR_LEVELS_250_PLUS`), WTA no tiene
    equivalente por-partido -- este backfill separado es el paso
    necesario para poder aplicar el mismo umbral.

    `tournament_name` se guarda TAL CUAL viene de la fuente
    (`tournamentGroup.name`, mayúsculas, ej. "INDIAN WELLS") -- la
    normalización para matchear contra el nombre libre de
    `TennisMatchHistory.tournament` (ej. "Indian Wells WTA") vive en
    `scripts/tennis/wta_favorite_surface_winrate_filter_backtest.py::
    _normalize_tournament_name`, no acá (esta tabla es un caché fiel de
    la fuente, sin opiniones de matching)."""

    class Meta:
        table_name = "wta_tournament_level"
        indexes = (
            (("year", "tournament_name"), True),
        )

    year = IntegerField()
    tournament_name = CharField()
    level = CharField(null=True)  # None real de la fuente = sin categoría reportada, fail-closed
    fetched_at = DateTimeField()
