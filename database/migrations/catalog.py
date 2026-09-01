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
    from database.accounting.systemhaltstate import SystemHaltState

    return [
        LimitArbSignal, MarketArbWebsocketSignal,
        PaperTrade, PaperAccount, SystemHaltState, FillVerification,
    ]


def historical_models():
    import os
    from database.historical.workerstate import HistoricalEnrichmentAttempt, HistoricalWorkerState, TennisMlExperiment
    from database.historical.baseball import MlbGameHistory, MlbHistoricalOdds
    from database.historical.market_data import CryptoPriceSnapshot, KalshiMarketTick
    from database.historical.team_sports import (
        MlbMatchProbabilityTick, MlbScannedMatch, TeamHeadToHead,
        TeamMatchHistory, TeamRating,
    )
    from database.historical.tennis import (
        MatchDuration, MatchHistory, Player, PlayerStats,
        TennisHistoricalOdds,
        TennisMatchProbabilityTick, TennisMatchStats, TennisMatchStatsSet,
        TennisFavouriteShadowDecision,
        TennisLivePointSnapshot, TennisLiveStatsSnapshot, TennisLiveLimitArbDecision,
        TennisMatchWeather, TennisPlayerRanking, TennisPlayerSyncStatus,
        TennisScannedMatch,
        Tournament,
        TennisPlayerStrength, TennisEloCalibration,
    )

    models = [
        KalshiMarketTick, CryptoPriceSnapshot, TeamRating, TennisPlayerRanking,
        TennisHistoricalOdds, PlayerStats, TennisScannedMatch,
        TennisMatchProbabilityTick, TennisMatchWeather,
        TennisFavouriteShadowDecision,
        TennisLivePointSnapshot, TennisLiveStatsSnapshot, TennisLiveLimitArbDecision,
        TennisMatchStats, TennisMatchStatsSet, Player, MatchDuration, MatchHistory,
        TennisPlayerSyncStatus, TeamMatchHistory,
        MlbGameHistory, MlbScannedMatch, MlbMatchProbabilityTick,
        MlbHistoricalOdds, Tournament,
        HistoricalWorkerState, HistoricalEnrichmentAttempt,
        TennisPlayerStrength, TennisEloCalibration, TennisMlExperiment,
    ]
    # Los tests de scripts históricos archivados conservan sus tablas
    # efímeras; producción no las crea ni las valida desde migración 9.
    if os.getenv("KAIROS_TESTING") == "1":
        from database.historical.team_sports import TeamHeadToHead
        from database.historical.tennis import (
            PlayerRankingHistory, TennisAbstractElo,
            TennisMatchHistoryUnvalidated, TennisPlayerWikidataReference,
        )
        models.extend([
            TeamHeadToHead, PlayerRankingHistory, TennisAbstractElo,
            TennisMatchHistoryUnvalidated, TennisPlayerWikidataReference,
        ])
    return models


def _create_accounting_schema(database: Database) -> None:
    database.create_tables(accounting_models(), safe=True)


def _create_historical_schema(database: Database) -> None:
    database.create_tables(historical_models(), safe=True)


def _drop_set1_entry_signal(database: Database) -> None:
    """2026-08-17: `Set1EntrySignal` quedó huérfana -- el único productor
    real (`workers/set1_favorite_reaction_worker.py`) se eliminó el
    2026-08-11, reemplazado por `strategies/bet_on_favourite.py` +
    `workers/tennis_favourite_shadow_worker.py`. Verificado en vivo antes
    de este cambio: 0 filas reales en producción, sin pérdida de datos.
    `IF EXISTS` -- idempotente ante una base ya limpia."""
    database.execute_sql("DROP TABLE IF EXISTS set1_entry_signal")


# Las migraciones aplicadas son inmutables. Todo cambio de modelo futuro debe
# agregar una entrada nueva; nunca se edita una versión ya desplegada.
ACCOUNTING_MIGRATIONS = (
    Migration(1, "postgresql_accounting_baseline_20260815", _create_accounting_schema),
    Migration(2, "drop_orphaned_set1_entry_signal_20260817", _drop_set1_entry_signal),
)

