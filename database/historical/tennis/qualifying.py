"""Clasificación de qualys para partidos de Kalshi usando TennisExplorer.

Kalshi suele publicar rondas genéricas (por ejemplo ``Round of 128``), que
no distinguen de forma segura un cuadro principal de una clasificación. La
decisión sólo se guarda cuando existe el partido canónico de TennisExplorer.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from database.historical.tennis.models import Match as TennisScannedMatch
    from database.historical.tennis.models import MatchHistory


_SYNTHETIC_PREFIXES = ("kalshi:", "recentform:")
_QUALIFYING_RE = re.compile(r"\bqual(?:ification|ifying|ifier|ifiers|\.)?\b", re.IGNORECASE)


def _normalized(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    return " ".join(
        re.sub(r"[^a-z0-9]+", " ", text.encode("ascii", "ignore").decode().lower()).split()
    )


def classify_tennisexplorer_history(history: MatchHistory | None) -> tuple[bool | None, str | None]:
    """Devuelve (clasificación, evidencia), sin inferir desde datos sintéticos."""
    if history is None or history.match_id.lower().startswith(_SYNTHETIC_PREFIXES):
        return None, None

    round_name = history.round or ""
    tournament_name = history.tournament.tournament_name if history.tournament_id else ""
    if _QUALIFYING_RE.search(round_name):
        return True, "tennisexplorer_round"
    if _QUALIFYING_RE.search(tournament_name):
        return True, "tennisexplorer_tournament"
    if round_name.strip():
        return False, "tennisexplorer_round"
    return None, None


def classify_qualifying_labels(
    round_name: str | None, tournament_name: str | None,
) -> bool | None:
    """Clasifica etiquetas ya obtenidas del partido real de TennisExplorer."""
    if _QUALIFYING_RE.search(round_name or "") or _QUALIFYING_RE.search(tournament_name or ""):
        return True
    if (round_name or "").strip():
        return False
    return None


def find_tennisexplorer_history(match: TennisScannedMatch) -> MatchHistory | None:
    """Encuentra un único partido real por jugadores+fecha; falla ante ambigüedad."""
    from database.historical.tennis.matchtracking import resolve_scanned_match_date
    from database.historical.tennis.models import MatchHistory
    from database.historical.tennis.writer import _lookup_player_id

    player_a_id = _lookup_player_id(match.player_a_name)
    player_b_id = _lookup_player_id(match.player_b_name)
    match_date = resolve_scanned_match_date(match.market_id, match.close_time)
    if player_a_id is None or player_b_id is None or match_date is None:
        return None

    candidates = list(
        MatchHistory.select()
        .where(
            (MatchHistory.match_date == match_date)
            & (
                ((MatchHistory.player_a == player_a_id) & (MatchHistory.player_b == player_b_id))
                | ((MatchHistory.player_a == player_b_id) & (MatchHistory.player_b == player_a_id))
            )
            & ~MatchHistory.match_id.startswith("kalshi:")
            & ~MatchHistory.match_id.startswith("recentform:")
        )
    )
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) <= 1 or not match.tournament:
        return None

    expected = _normalized(match.tournament)
    tournament_matches = [
        candidate for candidate in candidates
        if candidate.tournament_id
        and _normalized(candidate.tournament.tournament_name) == expected
    ]
    return tournament_matches[0] if len(tournament_matches) == 1 else None


def backfill_qualifying_status(match: TennisScannedMatch) -> bool | None:
    """Vincula el historial canónico y persiste True/False; None queda reintentable."""
    from database.historical.tennis.models import Match as TennisScannedMatch
    from database.historical.tennis.models import MatchHistory

    checked_at = datetime.now(timezone.utc)
    history = match.match_history
    classification, source = classify_tennisexplorer_history(history)
    if classification is None:
        history = find_tennisexplorer_history(match)
        classification, source = classify_tennisexplorer_history(history)
    if classification is None or history is None:
        match.qualifying_checked_at = checked_at
        match.save(only=[TennisScannedMatch.qualifying_checked_at])
        return None

    if history.is_qualifying != classification:
        history.is_qualifying = classification
        history.save(only=[MatchHistory.is_qualifying])
    match.match_history = history
    match.is_qualifying = classification
    match.qualifying_source = source
    match.qualifying_checked_at = checked_at
    match.qualifying_validated_at = checked_at
    match.save(only=[
        TennisScannedMatch.match_history,
        TennisScannedMatch.is_qualifying,
        TennisScannedMatch.qualifying_source,
        TennisScannedMatch.qualifying_checked_at,
        TennisScannedMatch.qualifying_validated_at,
    ])
    return classification
