"""
Tennis historical/reference data (`db_historical`). NEW tables, NOT
rescued from the old schema — the old Player/PlayerElo/PlayerMatchStats/
RankingHistory/RecentMatchHistory/H2HRecord/Match/MatchStats/
MatchVolatility/Tournament tables (inspected via SSH 2026-07-21) were ALL
empty (never populated by the old system) and designed for a different,
more elaborate architecture (surrogate-key Player FK chains, Sofascore
event IDs — Sofascore returns 403 and isn't used in this rebuild) that
doesn't match what this repo's current live tennis data sources produce.

These 4 tables map 1:1 onto what `outer_apis/tennis_*.py` already fetches live,
using player NAME as the identifier (no surrogate Player table/FK chain):

  - TennisPlayerRanking    <- outer_apis/tennis_rankings.py
  - TennisServeCalibration <- outer_apis/tennis_serve_stats.py
  - TennisMatchHistory     <- outer_apis/tennis_match_history.py (recent matches)
  - TennisHeadToHead       <- outer_apis/tennis_match_history.py (h2h)

`writer.py` in this package is what `outer_apis/tennis_*.py` calls to persist
on a fresh fetch (never on a cache hit).
"""

from database.historical.tennis.tennisplayerranking import TennisPlayerRanking
from database.historical.tennis.tennisservecalibration import TennisServeCalibration
from database.historical.tennis.tennismatchhistory import TennisMatchHistory
from database.historical.tennis.tennisheadtohead import TennisHeadToHead
from database.historical.tennis.writer import (
    save_rankings_snapshot, save_serve_calibration_snapshot,
    save_match_history, save_head_to_head_snapshot,
)

__all__ = [
    "TennisPlayerRanking", "TennisServeCalibration", "TennisMatchHistory", "TennisHeadToHead",
    "save_rankings_snapshot", "save_serve_calibration_snapshot",
    "save_match_history", "save_head_to_head_snapshot",
]
