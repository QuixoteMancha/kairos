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
  - MatchHistory           <- outer_apis/tennis_match_history.py (recent matches, 1 fila/partido)
  - TennisHeadToHead       <- outer_apis/tennis_match_history.py (h2h)

`writer.py` in this package is what `outer_apis/tennis_*.py` calls to persist
on a fresh fetch (never on a cache hit).
"""

from database.historical.tennis.models import (
    Match as TennisScannedMatch,
    ProbabilityTick as TennisMatchProbabilityTick,
    MatchWeather as TennisMatchWeather,
    MatchStats as TennisMatchStats,
    MatchStatsSet as TennisMatchStatsSet,
    TennisPlayerBio,
    TennisPlayerRanking,
    TennisPlayerSyncStatus,
    TennisServeCalibration,
    MatchHistory,
    Player,
    TennisMatchHistoryUnvalidated,
    TennisHistoricalOdds,
    WtaPlayerRankingHistory,
    WtaTournamentLevel,
    AtpTournamentLevel,
    TennisPlayerWikidataReference,
    TennisExplorerPlayerSlug,
    TournamentLocationCache,
    TennisHeadToHead,
    TennisAbstractElo,
    TennisAbstractYelo,
    TennisAbstractCareerSplit,
    TennisAbstractH2H,
    TennisRecentResult,
)
from database.historical.tennis.matchstats import has_match_stats, save_match_stats, get_match_stats
from database.historical.tennis.player_sync_status import (
    request_priority_sync, is_player_data_fresh, get_pending_priority_requests, mark_player_synced,
)
# 2026-08-08, corregido tras revisión del usuario ("dejaste un modelo
# propio de tennis en models.py"): `Match`/`ProbabilityTick`/
# `MatchWeather`/`MatchStats`/`MatchStatsSet` son 100% tenis en la
# práctica (table_name literal "tennis_*", ningún otro deporte los usa
# hoy) -- viven en `database.historical.tennis.models` junto con el
# resto de lo específico de tenis, NO en `database.historical.models`
# (reservado para modelos base genuinamente compartidos entre 2+
# deportes, que hoy no existen). Este paquete reexporta todo bajo los
# nombres históricos (`TennisScannedMatch`, etc.) para no romper el
# código existente.
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
    save_match_detail, save_next_match, has_real_match_detail_between,
)

__all__ = [
    "TennisPlayerRanking", "TennisServeCalibration", "MatchHistory", "Player", "save_match_detail", "save_next_match",
    "has_real_match_detail_between",
    "TennisMatchHistoryUnvalidated",
    "TennisHistoricalOdds", "TennisHeadToHead",
    "TournamentLocationCache", "WtaPlayerRankingHistory", "WtaTournamentLevel", "AtpTournamentLevel",
    "TennisPlayerWikidataReference",
    "TennisExplorerPlayerSlug",
    "TennisAbstractElo", "TennisAbstractYelo",
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
    "TennisMatchWeather", "TennisMatchStatsSet",
    "TennisMatchStats", "has_match_stats", "save_match_stats", "get_match_stats",
    "TennisPlayerBio",
    "TennisPlayerSyncStatus", "request_priority_sync", "is_player_data_fresh",
    "get_pending_priority_requests", "mark_player_synced",
]
