"""Import de partidos del Match Charting Project (2026-08-02, pedido
explícito del usuario), con matching contra `TennisScannedMatch` para
COMPLEMENTAR (no duplicar) partidos que ya tenemos trackeados vía Kalshi/
Flashscore.

Separado de `matchtracking.py` (ese es solo-DB para el dominio Kalshi) y
de `matchstats.py` (ese lee/escribe `TennisMatchStats`, que este módulo
SÍ usa para el caso de solapamiento, vía `merge_match_stats`)."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any

from database.db import init_db_historical
from database.historical.models import Match as TennisScannedMatch
from database.historical.tennis.models import ChartedMatch, ChartedMatchStats
from database.historical.tennis.matchstats import merge_match_stats, save_match_stats, has_match_stats


def _norm_name(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]", " ", ascii_str.lower()).strip()


def find_overlapping_scanned_match(
    player_a: str, player_b: str, match_date: date,
) -> TennisScannedMatch | None:
    """Busca un `TennisScannedMatch` YA registrado (vía Kalshi) para el
    mismo partido -- nombres normalizados en CUALQUIER orden + fecha con
    tolerancia de +/-1 día (mismo margen que `find_match_id`, por husos
    horarios). Fail-closed: None si no hay match o falla la query -- en
    ese caso el caller debe crear un `ChartedMatch` nuevo en vez de
    enriquecer algo inexistente."""
    try:
        init_db_historical()
        cutoff_low = datetime.combine(match_date - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        cutoff_high = datetime.combine(match_date + timedelta(days=1), datetime.max.time(), tzinfo=timezone.utc)
        candidates = TennisScannedMatch.select().where(
            TennisScannedMatch.detected_at.between(cutoff_low, cutoff_high)
        )
        a_norm, b_norm = _norm_name(player_a), _norm_name(player_b)
        for row in candidates:
            row_a, row_b = _norm_name(row.player_a_name), _norm_name(row.player_b_name)
            if {a_norm, b_norm} == {row_a, row_b}:
                return row
        return None
    except Exception:
        return None


def save_charted_match(entry: dict[str, Any], winner_side: int | None, tour: str) -> ChartedMatch | None:
    """Idempotente por `match_id`. Fail-closed: None si falla la
    escritura (nunca lanza, el import debe seguir con el resto)."""
    try:
        init_db_historical()
        existing = ChartedMatch.get_or_none(ChartedMatch.match_id == entry["match_id"])
        if existing is not None:
            return existing

        winner = None
        if winner_side == 1:
            winner = "p1"
        elif winner_side == 2:
            winner = "p2"

        return ChartedMatch.create(
            match_id=entry["match_id"], tour=tour,
            player_a_name=entry["player1"], player_b_name=entry["player2"],
            match_date=entry["date"], tournament=entry.get("tournament"),
            round=entry.get("round"), surface=entry.get("surface"),
            best_of=entry.get("best_of"), winner=winner,
        )
    except Exception:
        return None


def save_charted_match_stats(charted_match: ChartedMatch, stats: dict[str, Any]) -> bool:
    """Idempotente por `match` (unique). Fail-closed: False si `stats`
    vacío o falla la escritura."""
    if not stats:
        return False
    try:
        import json
        init_db_historical()
        if ChartedMatchStats.select().where(ChartedMatchStats.match == charted_match).exists():
            return False
        ChartedMatchStats.create(match=charted_match, raw_stats_json=json.dumps(stats))
        return True
    except Exception:
        return False


def import_or_merge_match(entry: dict[str, Any], winner_side: int | None, tour: str,
                           stats: dict[str, Any]) -> dict[str, Any]:
    """Punto de entrada del import por partido: si ya existe un
    `TennisScannedMatch` real para este jugador+fecha (partido que
    Kairos SÍ vio en Kalshi), enriquece su `TennisMatchStats` con
    `stats` (prefijo `sackmann`) en vez de crear un `ChartedMatch`
    duplicado. Si no, crea el `ChartedMatch` nuevo (caso mayoritario,
    dado que Kalshi solo retiene ~2 meses vs. los ~10 años de este
    import)."""
    overlap = find_overlapping_scanned_match(entry["player1"], entry["player2"], date.fromisoformat(entry["date"]))
    if overlap is not None:
        if has_match_stats(overlap.market_id):
            merged = merge_match_stats(overlap.market_id, stats, prefix="sackmann")
            return {"mode": "merged_existing", "market_id": overlap.market_id, "merged": merged}
        saved = False
        if stats:
            save_match_stats(overlap.market_id, "", {f"sackmann_{k}": v for k, v in stats.items()})
            saved = True
        return {"mode": "created_stats_for_existing", "market_id": overlap.market_id, "saved": saved}

    charted = save_charted_match(entry, winner_side, tour)
    if charted is None:
        return {"mode": "error", "match_id": entry["match_id"]}
    stats_saved = save_charted_match_stats(charted, stats)
    return {"mode": "created_new", "match_id": entry["match_id"], "stats_saved": stats_saved}
