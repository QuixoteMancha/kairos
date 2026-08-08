from peewee import CharField, DateTimeField, IntegerField

from database.base_models import HistoricalBaseModel


class TeamHeadToHead(HistoricalBaseModel):
    """One row per (team_a, team_b, sport, computation) head-to-head
    snapshot. `draws` is separate from a_wins/b_wins (unlike
    TennisHeadToHead, which has no draw concept)."""

    class Meta:
        table_name = "team_head_to_head"
        indexes = ((("team_a_name", "team_b_name", "sport"), False),)

    team_a_name = CharField()
    team_b_name = CharField()
    sport = CharField()
    a_wins = IntegerField()
    b_wins = IntegerField()
    draws = IntegerField(default=0)
    n_matches = IntegerField()
    computed_at = DateTimeField()
