"""
Persists real tennis data (already fetched by outer_apis/tennis_*.py) into the
historical DB (database/historical/tennis/ models) — called from those
facades on a FRESH fetch only (not on every in-process cache hit, to
avoid flooding the DB with redundant snapshots of unchanged data).

Fail-closed by design: every function here catches and swallows its own
exceptions (logged, not raised) — persistence is a side effect, a DB
hiccup must never break the caller's actual data path (a scan needing
real-time rankings/calibration/match-history shouldn't fail just because
writing the historical record failed).
"""

from datetime import datetime, timezone
from typing import Any

from database.db import init_db_historical
from database.historical.tennis.tennisplayerranking import TennisPlayerRanking
from database.historical.tennis.tennisservecalibration import TennisServeCalibration
from database.historical.tennis.tennismatchhistory import TennisMatchHistory
from database.historical.tennis.tennisheadtohead import TennisHeadToHead
from database.historical.tennis.tennissurfacematchhistory import TennisSurfaceMatchHistory


def _now():
    return datetime.now(timezone.utc)


def save_rankings_snapshot(tour: str, players: list[dict[str, Any]]) -> None:
    """One new row per player, per fresh fetch -- deliberately NOT
    deduped: this is a time series (ranking history), each fetch is a
    real new data point. Bounded naturally by the 1h cache TTL in
    outer_apis/tennis_rankings.py (a fresh fetch only happens that often)."""
    if not players:
        return
    try:
        init_db_historical()
        fetched_at = _now()
        rows = [
            {
                "player_name": p["nombre"],
                "tour": tour,
                "ranking": p["ranking"],
                "country": p.get("pais"),
                "fetched_at": fetched_at,
            }
            for p in players
            if p.get("nombre") and p.get("ranking")
        ]
        if rows:
            TennisPlayerRanking.insert_many(rows).execute()
    except Exception:
        pass


def save_serve_calibration_snapshot(tour: str, calibration: dict[tuple[int, int], dict[str, Any]]) -> None:
    """One row per rank bucket, per fresh calibration run -- also a time
    series (is p_serve drifting over time? is n_matches growing?).
    Bounded by the 6h cache TTL in outer_apis/tennis_serve_stats.py."""
    if not calibration:
        return
    try:
        init_db_historical()
        calibrated_at = _now()
        rows = [
            {
                "tour": tour,
                "bucket_min": bucket[0],
                "bucket_max": bucket[1],
                "p_serve": data["p_serve"],
                "n_matches": data["n_matches"],
                "n_players": data["n_players"],
                "calibrated_at": calibrated_at,
            }
            for bucket, data in calibration.items()
        ]
        if rows:
            TennisServeCalibration.insert_many(rows).execute()
    except Exception:
        pass


def save_match_history(player_name: str, matches: list[dict[str, Any]]) -> None:
    """One row per real charted match -- DEDUPED against
    (player_name, opponent_name, match_date, score): unlike rankings/
    calibration, a real match doesn't change once played, so re-inserting
    it on every cache refresh would just inflate the table with
    duplicates of the same fact and bias any future aggregate stats
    (e.g. a "recent form" query built on top of this table would double-
    count matches). Checks existence before each insert -- O(n) queries
    per call, acceptable given `matches` is capped at _MAX_DEPTH=40 in
    outer_apis/tennis_match_history.py."""
    if not matches:
        return
    try:
        init_db_historical()
        fetched_at = _now()
        for m in matches:
            opponent = m.get("oponente")
            match_date = m.get("fecha")
            score = m.get("score")
            result = m.get("resultado")
            if not (opponent and match_date and result):
                continue
            exists = (
                TennisMatchHistory
                .select()
                .where(
                    (TennisMatchHistory.player_name == player_name)
                    & (TennisMatchHistory.opponent_name == opponent)
                    & (TennisMatchHistory.match_date == match_date)
                )
                .exists()
            )
            if exists:
                continue
            TennisMatchHistory.create(
                player_name=player_name,
                opponent_name=opponent,
                match_date=match_date,
                result=result,
                score=score,
                source=m.get("source", "flashscore"),
                fetched_at=fetched_at,
            )
    except Exception:
        pass


