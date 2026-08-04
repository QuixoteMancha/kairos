from peewee import CharField, FloatField, IntegerField, Model

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class TmlDatabaseMatch(HistoricalBaseModel):
    """Caché LOCAL de `Tennismylife/TML-Database` (1968-2026) -- 2026-08-04,
    pedido explícito del usuario: "el backtesting se debe hacer con
    datos de base, nada de estar llamando a fuentes en red a cada rato".

    Antes de esto, `outer_apis/scrapers/tennis/tml_database_scraper.py`
    hacía un fetch en vivo por año, EN CADA CORRIDA del backtest (59
    años x N corridas por sesión) -- además de ser lento e innecesario,
    causó un hallazgo real de no-determinismo (2026-08-04): un fallo
    transitorio de red en un año puntual se absorbía en silencio
    (fail-closed correcto) y cambiaba los resultados sin ningún aviso.

    Esta tabla persiste el CSV real tal cual viene de la fuente (todos
    los niveles de torneo, sin filtrar por `TOUR_LEVELS_250_PLUS` --
    el filtro se sigue aplicando en Python al leer, igual que antes,
    para no tener que re-fetchear si cambia el filtro de niveles). Se
    puebla UNA VEZ vía `scripts/tml_database_local_backfill.py` (fetch
    de red explícito, deliberado, fuera del loop de backtesting) y
    después el scraper lee de acá por defecto -- fetch de red solo si
    se pide explícitamente un refresh."""

    class Meta:
        table_name = "tml_database_match"
        indexes = (
            (("year", "date", "winner_name", "loser_name", "tourney_name"), True),
        )

    year = IntegerField()
    tourney_name = CharField(null=True)
    level = CharField(null=True)
    date = CharField()  # "YYYY-MM-DD"
    round = CharField(null=True)
    surface = CharField(null=True)
    best_of = IntegerField(null=True)
    winner_name = CharField()
    winner_rank = IntegerField(null=True)
    winner_age = FloatField(null=True)
    loser_name = CharField()
    loser_rank = IntegerField(null=True)
    loser_age = FloatField(null=True)
    score = CharField(null=True)
    minutes = IntegerField(null=True)
