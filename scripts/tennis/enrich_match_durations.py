"""Pipeline de enriquecimiento POSTERIOR: duración real de partidos ya
guardados en `MatchHistory` -- 2026-08-12, pedido explícito del usuario:
"un enrich pipeline posterior con la duración del match y de los sets".

NO bloqueante, no forma parte del backfill principal (`initial_tennis_
backfill.py`): la duración viene de una fuente DISTINTA a TennisExplorer
(Flashscore, único lugar donde existe -- TennisExplorer no la expone en
absoluto, verificado exhaustivamente en vivo 2026-08-12), lo que requiere
resolver un `match_id` de Flashscore aparte por cada partido (matching
nombre+fecha+marcador real, ver `find_match_id`) -- un costo de red
completamente separado del historial principal, corrido a propósito
DESPUÉS y de forma independiente.

Idempotente: salta partidos que ya tienen `MatchHistory.duration`
resuelto -- correr esto de nuevo (ej. cron periódico, o manualmente tras
cada backfill de historial) solo avanza sobre lo que falte, nunca
reprocesa.

Fail-closed por partido: un partido que no resuelva (Flashscore no lo
tiene, matching de identidad falla, error de red) se cuenta aparte y NO
bloquea los siguientes."""

from __future__ import annotations

import time
from typing import Any

from database.db import init_db_historical
from database.historical.tennis import MatchHistory
from database.historical.tennis.models import MatchStats
from database.historical.tennis.writer import save_match_duration
from outer_apis.scrapers.tennis.flashscore_scraper import find_match_id, get_match_duration
from scripts.tennis.rotating_batch import take_rotating_batch
from database.historical.workerstate import (
    clear_enrichment_failure, enrichment_can_attempt, record_enrichment_failure,
)

_PAUSE_BETWEEN_MATCHES_S = 1.0
_batch_cursor_id = 0


def enrich_match_durations(limit: int = 200) -> dict[str, Any]:
    """Recorre `MatchHistory` (identidad de ambos jugadores ya resuelta,
    con fecha real) que todavía no tiene `duration`, resuelve el
    `match_id` real de Flashscore vía `find_match_id` (nombre+fecha+
    marcador, mismo filtro real ya usado en el resto del proyecto) y
    guarda la duración real vía `get_match_duration`.

    `limit`: tope de partidos por corrida (paginado manual sobre
    `MatchHistory.id` ascendente) -- correr de nuevo continúa donde
    quedó, ver `save_match_duration` (idempotente, nunca duplica).

    Returns: {"processed", "resolved", "not_found", "errors"} -- nunca
    excepciona hacia el caller, cada partido fallido se cuenta aparte
    sin bloquear el resto."""
    init_db_historical()

    global _batch_cursor_id

    query = (
        MatchHistory
        .select()
        .where(
            MatchHistory.duration.is_null()
            & MatchHistory.player_a.is_null(False)
            & MatchHistory.player_b.is_null(False)
            & MatchHistory.match_date.is_null(False)
        )
    )
    matches, _batch_cursor_id = take_rotating_batch(
        query, MatchHistory.id, after_id=_batch_cursor_id, limit=limit,
        cursor_key="tennis_match_durations",
    )

    processed = resolved = not_found = errors = 0
    for match in matches:
        if not enrichment_can_attempt("tennis_match_durations", match.id):
            continue
        processed += 1
        try:
            stats = MatchStats.get_or_none(
                (MatchStats.match_history == match.id)
                & MatchStats.flashscore_match_id.is_null(False)
            )
            fs_match_id = stats.flashscore_match_id if stats is not None else None
            if fs_match_id is None:
                tournament_name = match.tournament.tournament_name if match.tournament else None
                fs_match_id = find_match_id(
                    match.player_a.canonical_name, match.player_b.canonical_name,
                    match.match_date, tournament_name, expected_score=match.score,
                )
            if fs_match_id is None:
                not_found += 1
                record_enrichment_failure("tennis_match_durations", match.id, "match no encontrado")
                continue

            duration = get_match_duration(fs_match_id)
            if duration is None:
                not_found += 1
                record_enrichment_failure("tennis_match_durations", match.id, "duración no disponible")
                continue

            if save_match_duration(match.id, fs_match_id, duration):
                resolved += 1
                clear_enrichment_failure("tennis_match_durations", match.id)
            else:
                errors += 1
                record_enrichment_failure("tennis_match_durations", match.id, "escritura fallida")
        except Exception as e:
            errors += 1
            record_enrichment_failure("tennis_match_durations", match.id, str(e))
            print(f"[enrich_match_durations] error en match_id={match.match_id}: {e}")
        time.sleep(_PAUSE_BETWEEN_MATCHES_S)

    result = {"processed": processed, "resolved": resolved, "not_found": not_found, "errors": errors}
    print(f"[enrich_match_durations] {result}")
    return result


if __name__ == "__main__":
    enrich_match_durations()
