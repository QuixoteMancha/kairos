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
from database.historical.tennis.tennishistoricalodds import TennisHistoricalOdds
from database.historical.tennis.tmldatabasematch import TmlDatabaseMatch
from database.historical.tennis.tennisheadtohead import TennisHeadToHead
from database.historical.tennis.tennissurfacematchhistory import TennisSurfaceMatchHistory
from database.historical.tennis.tennisabstractelo import TennisAbstractElo, TennisAbstractYelo
from database.historical.tennis.tennisabstractplayerstats import TennisAbstractCareerSplit, TennisAbstractH2H
from database.historical.tennis.tennisscannedmatch import TennisScannedMatch
from database.historical.tennis.tennismatchprobabilitytick import TennisMatchProbabilityTick
from database.historical.tennis.tennisrecentresult import TennisRecentResult
from database.historical.tennis.tennismatchweather import TennisMatchWeather
from database.historical.tennis.tennismatchstats import TennisMatchStats
from database.historical.tennis.matchstats import has_match_stats, save_match_stats, get_match_stats
from database.historical.tennis.tennisplayerbio import TennisPlayerBio
from database.historical.tennis.chartedmatch import ChartedMatch
from database.historical.tennis.chartedmatchstats import ChartedMatchStats
from database.historical.tennis.chartedmatchimport import (
    find_overlapping_scanned_match, save_charted_match, save_charted_match_stats,
    import_or_merge_match,
)
from database.historical.tennis.matchtracking import (
    register_scanned_match, register_scanned_matches, get_open_scanned_matches,
    save_probability_tick, mark_match_settled, backfill_missing_tournaments,
    backfill_missing_rounds, filter_new_markets,
)
from database.historical.tennis.writer import (
    save_recent_results, get_recent_results,
    save_rankings_snapshot, save_serve_calibration_snapshot,
    save_match_history, save_head_to_head_snapshot,
    save_surface_match_history, get_recent_surface_match_history,
    save_tennisabstract_elo_snapshot, get_current_tennisabstract_elo,
    save_tennisabstract_yelo_snapshot, get_current_tennisabstract_yelo,
    save_tennisabstract_career_splits, get_current_career_splits,
    save_tennisabstract_h2h, get_current_tennisabstract_h2h,
    get_all_career_splits_for_surface, get_latest_rankings, get_all_surface_matches,
)

__all__ = [
    "TennisPlayerRanking", "TennisServeCalibration", "TennisMatchHistory", "TennisHistoricalOdds", "TennisHeadToHead",
    "TmlDatabaseMatch",
    "TennisSurfaceMatchHistory", "TennisAbstractElo", "TennisAbstractYelo",
    "TennisAbstractCareerSplit", "TennisAbstractH2H",
    "save_rankings_snapshot", "save_serve_calibration_snapshot",
    "save_match_history", "save_head_to_head_snapshot",
    "save_surface_match_history", "get_recent_surface_match_history",
    "save_tennisabstract_elo_snapshot", "get_current_tennisabstract_elo",
    "save_tennisabstract_yelo_snapshot", "get_current_tennisabstract_yelo",
    "save_tennisabstract_career_splits", "get_current_career_splits",
    "save_tennisabstract_h2h", "get_current_tennisabstract_h2h",
    "get_all_career_splits_for_surface", "get_latest_rankings", "get_all_surface_matches",
    "TennisScannedMatch", "TennisMatchProbabilityTick",
    "register_scanned_match", "register_scanned_matches", "get_open_scanned_matches",
    "save_probability_tick", "mark_match_settled", "backfill_missing_tournaments",
    "backfill_missing_rounds", "filter_new_markets",
    "TennisRecentResult", "save_recent_results", "get_recent_results",
    "TennisMatchWeather",
    "TennisMatchStats", "has_match_stats", "save_match_stats", "get_match_stats",
    "TennisPlayerBio",
    "ChartedMatch", "ChartedMatchStats",
    "find_overlapping_scanned_match", "save_charted_match", "save_charted_match_stats",
    "import_or_merge_match",
]