def _add_tournament_country(database: Database) -> None:
    """2026-08-16: `Tournament.country`, leído del `<h1>` de la página de
    torneo de TennisExplorer -- hace preciso el geocoding de la sede
    (ver `outer_apis/scrapers/tennis/tennisexplorer_tournament_scraper.py`).
    Aditiva y nullable: no toca ni requiere backfill de las filas
    existentes."""
    database.execute_sql("ALTER TABLE tournament ADD COLUMN IF NOT EXISTS country VARCHAR(255)")


def _create_historical_worker_state(database: Database) -> None:
    from database.historical.workerstate import HistoricalWorkerState
    database.create_tables([HistoricalWorkerState], safe=True)


def _consolidate_ranking_snapshots(database: Database) -> None:
    database.execute_sql("""
        DELETE FROM tennis_player_ranking old
        USING tennis_player_ranking keep
        WHERE old.tour = keep.tour
          AND old.player_name = keep.player_name
          AND old.fetched_at::date = keep.fetched_at::date
          AND old.id < keep.id
    """)
    database.execute_sql("""
        CREATE UNIQUE INDEX IF NOT EXISTS tennis_ranking_one_player_per_day
        ON tennis_player_ranking (tour, player_name, (fetched_at::date))
    """)


def _add_enrichment_attempts_and_stats_quality(database: Database) -> None:
    from database.historical.workerstate import HistoricalEnrichmentAttempt
    database.create_tables([HistoricalEnrichmentAttempt], safe=True)
    database.execute_sql(
        "ALTER TABLE tennis_match_stats ADD COLUMN IF NOT EXISTS is_usable BOOLEAN NOT NULL DEFAULT TRUE"
    )
    database.execute_sql(
        "ALTER TABLE tennis_match_stats ADD COLUMN IF NOT EXISTS invalid_reason TEXT"
    )


def _add_weighted_elo_models(database: Database) -> None:
    from database.historical.tennis import TennisEloCalibration, TennisPlayerStrength
    for column in (
        "player_a_weighted_elo", "player_b_weighted_elo",
        "player_a_surface_elo", "player_b_surface_elo",
    ):
        database.execute_sql(
            f"ALTER TABLE match_history ADD COLUMN IF NOT EXISTS {column} DOUBLE PRECISION"
        )
    database.create_tables([TennisPlayerStrength, TennisEloCalibration], safe=True)


def _add_tennis_ml_experiments(database: Database) -> None:
    from database.historical.workerstate import TennisMlExperiment
    database.create_tables([TennisMlExperiment], safe=True)


def _canonical_match_history_queries(database: Database) -> None:
    """La forma reciente/H2H se deriva de MatchHistory; elimina su caché materializada."""
    database.execute_sql("CREATE INDEX IF NOT EXISTS match_history_player_a_date ON match_history (player_a_id, match_date DESC)")
    database.execute_sql("CREATE INDEX IF NOT EXISTS match_history_player_b_date ON match_history (player_b_id, match_date DESC)")
    database.execute_sql("CREATE INDEX IF NOT EXISTS match_history_players_date ON match_history (player_a_id, player_b_id, match_date DESC)")
    database.execute_sql("CREATE INDEX IF NOT EXISTS match_history_surface_date ON match_history (surface, match_date DESC)")
    database.execute_sql("DROP TABLE IF EXISTS tennis_recent_result")


