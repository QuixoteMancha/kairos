"""
Peewee models for TEAM-SPORT historical/reference data (`db_historical`)
— football/soccer, basketball, baseball, hockey. Generic across sport
(via a `sport` column), same design philosophy as
database/models_historical_tennis.py: name-keyed (no surrogate Team
table/FK chain), NEW tables (not rescued from the old dead schema).

RESEARCH DONE 2026-07-21 (before building this): investigated live real
data sources per remaining sport, so this isn't guessed:

- Football/soccer: `TeamRating` (database/models_historical.py) already
  has 788 REAL rows from ClubElo (source='clubelo'), confirmed genuinely
  live-fetched as recently as today by the old system before it was
  decommissioned (the `updated_at` on rescued rows is same-day). ClubElo
  has a documented public API (`api.clubelo.com/{date}`,
  `api.clubelo.com/{club}`, `api.clubelo.com/Fixtures` for match
  probabilities) -- COULD NOT re-verify live from this sandbox (requests
  hung/returned empty, likely a sandbox network restriction, not
  necessarily the API being down) but the rescued data + public docs are
  solid enough evidence it's real and viable to wire live later.
- Basketball (NBA): NO dedicated free live Elo API found. Several
  GitHub repos compute their own Elo from game results; no ready-to-call
  endpoint.
- Baseball (MLB): NO dedicated free live Elo API found either, but MLB's
  own official Stats API (schedules/scores/standings) IS free and
  documented -- an Elo could be computed from that raw data ourselves,
  not found pre-computed anywhere free.
- Hockey (NHL): NO dedicated free live Elo API found (hockeyelo.com/
  harvitronix.com publish ratings but no API, would need scraping). The
  official NHL API for raw game data (schedules/scores/standings) is
  free and keyless, same situation as MLB.

CONCLUSION: only football/soccer has a genuine plug-and-play live rating
source (ClubElo) confirmed real. Basketball/baseball/hockey would need
either scraping a specific ratings site OR building an Elo calculator
from each league's free official raw-game API -- neither is built this
session, this file is schema-only groundwork so those sports have
somewhere to write once a real source is wired, consistent with how
`TennisPlayerRanking` etc. existed before `APIs/tennis_*.py` was wired to
write into them.

These 2 tables complement `TeamRating` (ratings) with real match
outcomes and head-to-head, generic across all 4 team sports:

  - TeamMatchHistory
  - TeamHeadToHead
"""

from peewee import CharField, DateTimeField, IntegerField, Model


from database.db import db_historical


class TeamSportHistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class TeamMatchHistory(TeamSportHistoricalBaseModel):
    """One row per real match result for a team, any of the 4 team
    sports (football/basketball/baseball/hockey), distinguished by the
    `sport` column. `result` supports "D" (draw) in addition to "W"/"L"
    since football allows draws (unlike tennis, which never does --
    that's why TennisMatchHistory's `result` field only needed W/L)."""

    class Meta:
        table_name = "team_match_history"
        indexes = ((("team_name", "sport"), False),)

    team_name = CharField()
    sport = CharField()  # "football" | "basketball" | "baseball" | "hockey"
    opponent_name = CharField()
    match_date = CharField()  # "YYYY-MM-DD"
    result = CharField()  # "W" | "L" | "D"
    score = CharField(null=True)
    source = CharField()
    fetched_at = DateTimeField()


class TeamHeadToHead(TeamSportHistoricalBaseModel):
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