def _last_midnight_utc():
    """Borde de calendario UTC más reciente (00:00:00 GMT de HOY).

    2026-07-31, cambio de diseño explícito del usuario: los partidos de
    tenis se resuelven "de un día para otro" (nunca hay torneos de
    top-100 jugándose a las 00:00 GMT), así que el chequeo de frescura de
    `get_recent_match_history` pasó de una ventana rodante de horas
    (`now - fetched_at < N horas`, la fila se volvía stale a una hora
    distinta cada día según cuándo se hizo el último fetch) a un borde
    fijo de calendario: "¿se fetcheó HOY (desde las 00:00 GMT), sí o no?".
    Esto evita el problema real encontrado el 2026-07-30 con
    BrightData caído: con una ventana rodante de 1h, cada intento fallido
    de refresh mantenía la fila "stale" indefinidamente, generando un
    reintento contra Flashscore en CADA llamada sin ninguna posibilidad de
    éxito. Con el borde de calendario, si ya se intentó/logró hoy no se
    reintenta hasta mañana a las 00:00 GMT -- como máximo 1 intento
    (exitoso o no) por jugador por día natural."""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def get_recent_match_history(player_name: str, max_age_hours: float | None = None, limit: int = 40) -> list[dict[str, Any]] | None:
    """Lee el historial de partidos ya persistido para `player_name` si
    ya se fetcheó hoy (desde las 00:00 GMT más reciente, ver
    `_last_midnight_utc` -- reemplaza la ventana rodante de horas usada
    antes del 2026-07-31) -- None si no hay filas, o si la fila más
    reciente (`fetched_at`) es de ANTES del borde de hoy (fuerza un fetch
    en vivo en ese caso, como máximo una vez por día natural UTC).

    `max_age_hours`: parámetro legacy, ignorado si es None (default) --
    se mantiene por compatibilidad de firma, pero el chequeo real ya no es
    una ventana rodante. Si se pasa un valor explícito, se respeta como
    ventana rodante ADICIONAL (más estricta que el borde diario) para
    casos que necesiten frescura más agresiva que "una vez al día" -- hoy
    ningún caller la usa así, pero no se rompe la firma existente.

    Reemplaza el cache en memoria que existía en
    outer_apis/tennis_match_history.py (`_recent_matches_cache`): ese
    diccionario vivía solo en RAM del proceso y se vaciaba por completo en
    cada reinicio del contenedor, disparando un refetch masivo vía
    BrightData de TODOS los jugadores activos de una sola vez -- causa
    real investigada tras un pico de tráfico de proxy (ver memoria
    kairos-tennis-brightdata-db-cache-2026-07-28). La DB es la única
    fuente de verdad ahora: sobrevive a cualquier reinicio, y en SQLite
    con WAL + índice por `player_name` esta consulta es de milisegundos
    (nada comparable a los ~5s de un fetch real vía proxy), así que no
    hace falta una capa de memoria intermedia.

    Devuelve la MISMA forma que
    outer_apis.scrapers.tennis.flashscore_scraper.get_player_recent_matches
    ({"fecha", "oponente", "resultado", "score", "source"}, más reciente
    primero), para que el caller no tenga que distinguir si el dato vino
    de la DB o de un fetch en vivo.

    Fail-closed: cualquier error de DB devuelve None (fuerza fetch en vivo,
    nunca sirve una lista vacía fabricada como si fuera "sin historial")."""
    try:
        init_db_historical()
        latest = (
            TennisMatchHistory
            .select(TennisMatchHistory.fetched_at)
            .where(TennisMatchHistory.player_name == player_name)
            .order_by(TennisMatchHistory.fetched_at.desc())
            .limit(1)
            .first()
        )
        if latest is None:
            return None

        fetched_at = latest.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)

        if fetched_at < _last_midnight_utc():
            return None  # no se fetcheó todavía hoy -- fuerza fetch en vivo
        if max_age_hours is not None and (_now() - fetched_at).total_seconds() / 3600.0 > max_age_hours:
            return None  # ventana rodante adicional explícita, más estricta que el borde diario

        rows = (
            TennisMatchHistory
            .select()
            .where(TennisMatchHistory.player_name == player_name)
            .order_by(TennisMatchHistory.match_date.desc(), TennisMatchHistory.id.desc())
            .limit(limit)
        )
        return [
            {
                "fecha": r.match_date,
                "oponente": r.opponent_name,
                "resultado": r.result,
                "score": r.score,
                "source": r.source,
            }
            for r in rows
        ]
    except Exception:
        return None


