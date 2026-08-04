"""Histórico REAL de MLB (`db_historical`) -- 2026-08-04, pedido
explícito del usuario. `MlbGameHistory` es análogo a `TmlDatabaseMatch`
(tenis): 1 fila = 1 partido real, poblado SOLO por
`scripts/mlb_local_backfill.py` (fetch de red deliberado). Distinto de
`database.historical.team_sports.TeamMatchHistory`, que es una ventana
rodante de 45 días para el Elo en vivo, no un histórico."""

from database.historical.baseball.mlbgamehistory import MlbGameHistory

__all__ = ["MlbGameHistory"]