def _normalize_tennis_storage(database: Database) -> None:
    """Retira caches muertos y agrega relaciones canónicas sin perder filas no resueltas."""
    database.execute_sql("ALTER TABLE match_history DROP COLUMN IF EXISTS minutes")
    database.execute_sql("ALTER TABLE match_history DROP COLUMN IF EXISTS best_of")
    database.execute_sql("ALTER TABLE tennis_match_stats_set DROP COLUMN IF EXISTS match_id")
    database.execute_sql("ALTER TABLE tennis_match_stats DROP COLUMN IF EXISTS match_id")
    database.execute_sql("ALTER TABLE tennis_historical_odds ADD COLUMN IF NOT EXISTS match_history_id INTEGER REFERENCES match_history(id) ON DELETE CASCADE")
    database.execute_sql("ALTER TABLE tennis_player_ranking ADD COLUMN IF NOT EXISTS player_id INTEGER REFERENCES tennis_player(id) ON DELETE CASCADE")
    database.execute_sql('ALTER TABLE "PlayerStats" ADD COLUMN IF NOT EXISTS player_id INTEGER REFERENCES tennis_player(id) ON DELETE CASCADE')
    database.execute_sql("""
        UPDATE tennis_player_ranking r SET player_id=p.id FROM tennis_player p
        WHERE r.player_id IS NULL AND lower(r.player_name)=lower(p.canonical_name)
    """)
    database.execute_sql("""
        UPDATE "PlayerStats" s SET player_id=p.id FROM tennis_player p
        WHERE s.player_id IS NULL AND lower(s.player_name)=lower(p.canonical_name)
    """)
    database.execute_sql("""
        UPDATE tennis_historical_odds o SET match_history_id=m.id
        FROM match_history m JOIN tennis_player a ON a.id=m.player_a_id
        JOIN tennis_player b ON b.id=m.player_b_id
        WHERE o.match_history_id IS NULL AND m.match_date=o.match_date
          AND ((lower(a.canonical_name)=lower(o.player_name) AND lower(b.canonical_name)=lower(o.opponent_name))
            OR (lower(b.canonical_name)=lower(o.player_name) AND lower(a.canonical_name)=lower(o.opponent_name)))
    """)
    database.execute_sql("CREATE INDEX IF NOT EXISTS tennis_historical_odds_match_history ON tennis_historical_odds(match_history_id)")
    database.execute_sql("CREATE INDEX IF NOT EXISTS tennis_player_ranking_player_date ON tennis_player_ranking(player_id, fetched_at DESC)")
    database.execute_sql('CREATE INDEX IF NOT EXISTS player_stats_player_surface ON "PlayerStats"(player_id, surface, fetched_at DESC)')
    # Los registros meteorológicos del tracker vivo se enlazan al partido
    # canónico cuando ambos nombres confirman exactamente. Los ambiguos se
    # conservan en el FK legacy para una reconciliación posterior.
    database.execute_sql("""
        DELETE FROM tennis_match_weather w USING tennis_scanned_match s,
          match_history h, tennis_player a, tennis_player b,
          tennis_match_weather existing
        WHERE w.match_id=s.id AND h.player_a_id=a.id AND h.player_b_id=b.id
          AND h.match_date=substring(s.close_time from 1 for 10)
          AND ((lower(a.canonical_name)=lower(s.player_a_name) AND lower(b.canonical_name)=lower(s.player_b_name))
            OR (lower(a.canonical_name)=lower(s.player_b_name) AND lower(b.canonical_name)=lower(s.player_a_name)))
          AND existing.match_history_id=h.id AND existing.phase=w.phase
    """)
    database.execute_sql("""
        UPDATE tennis_match_weather w SET match_history_id=h.id, match_id=NULL
        FROM tennis_scanned_match s, match_history h, tennis_player a, tennis_player b
        WHERE w.match_id=s.id AND h.player_a_id=a.id AND h.player_b_id=b.id
          AND h.match_date=substring(s.close_time from 1 for 10)
          AND ((lower(a.canonical_name)=lower(s.player_a_name) AND lower(b.canonical_name)=lower(s.player_b_name))
            OR (lower(a.canonical_name)=lower(s.player_b_name) AND lower(b.canonical_name)=lower(s.player_a_name)))
    """)
    for table in (
        "tennis_match_history_unvalidated", "team_head_to_head",
        "wta_player_ranking_history", "tennis_player_wikidata_reference",
        "tennis_abstract_elo",
    ):
        database.execute_sql(f'DROP TABLE IF EXISTS "{table}"')


