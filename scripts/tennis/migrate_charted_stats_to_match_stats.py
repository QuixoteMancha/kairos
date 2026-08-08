"""Migra `ChartedMatchStats` (Match Charting Project, 7,730 filas
reales) a la `MatchStats` fusionada, vinculadas por FK a `MatchHistory`
-- 2026-08-08, pedido explícito del usuario ("dónde estarían las
stats?" -> acordado: 1 tabla `MatchStats`, FK a `MatchHistory`).

Alcance ACOTADO a propósito (ver conversación): NO toca las 66 filas
reales de `MatchStats` ya vinculadas a `Match` (el tracker vivo de
Kalshi, Categoría B) -- no hay vínculo cierto entre esas y
`MatchHistory` sin el mismo matching fuzzy que se viene evitando toda
la sesión. Esto SOLO mueve Charting Project.

IDENTIDAD: `ChartedMatch` ya trae nombre completo real de ambos lados +
`match_date` EXACTO (a diferencia de `TmlDatabaseMatch`, que solo tiene
la fecha de inicio del torneo) -- se resuelve contra `MatchHistory` por
(par de nombres exacto, sin importar orden) + fecha más cercana dentro
de una tolerancia (`_DATE_TOLERANCE_DAYS`, cubre el caso de filas
`source="sackmann"` en `MatchHistory` que todavía tienen la fecha
aproximada de inicio de torneo en vez de la fecha real). Fail-closed:
si hay 2+ candidatos dentro de la tolerancia, no se adivina, se cuenta
como "ambiguous" y se deja sin migrar (no se descarta el dato original,
`ChartedMatchStats` sigue intacta)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

_DATE_TOLERANCE_DAYS = 14  # cubre partidos en MatchHistory con fecha aproximada de inicio de torneo (source=sackmann)


def _parse_date(raw) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def build_match_history_index(rows: list[dict]) -> dict:
    """rows: dicts con id/player_a_name/player_b_name/match_date (ISO
    string o date). Returns {frozenset({a.lower(), b.lower()}): [(id, date), ...]}"""
    index: dict[frozenset, list] = defaultdict(list)
    for r in rows:
        d = _parse_date(r["match_date"])
        if d is None:
            continue
        key = frozenset({r["player_a_name"].lower(), r["player_b_name"].lower()})
        index[key].append((r["id"], d))
    return dict(index)


def resolve_match_history_id(player_a: str, player_b: str, charted_date: date, index: dict) -> tuple[int | None, str]:
    """Returns (match_history_id o None, reason) -- reason: "resolved" |
    "no_candidate" | "ambiguous"."""
    candidates = index.get(frozenset({player_a.lower(), player_b.lower()}), [])
    within_tolerance = [
        (mh_id, d) for mh_id, d in candidates if abs((d - charted_date).days) <= _DATE_TOLERANCE_DAYS
    ]
    if not within_tolerance:
        return None, "no_candidate"
    if len(within_tolerance) == 1:
        return within_tolerance[0][0], "resolved"
    # 2+ candidatos dentro de tolerancia -- el más cercano en fecha, PERO
    # solo si es inequívocamente el más cercano (nunca adivinar en empate).
    within_tolerance.sort(key=lambda t: abs((t[1] - charted_date).days))
    best_diff = abs((within_tolerance[0][1] - charted_date).days)
    tied = [c for c in within_tolerance if abs((c[1] - charted_date).days) == best_diff]
    if len(tied) == 1:
        return tied[0][0], "resolved"
    return None, "ambiguous"


def run_migration() -> dict:
    from database.db import init_db_historical, db_historical
    from database.historical.tennis import ChartedMatch, ChartedMatchStats, MatchHistory
    from database.historical.tennis import TennisMatchStats as MatchStats

    init_db_historical()
    with db_historical.connection_context():
        mh_rows = [
            {"id": r.id, "player_a_name": r.player_a_name, "player_b_name": r.player_b_name,
             "match_date": r.match_date}
            for r in MatchHistory.select(MatchHistory.id, MatchHistory.player_a_name,
                                          MatchHistory.player_b_name, MatchHistory.match_date)
        ]
        charted = [
            {"stats_id": s.id, "match_id": s.match_id, "raw_stats_json": s.raw_stats_json,
             "player_a_name": s.match.player_a_name, "player_b_name": s.match.player_b_name,
             "match_date": s.match.match_date}
            for s in ChartedMatchStats.select().join(ChartedMatch)
        ]

    index = build_match_history_index(mh_rows)
    now = datetime.now(timezone.utc)
    created = already_linked = no_candidate = ambiguous = 0

    with db_historical.connection_context():
        for c in charted:
            charted_date = _parse_date(c["match_date"])
            if charted_date is None:
                no_candidate += 1
                continue
            mh_id, reason = resolve_match_history_id(c["player_a_name"], c["player_b_name"], charted_date, index)
            if mh_id is None:
                if reason == "ambiguous":
                    ambiguous += 1
                else:
                    no_candidate += 1
                continue
            if MatchStats.select().where(
                (MatchStats.match_history == mh_id) & (MatchStats.source == "sackmann_match_charting_project")
            ).exists():
                already_linked += 1
                continue
            MatchStats.create(
                match_history=mh_id, source="sackmann_match_charting_project",
                raw_stats_json=c["raw_stats_json"], fetched_at=now,
            )
            created += 1

    summary = {
        "charted_stats_total": len(charted), "created": created, "already_linked": already_linked,
        "no_candidate": no_candidate, "ambiguous": ambiguous,
    }
    print(f"[migrate_charted_stats_to_match_stats] {summary}", flush=True)
    return summary


if __name__ == "__main__":
    import json
    print(json.dumps(run_migration(), indent=2, ensure_ascii=False))
