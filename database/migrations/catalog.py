"""Catálogo único de modelos y migraciones para ambas bases."""

from __future__ import annotations

from peewee import Database

from database.migrations.runner import Migration


def accounting_models():
    from database.accounting.fillverification import FillVerification
    from database.accounting.limitarbsignal import LimitArbSignal
    from database.accounting.marketarbwebsocketsignal import MarketArbWebsocketSignal
    from database.accounting.paperaccount import PaperAccount
    from database.accounting.papertrade import PaperTrade
    from database.accounting.set1entrysignal import Set1EntrySignal
    from database.accounting.systemhaltstate import SystemHaltState

    return [
        LimitArbSignal, MarketArbWebsocketSignal, Set1EntrySignal,
        PaperTrade, PaperAccount, SystemHaltState, FillVerification,
    ]


def historical_models():
    from database.historical.baseball import MlbGameHistory, MlbHistoricalOdds
    from database.historical.market_data import CryptoPriceSnapshot, KalshiMarketTick
    from database.historical.team_sports import (
        MlbMatchProbabilityTick, MlbScannedMatch, TeamHeadToHead,
        TeamMatchHistory, TeamRating,
    )
    from database.historical.tennis import (
        MatchDuration, MatchHistory, Player, PlayerRankingHistory, PlayerStats,
        TennisAbstractElo, TennisHistoricalOdds, TennisMatchHistoryUnvalidated,
        TennisMatchProbabilityTick, TennisMatchStats, TennisMatchStatsSet,
        TennisMatchWeather, TennisPlayerRanking, TennisPlayerSyncStatus,
        TennisPlayerWikidataReference, TennisRecentResult, TennisScannedMatch,
        Tournament,
    )

    return [
        KalshiMarketTick, CryptoPriceSnapshot, TeamRating, TennisPlayerRanking,
        TennisHistoricalOdds, TennisAbstractElo, PlayerStats, TennisScannedMatch,
        TennisMatchProbabilityTick, TennisRecentResult, TennisMatchWeather,
        TennisMatchStats, TennisMatchStatsSet, Player, MatchDuration, MatchHistory,
        TennisMatchHistoryUnvalidated, TennisPlayerSyncStatus, TeamMatchHistory,
        TeamHeadToHead, MlbGameHistory, MlbScannedMatch, MlbMatchProbabilityTick,
        MlbHistoricalOdds, PlayerRankingHistory, Tournament,
        TennisPlayerWikidataReference,
    ]


def _create_accounting_schema(database: Database) -> None:
    database.create_tables(accounting_models(), safe=True)


def _create_historical_schema(database: Database) -> None:
    database.create_tables(historical_models(), safe=True)


# Las migraciones aplicadas son inmutables. Todo cambio de modelo futuro debe
# agregar una entrada nueva; nunca se edita una versión ya desplegada.
ACCOUNTING_MIGRATIONS = (
    Migration(1, "postgresql_accounting_baseline_20260815", _create_accounting_schema),
)

HISTORICAL_MIGRATIONS = (
    Migration(1, "postgresql_historical_baseline_20260815", _create_historical_schema),
)

