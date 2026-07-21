from peewee import CharField, DateTimeField, Model

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class TeamMatchHistory(HistoricalBaseModel):
    """One row per real match result for a team, any of the team sports
    (football/american_football/basketball/baseball/hockey), distinguished
    by the `sport` column. `result` supports "D" (draw) in addition to
    "W"/"L" since football allows draws (unlike tennis, which never
    does -- that's why TennisMatchHistory's `result` field only needs
    W/L)."""

    class Meta:
        table_name = "team_match_history"
        indexes = ((("team_name", "sport"), False),)

    team_name = CharField()
    sport = CharField()
    opponent_name = CharField()
    match_date = CharField()  # "YYYY-MM-DD"
    result = CharField()  # "W" | "L" | "D"
    score = CharField(null=True)
    source = CharField()
    fetched_at = DateTimeField()
