from peewee import CharField, DateTimeField, Model

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class TennisMatchHistory(HistoricalBaseModel):
    """One row per real charted match result for a player — see
    outer_apis/tennis_match_history.py::get_recent_matches (Flashscore-sourced).
    Persisting this builds a real historical match database over time
    (the old RecentMatchHistory/Match tables never got populated) —
    useful for recent-form calculations that don't need to re-scrape
    Flashscore every time, and as raw material for a future richer
    fair-price model (recency-weighted form, surface-specific form, etc.
    — none of that is built yet, this table just makes the raw data
    durable)."""

    class Meta:
        table_name = "tennis_match_history"
        indexes = ((("player_name",), False),)

    player_name = CharField()
    opponent_name = CharField()
    match_date = CharField()  # "YYYY-MM-DD", matches the source's string format
    result = CharField()  # "W" | "L"
    score = CharField(null=True)
    source = CharField()  # "flashscore"
    fetched_at = DateTimeField()
