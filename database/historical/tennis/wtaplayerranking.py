from peewee import CharField, DateTimeField, IntegerField, Model

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class WtaPlayerRankingHistory(HistoricalBaseModel):
    """Serie REAL de ranking semanal oficial WTA por jugadora --
    2026-08-05, pedido explícito del usuario ("calcular los resultados
    de esta estrategia tanto en ATP como WTA"). Fuente:
    `outer_apis/scrapers/tennis/wta_scraper.py::get_weekly_ranking_history`
    (api.wtatennis.com, verificado en vivo -- histórico semanal real,
    no solo el ranking actual).

    Es el equivalente WTA de lo que TML-Database provee gratis para ATP
    (ranking real punto-en-el-tiempo) -- a diferencia de TML-Database,
    acá el ranking NO viene embebido en cada fila de partido, hay que
    resolverlo aparte por (jugadora, fecha) buscando la entrada semanal
    más reciente <= la fecha del partido, ver
    `scripts/tennis/wta_favorite_surface_winrate_filter_backtest.py`.

    `player_id`/`player_name`: ambos guardados -- `player_id` es la
    clave real de la fuente (estable ante cambios de nombre/acentos),
    `player_name` es el nombre completo tal cual lo devuelve la API,
    usado para el join contra `TennisMatchHistory` (que no tiene
    player_id, solo nombres)."""

    class Meta:
        table_name = "wta_player_ranking_history"
        indexes = (
            (("player_id", "ranked_at"), True),
        )

    player_id = IntegerField()
    player_name = CharField()
    ranked_at = CharField()  # "YYYY-MM-DD"
    singles_ranking = IntegerField()
    fetched_at = DateTimeField()
