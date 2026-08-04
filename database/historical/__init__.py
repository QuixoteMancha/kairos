"""
Historical/reference data (`db_historical`, `DB_HISTORICAL_PATH`) — feeds
model calibration, separate from database/accounting/ (operational data,
`db`/`DB_PATH`). See database/db.py docstring for the two-database split.

One folder per topic, same convention as models/volatility/sports/ and
models/fairprice/sports/:
  - market_data/  raw Kalshi ticks + crypto OHLCV (rescued, no live writer yet)
  - team_sports/  TeamRating/TeamMatchHistory/TeamHeadToHead, all team sports
                  (TeamMatchHistory es una VENTANA RODANTE de 45 días
                  para Elo en vivo, NO un histórico profundo -- ver
                  baseball/ abajo para el histórico real de MLB)
  - tennis/       TennisPlayerRanking/TennisServeCalibration/TennisMatchHistory/TennisHeadToHead
  - baseball/     MlbGameHistory -- histórico REAL profundo de MLB
                  (2026-08-04), análogo a TmlDatabaseMatch de tenis,
                  poblado solo por scripts/mlb_local_backfill.py
"""
