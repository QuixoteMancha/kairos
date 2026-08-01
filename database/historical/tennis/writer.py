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

from datetime import datetime, timedelta, timezone
from typing import Any

# Ventana por default para get_all_surface_matches -- más amplia que los
# 6 meses de surface_form.py (LOOKBACK_DAYS=183) a propósito: el stat de
# "winrate vs grupo de rivales similares" (style_peer_group.py) necesita
# la mayor muestra real posible, no solo forma reciente.
LOOKBACK_DAYS_DEFAULT = 365.0

from database.db import init_db_historical
from database.historical.tennis.tennisplayerranking import TennisPlayerRanking
from database.historical.tennis.tennisservecalibration import TennisServeCalibration
from database.historical.tennis.tennismatchhistory import TennisMatchHistory
from database.historical.tennis.tennisheadtohead import TennisHeadToHead
from database.historical.tennis.tennissurfacematchhistory import TennisSurfaceMatchHistory
from database.historical.tennis.tennisabstractelo import TennisAbstractElo, TennisAbstractYelo


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

    ADEMÁS (2026-07-31, segundo pedido explícito del usuario -- "antes de
    hacer fetch de todo se debe verificar si hay un nuevo match con
    respecto a la fecha del último match guardado en base"): incluso si
    ya pasó el borde de medianoche desde el último fetch, si el partido
    más reciente YA GUARDADO en DB (`match_date`, no `fetched_at`) es de
    HOY, no hace falta un fetch en vivo -- lógicamente no puede existir un
    partido "más nuevo" del que ya tenemos, sin importar cuándo se hizo el
    último chequeo. Esto es un segundo camino a la frescura, independiente
    del borde diario: cualquiera de los dos (chequeado hoy, O ya tenemos
    el resultado de hoy) alcanza para servir desde DB.

    Fail-closed: cualquier error de DB devuelve None (fuerza fetch en vivo,
    nunca sirve una lista vacía fabricada como si fuera "sin historial")."""
    try:
        init_db_historical()
        today_str = _last_midnight_utc().date().isoformat()

        latest_match = (
            TennisMatchHistory
            .select(TennisMatchHistory.match_date)
            .where(TennisMatchHistory.player_name == player_name)
            .order_by(TennisMatchHistory.match_date.desc())
            .limit(1)
            .first()
        )
        if latest_match is None:
            return None
        already_have_todays_match = latest_match.match_date >= today_str

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

        checked_today = fetched_at >= _last_midnight_utc()
        if not checked_today and not already_have_todays_match:
            return None  # ni se chequeó hoy, ni ya tenemos el resultado de hoy -- fuerza fetch en vivo
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


def get_any_match_history(player_name: str, limit: int = 40) -> list[dict[str, Any]] | None:
    """Lee lo que haya en `TennisMatchHistory` para `player_name` SIN
    ningún chequeo de frescura -- a diferencia de
    `get_recent_match_history`, esto sirve datos VIEJOS a propósito.

    Usado exclusivamente cuando un jugador no está autorizado a un fetch
    en vivo hoy (ver `outer_apis/sports/tennis/match_history.py::
    set_fetch_allowlist`, pedido explícito del usuario 2026-08-01: solo
    hacer fetch de Flashscore para partidos NUEVOS) -- en ese caso,
    preferimos servir la última forma/H2H conocida (aunque tenga días) a
    no tener nada, mientras seguimos siendo honestos en el código sobre
    que puede estar desactualizada.

    Fail-closed: None si no hay NINGUNA fila para este jugador (nunca
    hubo un fetch exitoso) -- ahí sí el llamador no tiene nada que servir."""
    try:
        init_db_historical()
        rows = (
            TennisMatchHistory
            .select()
            .where(TennisMatchHistory.player_name == player_name)
            .order_by(TennisMatchHistory.match_date.desc(), TennisMatchHistory.id.desc())
            .limit(limit)
        )
        rows = list(rows)
        if not rows:
            return None
        return [
            {
                "fecha": r.match_date, "oponente": r.opponent_name,
                "resultado": r.result, "score": r.score, "source": r.source,
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


_TA_ELO_FRESHNESS_HOURS = 24 * 4  # 4 días -- el sitio se actualiza "semanalmente(ish)", ver docstring


def save_tennisabstract_elo_snapshot(tour: str, rows: list[dict[str, Any]]) -> None:
    """Snapshot semanal, una fila nueva por fetch fresco (mismo criterio
    que save_rankings_snapshot) -- no upsert, es una serie temporal."""
    if not rows:
        return
    try:
        init_db_historical()
        fetched_at = _now()
        to_insert = [
            {
                "player_name": r["player"], "tour": tour, "elo": r["elo"],
                "h_elo": r.get("h_elo"), "c_elo": r.get("c_elo"), "g_elo": r.get("g_elo"),
                "atp_rank": r.get("atp_rank"), "fetched_at": fetched_at,
            }
            for r in rows if r.get("player") and r.get("elo") is not None
        ]
        if to_insert:
            TennisAbstractElo.insert_many(to_insert).execute()
    except Exception:
        pass


def get_current_tennisabstract_elo(tour: str, max_age_hours: float = _TA_ELO_FRESHNESS_HOURS) -> dict[str, dict[str, Any]] | None:
    """DB-first: {player_name: {"elo","h_elo","c_elo","g_elo","atp_rank"}}
    de la fila MÁS RECIENTE por jugador, solo si el fetch más reciente de
    la tabla completa está dentro de `max_age_hours`. Fail-closed: None si
    no hay filas o están stale (fuerza fetch en vivo)."""
    try:
        init_db_historical()
        latest = (
            TennisAbstractElo
            .select(TennisAbstractElo.fetched_at)
            .where(TennisAbstractElo.tour == tour)
            .order_by(TennisAbstractElo.fetched_at.desc())
            .limit(1)
            .first()
        )
        if latest is None:
            return None
        fetched_at = latest.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if (_now() - fetched_at).total_seconds() / 3600.0 > max_age_hours:
            return None

        rows = (
            TennisAbstractElo
            .select()
            .where((TennisAbstractElo.tour == tour) & (TennisAbstractElo.fetched_at == latest.fetched_at))
        )
        return {
            r.player_name: {"elo": r.elo, "h_elo": r.h_elo, "c_elo": r.c_elo, "g_elo": r.g_elo, "atp_rank": r.atp_rank}
            for r in rows
        }
    except Exception:
        return None


def save_tennisabstract_yelo_snapshot(tour: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    try:
        init_db_historical()
        fetched_at = _now()
        to_insert = [
            {
                "player_name": r["player"], "tour": tour, "yelo": r["yelo"],
                "wins": r.get("wins"), "losses": r.get("losses"), "fetched_at": fetched_at,
            }
            for r in rows if r.get("player") and r.get("yelo") is not None
        ]
        if to_insert:
            TennisAbstractYelo.insert_many(to_insert).execute()
    except Exception:
        pass


def get_current_tennisabstract_yelo(tour: str, max_age_hours: float = _TA_ELO_FRESHNESS_HOURS) -> dict[str, dict[str, Any]] | None:
    """DB-first: {player_name: {"yelo","wins","losses"}}, mismo patrón que
    get_current_tennisabstract_elo."""
    try:
        init_db_historical()
        latest = (
            TennisAbstractYelo
            .select(TennisAbstractYelo.fetched_at)
            .where(TennisAbstractYelo.tour == tour)
            .order_by(TennisAbstractYelo.fetched_at.desc())
            .limit(1)
            .first()
        )
        if latest is None:
            return None
        fetched_at = latest.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if (_now() - fetched_at).total_seconds() / 3600.0 > max_age_hours:
            return None

        rows = (
            TennisAbstractYelo
            .select()
            .where((TennisAbstractYelo.tour == tour) & (TennisAbstractYelo.fetched_at == latest.fetched_at))
        )
        return {r.player_name: {"yelo": r.yelo, "wins": r.wins, "losses": r.losses} for r in rows}
    except Exception:
        return None


from database.historical.tennis.tennisabstractplayerstats import TennisAbstractCareerSplit, TennisAbstractH2H
from database.historical.tennis.tennisrecentresult import TennisRecentResult

_TA_PLAYER_STATS_FRESHNESS_HOURS = 24 * 7  # 1 semana -- career splits/H2H cambian muy lento partido a partido


def save_tennisabstract_career_splits(player_name: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    try:
        init_db_historical()
        fetched_at = _now()
        to_insert = [
            {
                "player_name": player_name, "surface": r["surface"],
                "matches": r.get("matches"), "wins": r.get("wins"), "losses": r.get("losses"),
                "win_pct": r.get("win_pct"), "hold_pct": r.get("hold_pct"),
                "break_pct": r.get("break_pct"), "ace_pct": r.get("ace_pct"), "df_pct": r.get("df_pct"),
                "fetched_at": fetched_at,
            }
            for r in rows if r.get("surface")
        ]
        if to_insert:
            TennisAbstractCareerSplit.insert_many(to_insert).execute()
    except Exception:
        pass


def get_current_career_splits(player_name: str, max_age_hours: float = _TA_PLAYER_STATS_FRESHNESS_HOURS) -> list[dict[str, Any]] | None:
    """DB-first, frescura POR JUGADOR (2026-07-31, pedido explícito del
    usuario: "solo cuando se necesiten actualizar los datos de algún
    player" -- cada jugador tiene su propio reloj de frescura, no uno
    global para toda la tabla). Fail-closed: None si no hay filas para
    ESTE jugador o están stale."""
    try:
        init_db_historical()
        latest = (
            TennisAbstractCareerSplit
            .select(TennisAbstractCareerSplit.fetched_at)
            .where(TennisAbstractCareerSplit.player_name == player_name)
            .order_by(TennisAbstractCareerSplit.fetched_at.desc())
            .limit(1)
            .first()
        )
        if latest is None:
            return None
        fetched_at = latest.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if (_now() - fetched_at).total_seconds() / 3600.0 > max_age_hours:
            return None

        rows = (
            TennisAbstractCareerSplit
            .select()
            .where((TennisAbstractCareerSplit.player_name == player_name)
                   & (TennisAbstractCareerSplit.fetched_at == latest.fetched_at))
        )
        return [
            {
                "surface": r.surface, "matches": r.matches, "wins": r.wins, "losses": r.losses,
                "win_pct": r.win_pct, "hold_pct": r.hold_pct, "break_pct": r.break_pct,
                "ace_pct": r.ace_pct, "df_pct": r.df_pct,
            }
            for r in rows
        ]
    except Exception:
        return None


def save_tennisabstract_h2h(player_name: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    try:
        init_db_historical()
        fetched_at = _now()
        to_insert = [
            {
                "player_name": player_name, "opponent_name": r["opponent"], "meetings": r["meetings"],
                "wins": r.get("wins"), "losses": r.get("losses"), "win_pct": r.get("win_pct"),
                "fetched_at": fetched_at,
            }
            for r in rows if r.get("opponent") and r.get("meetings") is not None
        ]
        if to_insert:
            TennisAbstractH2H.insert_many(to_insert).execute()
    except Exception:
        pass


def get_current_tennisabstract_h2h(player_name: str, max_age_hours: float = _TA_PLAYER_STATS_FRESHNESS_HOURS) -> list[dict[str, Any]] | None:
    """DB-first, frescura POR JUGADOR -- mismo criterio que get_current_career_splits."""
    try:
        init_db_historical()
        latest = (
            TennisAbstractH2H
            .select(TennisAbstractH2H.fetched_at)
            .where(TennisAbstractH2H.player_name == player_name)
            .order_by(TennisAbstractH2H.fetched_at.desc())
            .limit(1)
            .first()
        )
        if latest is None:
            return None
        fetched_at = latest.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if (_now() - fetched_at).total_seconds() / 3600.0 > max_age_hours:
            return None

        rows = (
            TennisAbstractH2H
            .select()
            .where((TennisAbstractH2H.player_name == player_name)
                   & (TennisAbstractH2H.fetched_at == latest.fetched_at))
        )
        return [
            {"opponent": r.opponent_name, "meetings": r.meetings, "wins": r.wins,
             "losses": r.losses, "win_pct": r.win_pct}
            for r in rows
        ]
    except Exception:
        return None


def get_all_career_splits_for_surface(surface: str, max_age_days: float = 30.0) -> dict[str, dict[str, Any]]:
    """Bulk read: {player_name: {surface, matches, wins, losses, win_pct,
    hold_pct, break_pct, ace_pct, df_pct}} para TODOS los jugadores que
    tengan una fila de `TennisAbstractCareerSplit` en `surface` -- la fila
    MÁS RECIENTE por jugador si hay más de una (nunca mezcla snapshots
    viejos de un jugador con nuevos de otro).

    Usado para armar el "pool" de candidatos de
    outer_apis/sports/tennis/style_peer_group.py -- este pool crece
    orgánicamente con el tiempo a medida que más jugadores aparecen en
    escaneos reales (cada uno se fetchea la primera vez que aparece en un
    mercado real, nunca se pre-carga todo el circuito de una vez).

    `max_age_days`: filas más viejas que esto se ignoran (evita usar el
    perfil de estilo de un jugador cuyo career-split no se refrescó en
    mucho tiempo, aunque siga siendo razonablemente estable dato a dato).

    Fail-closed: {} si no hay filas o falla la query -- nunca fabrica un
    pool de candidatos."""
    try:
        init_db_historical()
        cutoff = _now() - timedelta(days=max_age_days)
        rows = (
            TennisAbstractCareerSplit
            .select()
            .where((TennisAbstractCareerSplit.surface == surface)
                   & (TennisAbstractCareerSplit.fetched_at >= cutoff))
            .order_by(TennisAbstractCareerSplit.fetched_at.desc())
        )
        result: dict[str, dict[str, Any]] = {}
        for r in rows:
            if r.player_name in result:
                continue  # ya se guardó la fila más reciente de este jugador (orden desc)
            result[r.player_name] = {
                "surface": r.surface, "matches": r.matches, "wins": r.wins, "losses": r.losses,
                "win_pct": r.win_pct, "hold_pct": r.hold_pct, "break_pct": r.break_pct,
                "ace_pct": r.ace_pct, "df_pct": r.df_pct,
            }
        return result
    except Exception:
        return {}


def get_latest_rankings(tour: str, max_age_days: float = 14.0) -> dict[str, int]:
    """Bulk read: {player_name: ranking} para el snapshot MÁS RECIENTE de
    `TennisPlayerRanking` de ese tour (todas las filas de un mismo fetch
    comparten el mismo `fetched_at`, ver save_rankings_snapshot -- alcanza
    con el máximo global de la tabla para ese tour, no hace falta agrupar
    por jugador). Esta tabla YA se puebla en cada scan real (ver
    outer_apis/sports/tennis/rankings.py -> outer_apis/kalshi/markets/tennis.py),
    esto es lectura pura, sin fetch nuevo.

    Fail-closed: {} si no hay filas, el snapshot más reciente es más
    viejo que `max_age_days`, o falla la query."""
    try:
        init_db_historical()
        latest = (
            TennisPlayerRanking
            .select(TennisPlayerRanking.fetched_at)
            .where(TennisPlayerRanking.tour == tour)
            .order_by(TennisPlayerRanking.fetched_at.desc())
            .limit(1)
            .first()
        )
        if latest is None:
            return {}
        fetched_at = latest.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if (_now() - fetched_at).days > max_age_days:
            return {}

        rows = (
            TennisPlayerRanking
            .select()
            .where((TennisPlayerRanking.tour == tour) & (TennisPlayerRanking.fetched_at == latest.fetched_at))
        )
        return {r.player_name: r.ranking for r in rows}
    except Exception:
        return {}


def get_all_surface_matches(player_name: str, surface: str, lookback_days: float = LOOKBACK_DAYS_DEFAULT) -> list[dict[str, Any]]:
    """Lectura RAW de `TennisSurfaceMatchHistory` para `player_name` en
    `surface`, dentro de `lookback_days` -- a diferencia de
    `get_recent_surface_match_history`, NO tiene gate de frescura de
    `fetched_at` (esto es un stat derivado secundario, usar lo que ya
    esté en DB sin forzar/bloquear por un chequeo de "se refrescó hoy").

    Fail-closed: [] si no hay filas o falla la query."""
    try:
        init_db_historical()
        cutoff = (_now() - timedelta(days=lookback_days)).date().isoformat()
        rows = (
            TennisSurfaceMatchHistory
            .select()
            .where(
                (TennisSurfaceMatchHistory.player_name == player_name)
                & (TennisSurfaceMatchHistory.surface == surface)
                & (TennisSurfaceMatchHistory.match_date >= cutoff)
            )
        )
        return [
            {"opponent": r.opponent_name, "match_date": r.match_date, "result": r.result}
            for r in rows
        ]
    except Exception:
        return []


_TA_RECENT_RESULTS_FRESHNESS_HOURS = 24  # 1 día -- señal de lesión debe estar razonablemente al día, no 1 semana


def save_recent_results(player_name: str, rows: list[dict[str, Any]]) -> None:
    """Dedupe por (player_name, match_date, opponent_name) -- un partido
    real no cambia una vez jugado, evita duplicar en cada refresh."""
    if not rows:
        return
    try:
        init_db_historical()
        fetched_at = _now()
        for r in rows:
            date = r.get("date")
            opponent = r.get("opponent")
            if not date or not opponent:
                continue
            exists = TennisRecentResult.select().where(
                (TennisRecentResult.player_name == player_name)
                & (TennisRecentResult.match_date == date)
                & (TennisRecentResult.opponent_name == opponent)
            ).exists()
            if exists:
                continue
            TennisRecentResult.create(
                player_name=player_name, match_date=date, tournament=r.get("tournament"),
                opponent_name=opponent, subject_won=r.get("subject_won", False),
                retired_or_walkover=r.get("retired_or_walkover", False), fetched_at=fetched_at,
            )
    except Exception:
        pass


def get_recent_results(player_name: str, max_age_hours: float = _TA_RECENT_RESULTS_FRESHNESS_HOURS) -> list[dict[str, Any]] | None:
    """DB-first, frescura POR JUGADOR. Fail-closed: None si no hay filas
    para este jugador o la más reciente está stale (fuerza fetch en vivo)."""
    try:
        init_db_historical()
        latest = (
            TennisRecentResult
            .select(TennisRecentResult.fetched_at)
            .where(TennisRecentResult.player_name == player_name)
            .order_by(TennisRecentResult.fetched_at.desc())
            .limit(1)
            .first()
        )
        if latest is None:
            return None
        fetched_at = latest.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if (_now() - fetched_at).total_seconds() / 3600.0 > max_age_hours:
            return None

        rows = (
            TennisRecentResult
            .select()
            .where(TennisRecentResult.player_name == player_name)
            .order_by(TennisRecentResult.match_date.desc())
        )
        return [
            {
                "date": r.match_date, "tournament": r.tournament, "opponent": r.opponent_name,
                "subject_won": r.subject_won, "retired_or_walkover": r.retired_or_walkover,
            }
            for r in rows
        ]
    except Exception:
        return None