def _link_scanned_matches_to_history(database: Database) -> None:
    database.execute_sql("""
        ALTER TABLE tennis_scanned_match
        ADD COLUMN IF NOT EXISTS match_history_id INTEGER
        REFERENCES match_history(id) ON DELETE SET NULL
    """)


def _add_retirement_metadata(database: Database) -> None:
    """Metadata verificable de la tabla Injuries de TennisExplorer."""
    for column in (
        "retirement_reason VARCHAR(255)",
        "retirement_score VARCHAR(255)",
        "injury_end_date VARCHAR(255)",
        "retirement_source VARCHAR(255)",
    ):
        database.execute_sql(f"ALTER TABLE match_history ADD COLUMN IF NOT EXISTS {column}")


def _index_hot_market_deduplication(database: Database) -> None:
    """Evita scans completos por cada trade/candle observado."""
    database.execute_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS kalshimarkettick_trade_id "
        "ON kalshimarkettick(trade_id)"
    )
    database.execute_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS cryptopricesnapshot_symbol_timestamp "
        "ON cryptopricesnapshot(symbol, timestamp)"
    )
    database.execute_sql("""
        UPDATE tennis_scanned_match s SET match_history_id=h.id
        FROM match_history h
        WHERE s.match_history_id IS NULL AND h.match_id='kalshi:' || s.market_id
    """)
    database.execute_sql("""
        CREATE INDEX IF NOT EXISTS tennis_scanned_match_match_history
        ON tennis_scanned_match(match_history_id)
    """)


def _add_flashscore_stats_orientation(database: Database) -> None:
    """Permite garantizar que stats _a/_b correspondan al MatchHistory."""
    database.execute_sql(
        "ALTER TABLE tennis_match_stats ADD COLUMN IF NOT EXISTS "
        "player_a_is_flashscore_p1 BOOLEAN"
    )
    database.execute_sql(
        "ALTER TABLE tennis_player ADD COLUMN IF NOT EXISTS "
        "flashscore_participant_id VARCHAR(255)"
    )
    database.execute_sql(
        "ALTER TABLE tennis_player ADD COLUMN IF NOT EXISTS "
        "flashscore_slug VARCHAR(255)"
    )
    database.execute_sql(
        "ALTER TABLE tennis_player ADD COLUMN IF NOT EXISTS "
        "flashscore_lookup_failed_at TIMESTAMP"
    )
    database.execute_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS tennis_player_flashscore_participant "
        "ON tennis_player(flashscore_participant_id) "
        "WHERE flashscore_participant_id IS NOT NULL"
    )
    database.execute_sql("""
        UPDATE historical_enrichment_attempt
        SET last_error = CASE
              WHEN attempt_count >= 2 THEN 'terminal: stats incompletas del proveedor'
              ELSE 'stats incompletas del proveedor'
            END,
            retry_after = CASE WHEN attempt_count >= 2
              THEN CURRENT_TIMESTAMP + INTERVAL '10 years' ELSE retry_after END
        WHERE job_name='tennis_match_stats'
          AND last_error='stats incompletas o escritura fallida'
    """)


def _invalidate_unoriented_ml_experiments(database: Database) -> None:
    """Los experimentos previos pudieron consumir stats A/B invertidas."""
    database.execute_sql("""
        UPDATE tennis_ml_experiment
        SET eligible_for_review=FALSE,
            promotion_status='invalidated_stats_orientation'
        WHERE promotion_status NOT LIKE 'invalidated_%%'
    """)


