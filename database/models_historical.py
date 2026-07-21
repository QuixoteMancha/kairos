"""
Peewee models for the HISTORICAL/reference database (`db_historical`,
`DB_HISTORICAL_PATH` — see database/db.py docstring). Rescued from the
pre-"extreme makeover" architecture's Peewee schema, VERBATIM column
definitions (same types/names/nullability), confirmed against the real
production data still living in the mini-pc's `kairos_kairos_data` volume
(inspected directly via SSH 2026-07-21, not guessed).

Only the 3 tables that had REAL data are ported here — the other ~37
tables from the old schema were confirmed EMPTY (never populated, e.g.
Player/PlayerElo/H2HRecord/RankingHistory/Match/Tournament — the old
system's tennis calibration pipeline was dead, see kairos-tennis-data-
source-jeffsackmann-dead memory) or platform-mismatched (crypto_5m_interval/
FillSimulation referenced Polymarket condition_ids/platform, which was
removed from this project irreversibly) and were deliberately NOT ported.

NOTE: this only defines the schema + a connection. No collector/worker
writes to these tables yet in this rebuilt codebase — that's future work.
"""

from peewee import (
    CharField, DateTimeField, FloatField, IntegerField, Model, TextField,
)

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class KalshiMarketTick(HistoricalBaseModel):
    """Real per-trade ticks from Kalshi crypto_15m markets. 11.5M+ real
    rows already in the rescued volume (BTC/ETH/SOL/XRP/DOGE/BNB/ZEC/
    NEAR/HYPE, 2026-07-11 onward) — useful for volatility/price-path
    calibration of short-duration crypto markets."""

    class Meta:
        table_name = "kalshimarkettick"

    created_at = DateTimeField()
    updated_at = DateTimeField()
    trade_id = CharField()
    ticker = CharField()
    event_ticker = CharField(null=True)
    created_time = DateTimeField()
    yes_price = FloatField(null=True)
    no_price = FloatField(null=True)
    count = FloatField(null=True)
    taker_side = CharField(null=True)
    fetched_at = DateTimeField()


class CryptoPriceSnapshot(HistoricalBaseModel):
    """Real OHLCV candles, 11 crypto symbols, ~3 weeks of history already
    in the rescued volume — underlying-asset price reference for
    crypto_15m calibration (distinct from the Kalshi-side tick prices in
    KalshiMarketTick)."""

    class Meta:
        table_name = "cryptopricesnapshot"

    created_at = DateTimeField()
    updated_at = DateTimeField()
    symbol = CharField()
    timestamp = DateTimeField()
    open = FloatField()
    high = FloatField()
    low = FloatField()
    close = FloatField()
    volume = FloatField()


class TeamRating(HistoricalBaseModel):
    """Real football/soccer team Elo ratings (source: ClubElo), 788 real
    rows already in the rescued volume, multi-league. Reference for a
    future soccer fair-price model (queued, not built yet — see
    kairos-tennis-real-calibration-both-tours session for the tennis
    equivalent pattern this would follow).

    Column names/types verified against the real DDL on the mini-pc
    (`PRAGMA`/`sqlite_master.sql`, 2026-07-21) — NOT guessed from a
    sample row (a first attempt at this model got `league`/`elo`/`name`
    wrong by guessing from row values; corrected before use)."""

    class Meta:
        table_name = "teamrating"

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
