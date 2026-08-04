from peewee import CharField, DateTimeField, FloatField, Model

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class TennisHistoricalOdds(HistoricalBaseModel):
    """Cuotas de apuesta REALES pre-match, por partido -- 2026-08-03,
    pedido explícito del usuario ("creo que se podían sacar las odds,
    histórica pre-match no? añadir tabla donde se pongan las odds
    históricas"). Fuente: TennisExplorer.com (ver
    `outer_apis/scrapers/tennis/tennisexplorer_player_scraper.py`,
    columnas "H"/"A" de la tabla de resultados por jugador/a) -- es la
    ÚNICA fuente de precio de mercado histórico real encontrada en toda
    esta investigación (TML-Database, la fuente ATP usada en los
    backtests de `favorite_by_historical_rank_backtest.py`, NO tiene
    ningún precio).

    Tabla SEPARADA de `TennisMatchHistory` (pedido explícito, no
    columnas adicionales en la misma tabla) -- vinculada por
    (player_name, opponent_name, match_date), mismo criterio de
    duplicado que esa tabla.

    Cuota decimal cruda, SIN remover el margen/vig de la casa -- la
    probabilidad implícita (1/cuota) que se derive de esto sobre-estima
    levemente ambos lados (suman >100% por el margen), quien consuma
    este dato es responsable de decidir si normaliza o no."""

    class Meta:
        table_name = "tennis_historical_odds"
        indexes = ((("player_name", "opponent_name", "match_date"), True),)

    player_name = CharField()
    opponent_name = CharField()
    match_date = CharField()  # "YYYY-MM-DD"
    odds_self = FloatField(null=True)  # cuota decimal del propio jugador/a, None si no había cuota registrada
    odds_opponent = FloatField(null=True)
    source = CharField()  # "tennisexplorer"
    fetched_at = DateTimeField()