def save_head_to_head_snapshot(player_a: str, player_b: str, h2h: dict[str, Any]) -> None:
    """One new row per fresh h2h computation -- a snapshot (like
    calibration), since the underlying record can genuinely change as new
    matches get played. Bounded by the 1h cache TTL of the underlying
    match-history fetch in outer_apis/tennis_match_history.py."""
    if not h2h:
        return
    try:
        init_db_historical()
        TennisHeadToHead.create(
            player_a_name=player_a,
            player_b_name=player_b,
            a_wins=h2h["a_wins"],
            b_wins=h2h["b_wins"],
            n_matches=h2h["n_matches"],
            computed_at=_now(),
        )
    except Exception:
        pass


def save_surface_match_history(player_name: str, matches: list[dict[str, Any]]) -> None:
    """Persiste historial de TennisExplorer (con superficie real por
    partido, ver outer_apis/scrapers/tennis/tennisexplorer_scraper.py).
    Dedup por (player_name, opponent_name, match_date, surface) -- mismo
    criterio que save_match_history, evita duplicar el mismo partido en
    fetches sucesivos que se solapan en rango de fechas."""
    if not matches:
        return
    try:
        init_db_historical()
        fetched_at = _now()
        for m in matches:
            opponent = m.get("opponent")
            match_date = m.get("match_date")
            surface = m.get("surface")
            if not opponent or not match_date or not surface:
                continue
            exists = TennisSurfaceMatchHistory.select().where(
                (TennisSurfaceMatchHistory.player_name == player_name)
                & (TennisSurfaceMatchHistory.opponent_name == opponent)
                & (TennisSurfaceMatchHistory.match_date == match_date)
                & (TennisSurfaceMatchHistory.surface == surface)
            ).exists()
            if exists:
                continue
            TennisSurfaceMatchHistory.create(
                player_name=player_name,
                opponent_name=opponent,
                match_date=match_date,
                surface=surface,
                result=m.get("result"),
                score=m.get("score"),
                tournament=m.get("tournament"),
                source="tennisexplorer",
                fetched_at=fetched_at,
            )
    except Exception:
        pass


def get_recent_surface_match_history(
    player_name: str, max_age_hours: float = 24.0, lookback_days: int = 183,
) -> list[dict[str, Any]] | None:
    """DB-first: partidos reales de `player_name` con superficie, dentro
    de los últimos `lookback_days` (~6 meses por default, ver
    outer_apis/sports/tennis/surface_form.py), SOLO si hay al menos una
    fila fetcheada dentro de `max_age_hours` (evita re-scrapear
    TennisExplorer en cada llamada del mismo scan, mismo patrón DB-first
    que get_recent_match_history para tenis/Flashscore).

    Fail-closed: None si no hay ninguna fila para el jugador, o si la más
    reciente está más vieja que `max_age_hours` (el llamador debe
    refetchear en vivo en ese caso) -- nunca sirve una ventana vacía
    fingiendo que es una respuesta válida de "sin partidos"."""
    from datetime import datetime, timedelta, timezone

    try:
        init_db_historical()
        latest = (
            TennisSurfaceMatchHistory.select()
            .where(TennisSurfaceMatchHistory.player_name == player_name)
            .order_by(TennisSurfaceMatchHistory.fetched_at.desc())
            .first()
        )
        if latest is None:
            return None

        fetched_at = latest.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - fetched_at) > timedelta(hours=max_age_hours):
            return None

        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date().isoformat()
        rows = (
            TennisSurfaceMatchHistory.select()
            .where(
                (TennisSurfaceMatchHistory.player_name == player_name)
                & (TennisSurfaceMatchHistory.match_date >= cutoff)
            )
            .order_by(TennisSurfaceMatchHistory.match_date.desc())
        )
        return [
            {
                "opponent": r.opponent_name,
                "match_date": r.match_date,
                "surface": r.surface,
                "result": r.result,
                "score": r.score,
            }
            for r in rows
        ]
    except Exception:
        return None
