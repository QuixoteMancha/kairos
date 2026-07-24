from peewee import CharField, DateTimeField, IntegerField, Model

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class TennisHeadToHead(HistoricalBaseModel):
    """One row per (player_a, player_b, computation) snapshot of head-to-
    head record — see outer_apis/tennis_match_history.py::get_head_to_head.
    Player order is NOT normalized (a vs b is whatever order the caller
    used) — a query needs to check both orderings if it wants a
    canonical lookup; kept simple rather than adding a normalization
    step that isn't needed by any current caller."""

    class Meta:
        table_name = "tennis_head_to_head"
        indexes = ((("player_a_name", "player_b_name"), False),)

    player_a_name = CharField()
    player_b_name = CharField()
    a_wins = IntegerField()
    b_wins = IntegerField()
    n_matches = IntegerField()
    computed_at = DateTimeField()
