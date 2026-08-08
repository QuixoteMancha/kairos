"""Lectura/escritura de `TennisMatchStats` -- estadísticas reales de
partido (aces, 1er saque%, break points, etc.) vía Flashscore, pobladas
al resolver un partido trackeado (pedido explícito del usuario
2026-08-01). Separado de `matchtracking.py` porque ese módulo es
solo-DB puro y este necesita el shape de dict que produce
`outer_apis/scrapers/tennis/flashscore_scraper.py::get_match_statistics`
(consistente con la separación DB/scraping del resto del paquete).

Fail-closed por diseño, mismo criterio que el resto del paquete:
cualquier excepción de DB se traga (logueada, no relanzada)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from database.db import init_db_historical
from database.historical.match_models import MatchStats as TennisMatchStats


def _now():
    return datetime.now(timezone.utc)


def has_match_stats(market_id: str) -> bool:
    """Fail-closed: False si falla la query (nunca fuerza un re-fetch
    accidental por asumir 'sí existe' ante un error de DB -- ante la
    duda, mejor reintentar el fetch de más que perderlo)."""
    try:
        init_db_historical()
        return TennisMatchStats.select().where(TennisMatchStats.market_id == market_id).exists()
    except Exception:
        return False


def save_match_stats(market_id: str, flashscore_match_id: str, stats: dict[str, Any]) -> None:
    """Fail-closed: no hace nada si `stats` está vacío (nunca guarda una
    fila con estadísticas fantasma) o si falla la escritura."""
    if not stats:
        return
    try:
        init_db_historical()
        TennisMatchStats.create(
            market_id=market_id, flashscore_match_id=flashscore_match_id,
            raw_stats_json=json.dumps(stats), fetched_at=_now(),
        )
    except Exception:
        pass


def get_match_stats(market_id: str) -> dict[str, Any] | None:
    """Fail-closed: None si no hay estadísticas guardadas para este
    partido o falla la query."""
    try:
        init_db_historical()
        row = TennisMatchStats.get_or_none(TennisMatchStats.market_id == market_id)
        if row is None:
            return None
        return json.loads(row.raw_stats_json)
    except Exception:
        return None


def merge_match_stats(market_id: str, extra_stats: dict[str, Any], prefix: str) -> bool:
    """Enriquece la fila YA EXISTENTE de `market_id` con `extra_stats`
    (mismo shape {"match": {...}, "set_1": {...}}) bajo claves
    prefijadas (ej. `prefix='sackmann'` -> "sackmann_match",
    "sackmann_set_1") -- pedido explícito del usuario 2026-08-02:
    complementar partidos que YA están trackeados (Kalshi/Flashscore) con
    data del Match Charting Project, sin pisar ni duplicar lo que ya
    había.

    Returns: True si se actualizó, False si `market_id` no existe
    todavía (el caller debe entonces usar `save_match_stats` para
    crearla, o -- para partidos que Kalshi nunca vio -- usar el modelo
    `ChartedMatch` en vez de este). Fail-closed: False también ante
    cualquier excepción de DB."""
    if not extra_stats:
        return False
    try:
        init_db_historical()
        row = TennisMatchStats.get_or_none(TennisMatchStats.market_id == market_id)
        if row is None:
            return False
        current = json.loads(row.raw_stats_json)
        for key, value in extra_stats.items():
            current[f"{prefix}_{key}"] = value
        row.raw_stats_json = json.dumps(current)
        row.save()
        return True
    except Exception:
        return False
