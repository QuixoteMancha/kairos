"""Puebla `AtpTournamentLevel` -- catálogo persistente torneo ATP real
-> nivel, mismo patrón que `wta_tournament_level_backfill.py` pero
derivado de Sackmann (`MatchHistory` `source="sackmann"`) en vez de una
API externa -- 2026-08-09, pedido explícito del usuario: "hay que tener
un mapa de torneos, que indique el level".

Voto por MAYORÍA real por (año, torneo normalizado), no prioridad
máxima -- bug real encontrado en vivo: "Washington" 2025 tenía 43 filas
reales '500' + 1 fila mal etiquetada 'M' en la fuente TML-Database
misma; prioridad-máxima elegía la fila mala, mayoría real elige
correcto."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from database.db import init_db_historical
from database.historical.tennis.models import AtpTournamentLevel, MatchHistory

_DROP_TOKENS = {"WTA", "ITF", "CHALLENGER"}

_ATP_LEVEL_PRIORITY = {"G": 6, "F": 5, "M": 4, "500": 3, "250": 2, "D": 1, "A": 0}


def _normalize_tournament_name(raw: str | None) -> str | None:
    if not raw:
        return None
    tokens = [t for t in raw.strip().upper().split(" ") if t]
    kept = [t for t in tokens if t not in _DROP_TOKENS and not t.isdigit()]
    if not kept:
        return None
    return " ".join(kept)


def compute_level_by_majority_vote() -> dict[tuple[int, str], str]:
    """{(year, normalized_tournament_name): level} -- voto por mayoría
    sobre las filas reales `source="sackmann"` de `MatchHistory`."""
    init_db_historical()
    counts: dict[tuple[int, str], Counter[str]] = {}
    rows = (
        MatchHistory
        .select(MatchHistory.tournament, MatchHistory.level, MatchHistory.match_date)
        .where(
            (MatchHistory.source == "sackmann")
            & (MatchHistory.tournament.is_null(False))
            & (MatchHistory.level.is_null(False))
        )
    )
    for r in rows:
        norm = _normalize_tournament_name(r.tournament)
        if norm is None or not r.match_date:
            continue
        try:
            year = int(r.match_date[:4])
        except (ValueError, TypeError):
            continue
        key = (year, norm)
        counts.setdefault(key, Counter())[r.level] += 1

    result: dict[tuple[int, str], str] = {}
    for key, level_counts in counts.items():
        max_count = max(level_counts.values())
        tied = [lvl for lvl, n in level_counts.items() if n == max_count]
        result[key] = max(tied, key=lambda lvl: _ATP_LEVEL_PRIORITY.get(lvl, -1)) if len(tied) > 1 else tied[0]
    return result


def run_backfill() -> dict[str, Any]:
    """Recalcula el voto por mayoría y lo persiste en `AtpTournamentLevel`
    (upsert por (year, tournament_name) -- idempotente, correr de nuevo
    actualiza con los datos más recientes de `MatchHistory`, nunca
    duplica)."""
    init_db_historical()
    computed = compute_level_by_majority_vote()
    now = datetime.now(timezone.utc)

    inserted = updated = unchanged = 0
    for (year, tournament_name), level in computed.items():
        existing = AtpTournamentLevel.get_or_none(
            (AtpTournamentLevel.year == year) & (AtpTournamentLevel.tournament_name == tournament_name)
        )
        if existing is None:
            AtpTournamentLevel.create(year=year, tournament_name=tournament_name, level=level, fetched_at=now)
            inserted += 1
        elif existing.level != level:
            existing.level = level
            existing.fetched_at = now
            existing.save()
            updated += 1
        else:
            unchanged += 1

    return {"tournaments_total": len(computed), "inserted": inserted, "updated": updated, "unchanged": unchanged}


if __name__ == "__main__":
    print(run_backfill())
