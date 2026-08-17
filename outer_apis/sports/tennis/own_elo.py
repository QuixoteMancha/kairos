"""Lectura del Elo propio materializado por ``compute_weighted_elo``."""

from __future__ import annotations

from database.db import init_db_historical
from database.historical.tennis.models import Player, TennisPlayerStrength
from outer_apis.sports.tennis.tennisabstract_elo import elo_implied_win_prob


def get_elo(player_name: str, tour: str, surface: str | None = None) -> float | None:
    """Elo actual general o de superficie, sin red y con identidad canónica."""
    try:
        init_db_historical()
        player = Player.select().where(Player.canonical_name ** player_name.strip()).first()
        if player is None:
            return None
        strength = TennisPlayerStrength.get_or_none(
            (TennisPlayerStrength.player == player.id) & (TennisPlayerStrength.tour == tour)
        )
        if strength is None:
            return None
        field = {"Hard": "hard_elo", "Clay": "clay_elo", "Grass": "grass_elo"}.get(surface or "")
        value = getattr(strength, field) if field else None
        return value if value is not None else strength.overall_elo
    except Exception:
        return None


__all__ = ["get_elo", "elo_implied_win_prob"]
