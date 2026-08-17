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
from database.historical.tennis.models import MatchStats as TennisMatchStats
from database.historical.tennis.models import MatchStatsSet as TennisMatchStatsSet
from database.historical.tennis.matchstats_parser import (
    set_number_from_key, typed_columns_from_stats,
)


def _now():
    return datetime.now(timezone.utc)


def stats_are_usable(stats: dict[str, Any]) -> bool:
    return any(
        typed_columns_from_stats(section or {})
        for key, section in (stats or {}).items()
        if key == "match" or set_number_from_key(key) is not None
    )


_NON_PERFORMANCE_STATS = {"Last 10 balls", "Match points saved"}


def classify_unusable_stats(stats: dict[str, Any]) -> str:
    """Distingue feed reducido del proveedor de nombres que el parser ignora."""
    names = {
        str(stat_name)
        for section in (stats or {}).values()
        if isinstance(section, dict)
        for stat_name in section
    }
    if names and not names.issubset(_NON_PERFORMANCE_STATS):
        return "parser_unrecognized"
    return "source_incomplete"


def has_match_stats(market_id: str) -> bool:
    """Fail-closed: False si falla la query (nunca fuerza un re-fetch
    accidental por asumir 'sí existe' ante un error de DB -- ante la
    duda, mejor reintentar el fetch de más que perderlo)."""
    try:
        init_db_historical()
        return TennisMatchStats.select().where(TennisMatchStats.market_id == market_id).exists()
    except Exception:
        return False


def _save_set_rows(
    stats: dict[str, Any], *,
    match_history_id: int | None = None,
    match_stats_row=None,
) -> int:
    """Crea las filas por SET (`MatchStatsSet`) de un partido.

    2026-08-16: esta tabla estaba en 0 filas pese a que el feed de
    Flashscore SÍ trae `set_1`/`set_2`/`set_3` -- simplemente nadie las
    escribía. Se vincula por `match_history` (el vínculo nuevo) o por
    `match` (el viejo, vía la fila de `MatchStats` ya creada), según de
    dónde venga el guardado.

    Fail-closed: cada set se guarda por separado, un set que falle no
    impide los demás, y nunca se relanza hacia el caller."""
    saved = 0
    for key, section in (stats or {}).items():
        set_number = set_number_from_key(key)
        if set_number is None:
            continue
        try:
            fields: dict[str, Any] = {
                "set_number": set_number,
                "raw_stats_json": json.dumps(section),
                "fetched_at": _now(),
                **typed_columns_from_stats(section),
            }
            if match_history_id is not None:
                fields["match_history"] = match_history_id
            elif match_stats_row is not None:
                continue  # sin MatchHistory canónico no se persisten sets huérfanos
            TennisMatchStatsSet.create(**fields)
            saved += 1
        except Exception:
            continue
    return saved


def save_match_stats(market_id: str, flashscore_match_id: str, stats: dict[str, Any]) -> None:
    """API legacy: los stats solo se guardan con un ``MatchHistory`` real."""
    return None


def has_match_stats_for_history(match_history_id: int) -> bool:
    """Igual que `has_match_stats` pero para partidos vinculados por
    `MatchHistory` en vez de `market_id` de Kalshi (2026-08-08, pedido
    explícito del usuario -- worker de sync que rellena stats para
    partidos de `MatchHistory` sin cobertura). Fail-closed: False si
    falla la query."""
    try:
        init_db_historical()
        return TennisMatchStats.select().where(
            (TennisMatchStats.match_history == match_history_id)
            & (TennisMatchStats.is_usable == True)
        ).exists()
    except Exception:
        return False


def remember_match_stats_source(match_history_id: int, flashscore_match_id: str) -> bool:
    """Conserva el ID resuelto aunque Flashscore aún no entregue stats."""
    try:
        init_db_historical()
        row = TennisMatchStats.get_or_none(
            TennisMatchStats.match_history == match_history_id
        )
        if row is None:
            TennisMatchStats.create(
                match_history=match_history_id,
                source="flashscore",
                market_id=f"history:{match_history_id}",
                flashscore_match_id=flashscore_match_id,
                raw_stats_json="{}",
                fetched_at=_now(),
                is_usable=False,
                invalid_reason="stats pendientes",
            )
        elif row.flashscore_match_id != flashscore_match_id:
            row.flashscore_match_id = flashscore_match_id
            row.fetched_at = _now()
            row.save(only=[TennisMatchStats.flashscore_match_id, TennisMatchStats.fetched_at])
        return True
    except Exception:
        return False


def match_stats_source_id(match_history_id: int) -> str | None:
    """Devuelve el ID de Flashscore previamente resuelto, si existe."""
    try:
        row = TennisMatchStats.get_or_none(
            TennisMatchStats.match_history == match_history_id
        )
        return row.flashscore_match_id if row is not None else None
    except Exception:
        return None


def save_match_stats_for_history_status(
    match_history_id: int, flashscore_match_id: str, stats: dict[str, Any],
) -> str:
    """Persiste stats y distingue guardado, feed incompleto y fallo DB."""
    if not stats:
        return "source_unavailable"
    try:
        init_db_historical()
        existing = TennisMatchStats.get_or_none(
            TennisMatchStats.match_history == match_history_id
        )
        if existing is not None and existing.is_usable:
            return "already_usable"
        if not stats_are_usable(stats):
            if existing is None:
                remember_match_stats_source(match_history_id, flashscore_match_id)
                existing = TennisMatchStats.get(
                    TennisMatchStats.match_history == match_history_id
                )
            existing.is_usable = False
            existing.invalid_reason = "Flashscore no entregó métricas tipables"
            existing.raw_stats_json = json.dumps(stats)
            existing.fetched_at = _now()
            existing.save()
            return classify_unusable_stats(stats)
        fields = {
            "flashscore_match_id": flashscore_match_id,
            "raw_stats_json": json.dumps(stats), "fetched_at": _now(),
            "is_usable": True, "invalid_reason": None,
            **typed_columns_from_stats(stats.get("match") or {}),
        }
        if existing is None:
            TennisMatchStats.create(
                match_history=match_history_id, source="flashscore",
                market_id=f"history:{match_history_id}", **fields,
            )
        else:
            TennisMatchStatsSet.delete().where(
                TennisMatchStatsSet.match_history == match_history_id
            ).execute()
            for key, value in fields.items():
                setattr(existing, key, value)
            existing.save()
        _save_set_rows(stats, match_history_id=match_history_id)
        return "saved"
    except Exception:
        return "database_write_failed"


def save_match_stats_for_history(match_history_id: int, flashscore_match_id: str, stats: dict[str, Any]) -> bool:
    """Igual que `save_match_stats` pero vinculando por `MatchHistory`
    (`source="flashscore"`) en vez de `market_id` de Kalshi -- ver
    docstring del módulo y `has_match_stats_for_history`. `market_id`
    sintético y único (`history:{match_history_id}`) porque la columna
    real en producción es NOT NULL pese a que el modelo la marca
    `null=True` (mismo hallazgo real que en
    `migrate_charted_stats_to_match_stats.py`).

    Returns: True si escribió una fila nueva, False si ya existía o
    falló (fail-closed, nunca duplica ni lanza)."""
    return save_match_stats_for_history_status(
        match_history_id, flashscore_match_id, stats,
    ) == "saved"


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
    prefijadas (ej. `prefix='extra'` -> "extra_match", "extra_set_1")
    -- pedido explícito del usuario 2026-08-02: complementar partidos
    que YA están trackeados (Kalshi/Flashscore) con datos de una
    segunda fuente, sin pisar ni duplicar lo que ya había.

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