def _add_favourite_shadow_decisions(database: Database) -> None:
    database.execute_sql(
        "ALTER TABLE tennis_match_probability_tick ADD COLUMN IF NOT EXISTS "
        "market_price_b DOUBLE PRECISION"
    )
    database.execute_sql(
        "ALTER TABLE tennis_match_probability_tick ADD COLUMN IF NOT EXISTS "
        "model_fair_b DOUBLE PRECISION"
    )
    from database.historical.tennis import TennisFavouriteShadowDecision
    database.create_tables([TennisFavouriteShadowDecision], safe=True)
    database.execute_sql("""
        UPDATE historical_enrichment_attempt
        SET last_error = CASE
              WHEN attempt_count >= 2 THEN 'terminal: stats no disponibles'
              ELSE last_error
            END,
            retry_after = CASE WHEN attempt_count >= 2
              THEN CURRENT_TIMESTAMP + INTERVAL '10 years' ELSE retry_after END
        WHERE job_name='tennis_match_stats'
          AND last_error='stats no disponibles'
    """)


def _add_tennis_live_snapshots(database: Database) -> None:
    """Identidad live reutilizable y snapshots enlazados al tick exacto."""
    for column in (
        "flashscore_match_id VARCHAR(255)",
        "player_a_is_flashscore_p1 BOOLEAN",
        "flashscore_resolved_at TIMESTAMP",
    ):
        database.execute_sql(
            f"ALTER TABLE tennis_scanned_match ADD COLUMN IF NOT EXISTS {column}"
        )
    from database.historical.tennis import TennisLivePointSnapshot, TennisLiveStatsSnapshot
    database.create_tables([TennisLivePointSnapshot, TennisLiveStatsSnapshot], safe=True)


def _add_live_limit_arb_decisions(database: Database) -> None:
    from database.historical.tennis import TennisLiveLimitArbDecision
    database.create_tables([TennisLiveLimitArbDecision], safe=True)


def _add_scanned_match_qualifying_classification(database: Database) -> None:
    """Estado ternario y trazable de qualy validado por TennisExplorer."""
    for column in (
        "is_qualifying BOOLEAN",
        "qualifying_source VARCHAR(255)",
        "qualifying_checked_at TIMESTAMP",
        "qualifying_validated_at TIMESTAMP",
    ):
        database.execute_sql(
            f"ALTER TABLE tennis_scanned_match ADD COLUMN IF NOT EXISTS {column}"
        )


HISTORICAL_MIGRATIONS = (
    Migration(1, "postgresql_historical_baseline_20260815", _create_historical_schema),
    Migration(2, "add_tournament_country_20260816", _add_tournament_country),
    Migration(3, "add_historical_worker_state_20260816", _create_historical_worker_state),
    Migration(4, "consolidate_daily_ranking_snapshots_20260816", _consolidate_ranking_snapshots),
    Migration(5, "add_enrichment_attempts_and_stats_quality_20260816", _add_enrichment_attempts_and_stats_quality),
    Migration(6, "add_weighted_surface_elo_20260816", _add_weighted_elo_models),
    Migration(7, "add_tennis_ml_experiments_20260816", _add_tennis_ml_experiments),
    Migration(8, "canonical_match_history_queries_20260816", _canonical_match_history_queries),
    Migration(9, "normalize_tennis_storage_20260816", _normalize_tennis_storage),
    Migration(10, "link_scanned_matches_to_history_20260816", _link_scanned_matches_to_history),
    Migration(11, "add_retirement_metadata_20260816", _add_retirement_metadata),
    Migration(12, "index_hot_market_deduplication_20260816", _index_hot_market_deduplication),
    Migration(13, "add_flashscore_stats_orientation_20260817", _add_flashscore_stats_orientation),
    Migration(14, "invalidate_unoriented_ml_experiments_20260817", _invalidate_unoriented_ml_experiments),
    Migration(15, "add_favourite_shadow_decisions_20260817", _add_favourite_shadow_decisions),
    Migration(16, "add_tennis_live_snapshots_20260817", _add_tennis_live_snapshots),
    Migration(17, "add_live_limit_arb_decisions_20260817", _add_live_limit_arb_decisions),
    Migration(
        18, "classify_scanned_match_qualifying_20260901",
        _add_scanned_match_qualifying_classification,
    ),
)
