"""Edad real por jugador/a, cache-permanente DB-first (pedido explícito
del usuario 2026-08-01, para el índice de fatiga -- ver
`outer_apis/sports/tennis/fatigue.py`). Fuente: Tennis Abstract
(`tennisabstract_player_scraper.get_date_of_birth`), NO el `atp_scraper`
que tiene su propia extracción de edad pero solo para ATP y nunca
persistida (ver auditoría 2026-08-01) -- Tennis Abstract cubre ambos
tours con el mismo formato.

Fail-closed en cada capa: sin dato real, `get_age`/`get_date_of_birth`
devuelven None, nunca fabrican una edad."""

from __future__ import annotations

from datetime import date, datetime, timezone

from database.db import init_db_historical
from database.historical.tennis.tennisplayerbio import TennisPlayerBio
from outer_apis.scrapers.tennis.tennisabstract_player_scraper import get_date_of_birth as _fetch_dob


def _now():
    return datetime.now(timezone.utc)


def get_date_of_birth_cached(player_name: str) -> date | None:
    """DB-first, cache PERMANENTE (la fecha de nacimiento no cambia).
    Fail-closed: None si nunca se pudo obtener un dato real."""
    try:
        init_db_historical()
        row = TennisPlayerBio.get_or_none(TennisPlayerBio.player_name == player_name)
        if row is not None:
            return row.date_of_birth
    except Exception:
        pass

    dob_iso = _fetch_dob(player_name)
    if dob_iso is None:
        return None
    try:
        dob = date.fromisoformat(dob_iso)
    except ValueError:
        return None

    try:
        init_db_historical()
        TennisPlayerBio.create(player_name=player_name, date_of_birth=dob, fetched_at=_now())
    except Exception:
        pass  # el fetch en sí fue exitoso, solo falló guardarlo -- se devuelve igual

    return dob


def get_age(player_name: str, as_of: date | None = None) -> int | None:
    """Edad en años enteros al día `as_of` (default: hoy). Fail-closed:
    None si no hay fecha de nacimiento real disponible."""
    dob = get_date_of_birth_cached(player_name)
    if dob is None:
        return None
    ref = as_of or date.today()
    age = ref.year - dob.year
    if (ref.month, ref.day) < (dob.month, dob.day):
        age -= 1
    return age
