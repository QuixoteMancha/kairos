"""
Peewee models for tennis historical/reference data (`db_historical`, see
database/db.py). NEW tables, NOT rescued from the old schema — the old
`Player`/`PlayerElo`/`PlayerMatchStats`/`RankingHistory`/`RecentMatchHistory`/
`H2HRecord`/`Match`/`MatchStats`/`MatchVolatility`/`Tournament` tables
(inspected via SSH 2026-07-21) were ALL empty (never populated by the old
system) and designed for a different, more elaborate architecture
(surrogate-key `Player` FK chains, Sofascore event IDs — Sofascore
returns 403 and isn't used in this rebuild, travel-fatigue fields like
`timezone_changes_7d`/`distance_traveled_7d`, rival serve/BPC tiers from
the old dwell-time model) that doesn't match what this repo's current
live tennis data sources actually produce.

Instead, these 4 tables map 1:1 onto what the existing `APIs/tennis_*.py`
facades already fetch live, using player NAME as the identifier (no
surrogate Player table/FK chain — consistent with how
`APIs/tennis_rankings.py`/`APIs/tennis_match_history.py`/
`APIs/kalshi_tennis_markets.py` already work, all name-keyed):

  - TennisPlayerRanking  <- APIs/tennis_rankings.py (get_top_rankings)
  - TennisServeCalibration <- APIs/tennis_serve_stats.py (calibrate_serve_pct_by_bucket)
  - TennisMatchHistory   <- APIs/tennis_match_history.py (get_recent_matches, Flashscore-sourced)
  - TennisHeadToHead     <- APIs/tennis_match_history.py (get_head_to_head)

IMPORTANT (same caveat as models_historical.py): this only defines the
schema. No collector/worker persists into these tables yet — the
`APIs/tennis_*.py` facades currently only cache in-process (TTL dicts,
lost on restart). Wiring them to also write here is separate future work.
"""

from peewee import (
    CharField, DateTimeField, FloatField, IntegerField, Model,
)

from database.db import db_historical


class TennisHistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class TennisPlayerRanking(TennisHistoricalBaseModel):
    """One row per (player, fetch) snapshot of a player's current ATP/WTA
    ranking — see APIs/tennis_rankings.py::get_top_rankings. Accumulating
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


class TennisServeCalibration(TennisHistoricalBaseModel):
    """One row per (tour, rank bucket, calibration run) snapshot of the
    real p_serve calibration — see
    APIs/tennis_serve_stats.py::calibrate_serve_pct_by_bucket, which feeds
    models/volatility/sports/tennis/tennis_markov.py. Persisting this
    over time lets the calibration be tracked/audited across runs (is
    p_serve drifting? is n_matches per bucket growing?) instead of only
    living in the 6h in-process cache."""

    class Meta:
        table_name = "tennis_serve_calibration"
        indexes = ((("tour", "bucket_min", "bucket_max"), False),)

    tour = CharField()  # "ATP" | "WTA"
    bucket_min = IntegerField()
    bucket_max = IntegerField()
    p_serve = FloatField()
    n_matches = IntegerField()
    n_players = IntegerField()
    calibrated_at = DateTimeField()


class TennisMatchHistory(TennisHistoricalBaseModel):
    """One row per real charted match result for a player — see
    APIs/tennis_match_history.py::get_recent_matches (Flashscore-sourced).
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


class TennisHeadToHead(TennisHistoricalBaseModel):
    """One row per (player_a, player_b, computation) snapshot of head-to-
    head record — see APIs/tennis_match_history.py::get_head_to_head.
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
