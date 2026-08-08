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

from database.historical.match_models import (
    Match as TennisScannedMatch,
    ProbabilityTick as TennisMatchProbabilityTick,
    MatchWeather as TennisMatchWeather,
    MatchStats as TennisMatchStats,
    TennisPlayerBio,
    TennisPlayerRanking,
    TennisServeCalibration,
    TennisMatchHistory,
    TennisHistoricalOdds,
    TmlDatabaseMatch,
    WtaPlayerRankingHistory,
    WtaTournamentLevel,
    TennisExplorerPlayerSlug,
    TournamentLocationCache,
    TennisHeadToHead,
    TennisSurfaceMatchHistory,
    TennisAbstractElo,
    TennisAbstractYelo,
    TennisAbstractCareerSplit,
    TennisAbstractH2H,
    TennisRecentResult,
    ChartedMatch,
    ChartedMatchStats,
)
from database.historical.tennis.matchstats import has_match_stats, save_match_stats, get_match_stats
# Prefer consolidated models where available; keep legacy modules available for
# functions that still import them directly. Above we mapped common names to
# the consolidated `database.historical.match_models` classes so existing
# imports that expect e.g. `TennisScannedMatch` continue to work.
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
    "TmlDatabaseMatch", "TournamentLocationCache", "WtaPlayerRankingHistory", "WtaTournamentLevel",
    "TennisExplorerPlayerSlug",
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
