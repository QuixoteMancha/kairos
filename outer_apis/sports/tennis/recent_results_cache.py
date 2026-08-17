"""Compatibilidad: resultados recientes derivados de ``MatchHistory``."""

from __future__ import annotations

from typing import Any

from database.historical.tennis.queries import get_player_matches


def fetch_and_cache(player_name: str) -> list[dict[str, Any]]:
    return get_player_matches(player_name, limit=40)
