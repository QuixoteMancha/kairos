"""
Team-sport historical/reference data (`db_historical`) — football/soccer,
american_football (NFL), basketball, baseball, hockey. Generic across
sport (via a `sport` column on every model here), name-keyed (no
surrogate Team table/FK chain).

RESEARCH DONE 2026-07-21 (before building this, not guessed):
- Football/soccer: `TeamRating` had 788 REAL rows from ClubElo already
  rescued (source='clubelo'), confirmed genuinely live-fetched same-day
  as the old system's decommissioning. ClubElo has a documented public
  API (`api.clubelo.com/{date}`, `/{club}`, `/Fixtures`) — confirmed live
  (see kairos-team-sports-tables-and-research-2026-07-21 memory for the
  full verification, including a false-negative sandbox network hang
  that eventually returned real data). ZERO MLS/USA coverage confirmed
  (57 countries, all European) — an open gap, KXMLSGAME is the only open
  Kalshi football series and ClubElo doesn't cover it.
- American football (NFL): real data already rescued too
  (source='nflverse', 32 teams) — nflverse-data's public `games.csv`
  GitHub release wired live (`APIs/nfl_ratings.py`).
- Basketball (NBA): no dedicated free live Elo API. Self-computed from
  stats.nba.com (needs curl_cffi TLS impersonation, same fix class as
  ATP tour) via `APIs/basketball_ratings.py`.
- Baseball (MLB): no dedicated free live Elo API, but MLB's own official
  Stats API is free — self-computed via `APIs/baseball_ratings.py`.
- Hockey (NHL): same situation as MLB, self-computed via
  `APIs/hockey_ratings.py` against api-web.nhle.com.

All 4 self-computed sports share `APIs/elo_engine.py` (the math) and
`writer.py` in this package (persistence + the `find_team_rating` prefix-
match lookup, needed because Kalshi's market data gives short/partial
team names that don't exact-match the full names stored here).
"""

from database.historical.team_sports.teamrating import TeamRating
from database.historical.team_sports.teammatchhistory import TeamMatchHistory
from database.historical.team_sports.teamheadtohead import TeamHeadToHead
from database.historical.team_sports.writer import compute_and_persist_elo, find_team_rating

__all__ = [
    "TeamRating", "TeamMatchHistory", "TeamHeadToHead",
    "compute_and_persist_elo", "find_team_rating",
]
