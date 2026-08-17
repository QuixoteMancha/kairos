"""Repara incrementalmente stats legacy cuya orientación P1/P2 era desconocida."""

from __future__ import annotations

import time
from typing import Any

from database.db import init_db_historical
from database.historical.tennis.matchstats import apply_match_stats_orientation
from database.historical.tennis.models import MatchStats
from database.historical.workerstate import (
    clear_enrichment_failure, enrichment_can_attempt, record_enrichment_failure,
)
from outer_apis.scrapers.tennis.flashscore_scraper import find_match_identity


def repair_match_stats_orientation(limit: int = 25) -> dict[str, Any]:
    init_db_historical()
    rows = list(
        MatchStats.select()
        .where(
            (MatchStats.is_usable == True)
            & MatchStats.match_history.is_null(False)
            & MatchStats.player_a_is_flashscore_p1.is_null(True)
        )
        .order_by(MatchStats.id)
        .limit(limit)
    )
    result = {"processed": 0, "repaired": 0, "already_aligned": 0, "failed": 0}
    for row in rows:
        if not enrichment_can_attempt("tennis_stats_orientation", row.id):
            continue
        result["processed"] += 1
        try:
            history = row.match_history
            identity = find_match_identity(
                history.player_a.canonical_name,
                history.player_b.canonical_name,
                history.match_date or "",
                history.tournament.tournament_name if history.tournament else None,
                expected_score=history.score,
            )
            if not identity or identity["match_id"] != row.flashscore_match_id:
                result["failed"] += 1
                record_enrichment_failure(
                    "tennis_stats_orientation", row.id,
                    "no se confirmó identidad/orientación del match",
                )
                continue
            is_p1 = identity["player_a_is_p1"]
            if apply_match_stats_orientation(history.id, is_p1):
                result["repaired" if not is_p1 else "already_aligned"] += 1
                clear_enrichment_failure("tennis_stats_orientation", row.id)
            else:
                result["failed"] += 1
                record_enrichment_failure(
                    "tennis_stats_orientation", row.id, "falló actualización DB",
                )
        except Exception as exc:
            result["failed"] += 1
            record_enrichment_failure(
                "tennis_stats_orientation", row.id,
                f"{type(exc).__name__}: {exc}",
            )
        time.sleep(0.5)
    return result


if __name__ == "__main__":
    print(repair_match_stats_orientation(), flush=True)
