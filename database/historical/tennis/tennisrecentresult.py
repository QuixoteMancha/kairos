from peewee import BooleanField, CharField, DateTimeField, Model

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class TennisRecentResult(HistoricalBaseModel):
    """Últimos resultados reales por jugador/a (Tennis Abstract,
    sección `recent-results`), con foco en detectar retiros/
    no-presentaciones reales del propio jugador -- señal de riesgo de
    lesión pedida explícitamente por el usuario 2026-07-31 tras ver a
    Svitolina jugar con una lesión de cadera real (walkover confirmado el
    2026-06-21 en Bad Homburg). Ver
    outer_apis/scrapers/tennis/tennisabstract_player_scraper.py::get_recent_results
    y outer_apis/sports/tennis/injury_risk.py."""

    class Meta:
        table_name = "tennis_recent_result"
        indexes = ((("player_name", "match_date", "opponent_name"), True),)

    player_name = CharField()
    match_date = CharField()  # "YYYY-MM-DD"
    tournament = CharField(null=True)
    opponent_name = CharField()
    subject_won = BooleanField()
    retired_or_walkover = BooleanField()  # True SOLO si el sujeto (no el rival) se retiró/no se presentó
    fetched_at = DateTimeField()
