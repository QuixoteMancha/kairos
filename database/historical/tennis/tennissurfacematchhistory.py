from peewee import CharField, DateTimeField, Model

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class TennisSurfaceMatchHistory(HistoricalBaseModel):
    """One row per real match result WITH SURFACE, sourced from
    TennisExplorer.com (see outer_apis/scrapers/tennis/tennisexplorer_scraper.py)
    -- separate table from TennisMatchHistory (Flashscore-sourced)
    deliberately: Flashscore's feed has no surface field at all (confirmed
    2026-07-30, see kairos-tennis-surface-form-2026-07-30 memory), so this
    isn't an extra nullable column on that table, it's a genuinely
    different source with a different schema contract (every row here
    always has a real surface, never null-by-source-limitation).

    Used for `outer_apis/sports/tennis/surface_form.py`'s "recent form on
    THIS surface" signal -- a player's overall recent-form win rate
    (TennisMatchHistory-based) can look very different from their
    form specifically on the surface of the upcoming match."""

    class Meta:
        table_name = "tennis_surface_match_history"
        indexes = ((("player_name",), False),)

    player_name = CharField()
    opponent_name = CharField()
    match_date = CharField()  # "YYYY-MM-DD"
    surface = CharField()  # "Clay" | "Hard" | "Grass" | "Indoors" -- never null, see class docstring
    result = CharField()  # "W" | "L"
    score = CharField(null=True)
    tournament = CharField(null=True)
    source = CharField()  # "tennisexplorer"
    fetched_at = DateTimeField()
