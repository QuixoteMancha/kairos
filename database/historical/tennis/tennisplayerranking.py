from peewee import CharField, DateTimeField, IntegerField, Model

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class TennisPlayerRanking(HistoricalBaseModel):
    """One row per (player, fetch) snapshot of a player's current ATP/WTA
    ranking — see outer_apis/tennis_rankings.py::get_top_rankings. Accumulating
    these over time builds a real ranking-history time series (something
    the old schema had a dedicated table for, RankingHistory, but never
    populated) — useful later for bucketing a player's rank AT THE TIME
    of a historical match, instead of today's approximation (see the
    documented limitation in kairos-tennis-real-calibration-both-tours:
    Match Charting Project matches are historical, only current rank is
    available today)."""

    class Meta:
        table_name = "tennis_player_ranking"
        indexes = ((("player_name", "tour"), False),)

    player_name = CharField()
    tour = CharField()  # "ATP" | "WTA"
    ranking = IntegerField()
    country = CharField(null=True)
    fetched_at = DateTimeField()
