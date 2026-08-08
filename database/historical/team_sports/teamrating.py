"""
Peewee model for real team Elo-style ratings — see
database/historical/team_sports/__init__.py for the full research context
(which sports have live sources, which don't).
"""

from peewee import CharField, DateTimeField, FloatField, IntegerField, TextField

from database.base_models import HistoricalBaseModel


class TeamRating(HistoricalBaseModel):
    """Real team Elo-style ratings. MULTI-SPORT (the `sport` column is
    real, not decorative): football (ClubElo), american_football (NFL,
    nflverse), basketball (NBA, self-computed), baseball (MLB,
    self-computed), hockey (NHL, self-computed) — see
    database/historical/team_sports/__init__.py for the research/build
    history per sport.

    Column names/types verified against the real DDL on the mini-pc
    (`PRAGMA`/`sqlite_master.sql`, 2026-07-21) — NOT guessed from a
    sample row (a first attempt at this model got `league`/`elo`/`name`
    wrong by guessing from row values; corrected before use).

    IMPORTANT semantics, found the hard way (2026-07-21): this is a
    CURRENT-STATE table, one row per (sport, name_norm), NOT a time
    series — the real schema has
    `CREATE UNIQUE INDEX teamrating_sport_name_norm ON
    teamrating(sport, name_norm)` (only discovered by inspecting indexes
    on `sqlite_master`, not just the table's own CREATE TABLE sql — a
    first attempt at outer_apis/football_ratings.py assumed this was a time
    series like TennisPlayerRanking and got IntegrityError on every
    write in production). Writers MUST upsert (insert-or-update on
    conflict), never blind-insert. See writer.py in this package."""

    class Meta:
        table_name = "teamrating"
        indexes = ((("sport", "name_norm"), True),)  # True = UNIQUE, matches the real schema

    created_at = DateTimeField()
    sport = CharField()
    name = CharField()
    name_norm = CharField()
    league = CharField()
    elo = FloatField()
    n_games = IntegerField()
    source = CharField()
    updated_at = CharField()  # sic: stored as VARCHAR in the original schema, not DATETIME
    extra_json = TextField()
