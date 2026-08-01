"""Señal de riesgo de lesión, vía retiros/no-presentaciones reales
recientes -- pedido explícito del usuario 2026-07-31, caso real que lo
motivó: Svitolina jugando con una lesión de cadera real (walkover
confirmado el 2026-06-21 en Bad Homburg, retirada de otro torneo por lo
mismo a fines de junio).

Fuente: Tennis Abstract, sección `recent-results` de cada jugador (ver
`outer_apis/scrapers/tennis/tennisabstract_player_scraper.py::get_recent_results`).
DB-first, frescura POR JUGADOR (mismo patrón que el resto de
outer_apis/sports/tennis/*).

NO es un blend probabilístico como los demás (no hay forma honesta de
calibrar "cuántos puntos porcentuales vale un walkover de hace 3
semanas" sin datos reales de cuántos partidos post-lesión se ganan/
pierden) -- es un FLAG binario con un descuento fijo, chico y
documentado como heurística, no como algo calibrado."""

from __future__ import annotations

from datetime import date
from typing import Any

from database.historical.tennis.writer import get_recent_results, save_recent_results
from outer_apis.scrapers.tennis import tennisabstract_player_scraper

INJURY_LOOKBACK_DAYS = 45  # ventana en la que un retiro/walkover real se considera "riesgo vigente"


def _fetch_and_cache(player_name: str) -> list[dict[str, Any]]:
    cached = get_recent_results(player_name)
    if cached is not None:
        return cached

    try:
        rows = tennisabstract_player_scraper.get_recent_results(player_name)
    except Exception:
        rows = []
    if rows:
        save_recent_results(player_name, rows)
    return rows


def get_injury_risk_flag(player_name: str, lookback_days: int = INJURY_LOOKBACK_DAYS) -> dict[str, Any] | None:
    """True/False de si `player_name` tuvo un retiro/no-presentación REAL
    (no del rival) dentro de los últimos `lookback_days`.

    Returns:
        {"flagged": bool, "date": str | None, "opponent": str | None,
         "tournament": str | None} -- el partido más reciente marcado, si
        hay más de uno.
        Fail-closed: None si no hay datos reales del jugador (nunca
        asume "sin riesgo" por ausencia de información, distinto de
        {"flagged": False, ...} que sí es una respuesta real de "no hay
        evidencia de lesión reciente")."""
    rows = _fetch_and_cache(player_name)
    if not rows:
        return None

    cutoff = date.today().toordinal() - lookback_days
    flagged_rows = [
        r for r in rows
        if r.get("retired_or_walkover") and r.get("date")
        and date.fromisoformat(r["date"]).toordinal() >= cutoff
    ]

    if not flagged_rows:
        return {"flagged": False, "date": None, "opponent": None, "tournament": None}

    most_recent = max(flagged_rows, key=lambda r: r["date"])
    return {
        "flagged": True, "date": most_recent["date"],
        "opponent": most_recent.get("opponent"), "tournament": most_recent.get("tournament"),
    }
