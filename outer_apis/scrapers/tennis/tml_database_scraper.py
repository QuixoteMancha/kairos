"""Tennismylife/TML-Database -- registro COMPLETO del tour ATP (no
sesgado como el Match Charting Project, ver
`match_charting_scraper.py`), investigado 2026-08-02 a pedido explícito
del usuario para poder correr el backtest de "favorito por ranking" con
el ranking REAL que cada jugador tenía AL MOMENTO del partido (no el de
hoy).

Verificado en vivo: repo activo (`pushed_at` 2026-01-27), archivos por
año `{year}.csv` desde 1968 hasta 2026, ~2945 partidos reales en 2025
(consistente con un año ATP completo, no una muestra). Trae
`winner_rank`/`loser_rank` REALES al momento del torneo, y
`tourney_level` con códigos directos: '250'/'500' (categoría literal),
'M' (Masters 1000), 'G' (Grand Slam), 'F' (ATP Finals), 'D' (Davis Cup,
formato de equipos, EXCLUIDO por defecto), 'A' (exhibición -- Laver Cup/
United Cup/Next Gen Finals, EXCLUIDO por defecto, no es el "torneo
regular" que pide la hipótesis del usuario).

SOLO ATP -- no existe equivalente WTA del mismo autor (investigado,
no hay `Tennismylife/WTA-Database` ni otros repos en esa org).

ARQUITECTURA DB-FIRST (2026-08-04, pedido explícito del usuario: "el
backtesting se debe hacer con datos de base, nada de estar llamando a
fuentes en red a cada rato"): `get_matches_range`/`get_year_matches`
de abajo son el scraper de RED -- se usan SOLO desde
`scripts/tml_database_local_backfill.py` para poblar la tabla local
`TmlDatabaseMatch` una vez (o cuando se pide un refresh explícito).
Todos los scripts de backtest (`favorite_by_historical_rank_backtest.py`,
`favorite_surface_winrate_filter_backtest.py`,
`favorite_comeback_pattern_analysis.py`, `self_computed_elo_backtest.py`)
usan en cambio `get_matches_range_from_db` -- lee SOLO de la DB local,
nunca toca la red durante una corrida de backtest.

Motivación real, no solo preferencia de estilo: antes de este cambio,
`get_year_matches` hacía un fetch de red por año EN CADA CORRIDA (59
años x N corridas por sesión) -- lento, y causó un hallazgo real de
no-determinismo (2026-08-04): un fallo transitorio de red en un año
puntual se absorbía en silencio (fail-closed correcto) y cambiaba los
resultados del backtest sin ningún aviso (capital final $1.1M vs $55M
con los mismos parámetros, en corridas distintas)."""

from __future__ import annotations

import csv
import io
import logging
from typing import Any

import requests

_logger = logging.getLogger(__name__)

_BASE_URL = "https://raw.githubusercontent.com/Tennismylife/TML-Database/master"
_REQUEST_TIMEOUT_S = 30

# "250 en adelante" (pedido original del usuario, 2026-08-02) -- excluye
# 'D' (Davis Cup, formato de equipos distinto) y 'A' (exhibición: Laver
# Cup/United Cup/Next Gen Finals, no torneo regular del tour).
TOUR_LEVELS_250_PLUS = frozenset({"250", "500", "M", "G", "F"})


def _fetch_csv(year: int) -> list[dict[str, str]] | None:
    """Fail-closed: None ante cualquier fallo de red/parseo -- nunca
    devuelve filas parciales/basura como si fueran válidas."""
    url = f"{_BASE_URL}/{year}.csv"
    try:
        resp = requests.get(url, timeout=_REQUEST_TIMEOUT_S)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        return list(csv.DictReader(io.StringIO(resp.text)))
    except csv.Error:
        return None


def get_year_matches(year: int, levels: frozenset[str] | None = TOUR_LEVELS_250_PLUS) -> list[dict[str, Any]]:
    """Partidos reales de un año, con ranking real al momento del
    partido. `levels=None` trae TODO (incluye Davis Cup/exhibición).

    Returns: lista de dicts {tourney_name, level, date (ISO), round,
    surface, best_of, winner_name, winner_rank, winner_age, loser_name,
    loser_rank, loser_age, score, minutes}. Fail-closed: [] si el
    fetch/parse falla o si una fila no tiene los campos mínimos
    (nombre+fecha de ambos jugadores). `winner_age`/`loser_age`
    (2026-08-03, pedido explícito del usuario para análisis de patrones
    de sorpresa) son la edad REAL en años (decimal) al momento del
    partido, tal cual la trae la fuente -- None si el campo viene vacío
    o no parseable."""
    rows = _fetch_csv(year)
    if rows is None:
        # 2026-08-04, hallazgo real: una corrida del backtest dio
        # resultados MUY distintos a corridas previas/posteriores con
        # los mismos parámetros (capital final $1.1M vs $55M) sin
        # ningún error visible -- causa más probable: un fallo
        # transitorio de red/GitHub en UN año puntual, absorbido en
        # silencio por el fail-closed de abajo (retorna [] para ese
        # año, el resto del backtest sigue corriendo con un universo de
        # datos más chico sin que nadie se entere). Este warning no
        # cambia el comportamiento fail-closed (sigue devolviendo []),
        # solo lo hace VISIBLE para poder correlacionar resultados
        # anómalos con fallos de fetch reales.
        _logger.warning("TML-Database: fetch de %s.csv falló (red o parseo) -- año excluido del resultado, universo de datos más chico de lo esperado", year)
        return []

    result: list[dict[str, Any]] = []
    for row in rows:
        level = (row.get("tourney_level") or "").strip()
        if levels is not None and level not in levels:
            continue

        winner_name, loser_name = row.get("winner_name"), row.get("loser_name")
        raw_date = row.get("tourney_date", "")
        if not winner_name or not loser_name or len(raw_date) != 8 or not raw_date.isdigit():
            continue
        date_iso = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"

        def _int_or_none(value: str | None) -> int | None:
            try:
                return int(value) if value else None
            except ValueError:
                return None

        def _float_or_none(value: str | None) -> float | None:
            try:
                return float(value) if value else None
            except ValueError:
                return None

        result.append({
            "tourney_name": row.get("tourney_name"), "level": level, "date": date_iso,
            "round": row.get("round"), "surface": row.get("surface"),
            "best_of": _int_or_none(row.get("best_of")),
            "winner_name": winner_name.strip(), "winner_rank": _int_or_none(row.get("winner_rank")),
            "winner_age": _float_or_none(row.get("winner_age")),
            "loser_name": loser_name.strip(), "loser_rank": _int_or_none(row.get("loser_rank")),
            "loser_age": _float_or_none(row.get("loser_age")),
            "score": row.get("score"), "minutes": _int_or_none(row.get("minutes")),
        })

    return result


def get_matches_range(start_year: int, end_year: int,
                       levels: frozenset[str] | None = TOUR_LEVELS_250_PLUS) -> list[dict[str, Any]]:
    """`get_year_matches` para cada año en [start_year, end_year]
    inclusive. Fail-closed por año: un año que falle no aborta el resto.

    SCRAPER DE RED -- usar solo desde `scripts/tml_database_local_backfill.py`.
    Los scripts de backtest deben usar `get_matches_range_from_db`
    (ver docstring del módulo)."""
    result: list[dict[str, Any]] = []
    for year in range(start_year, end_year + 1):
        result.extend(get_year_matches(year, levels=levels))
    return result


def _find_existing_match_history_row(MatchHistory, name_a: str, name_b: str, match_date: str):
    return MatchHistory.get_or_none(
        (MatchHistory.match_date == match_date)
        & (
            ((MatchHistory.player_a_name == name_a) & (MatchHistory.player_b_name == name_b))
            | ((MatchHistory.player_a_name == name_b) & (MatchHistory.player_b_name == name_a))
        )
    )


def save_matches_to_db(year: int, matches: list[dict[str, Any]]) -> int:
    """Persiste partidos de un año (YA parseados, formato de
    `get_year_matches`) en `MatchHistory` -- usado SOLO por
    `scripts/tml_database_local_backfill.py`.

    2026-08-09, MIGRADO de la tabla propia `TmlDatabaseMatch` (eliminada,
    pedido explícito del usuario) a `MatchHistory` (1 fila por partido
    compartida entre todas las fuentes). Cambio de comportamiento real:
    ya NO borra+reinserta todas las filas del año en cada corrida
    (`TmlDatabaseMatch` era una tabla propia, podía darse ese lujo;
    `MatchHistory` es compartida -- borrar sus filas rompería otras
    fuentes que ya la usan, ej. `match-detail` real). Ahora es UPSERT
    idempotente por (par de jugadores + fecha): si el partido ya existe
    en `MatchHistory` (por esta misma fuente en una corrida anterior, o
    por cualquier otra fuente -- ej. ya validado vía match-detail real),
    solo completa los campos que falten (nunca pisa un dato ya
    confirmado); si no existe, lo crea con `source="sackmann"`."""
    from datetime import datetime, timezone

    from database.db import init_db_historical
    from database.historical.tennis.models import MatchHistory

    init_db_historical()
    if not matches:
        return 0
    now = datetime.now(timezone.utc)
    saved = 0
    with MatchHistory._meta.database.atomic():
        for m in matches:
            winner_name, loser_name, date = m["winner_name"], m["loser_name"], m["date"]
            existing = _find_existing_match_history_row(MatchHistory, winner_name, loser_name, date)
            extra_fields = {
                "tournament": m.get("tourney_name"), "level": m.get("level"), "round": m.get("round"),
                "surface": m.get("surface"), "best_of": m.get("best_of"), "score": m.get("score"),
                "minutes": m.get("minutes"), "winner_age": m.get("winner_age"), "loser_age": m.get("loser_age"),
            }
            if existing is not None:
                changed = False
                winner_is_a = existing.player_a_name == winner_name
                rank_a_new = m.get("winner_rank") if winner_is_a else m.get("loser_rank")
                rank_b_new = m.get("loser_rank") if winner_is_a else m.get("winner_rank")
                if existing.player_a_ranking is None and rank_a_new is not None:
                    existing.player_a_ranking, changed = rank_a_new, True
                if existing.player_b_ranking is None and rank_b_new is not None:
                    existing.player_b_ranking, changed = rank_b_new, True
                if existing.winner_side is None:
                    existing.winner_side, changed = ("a" if winner_is_a else "b"), True
                for f, v in extra_fields.items():
                    if getattr(existing, f) is None and v is not None:
                        setattr(existing, f, v)
                        changed = True
                if changed:
                    existing.updated_at = now
                    existing.save()
            else:
                try:
                    MatchHistory.create(
                        match_id=f"tmldb:{date}:{winner_name}:{loser_name}",
                        source="sackmann", tour="ATP", winner_side="a",
                        player_a_name=winner_name, player_a_ranking=m.get("winner_rank"),
                        player_b_name=loser_name, player_b_ranking=m.get("loser_rank"),
                        match_date=date, fetched_at=now, **extra_fields,
                    )
                except Exception:
                    # 2026-08-04, hallazgo real: TML-Database tiene un
                    # puñado de filas duplicadas EXACTAS dentro del mismo
                    # año (mismo día/ganador/perdedor/torneo) -- el
                    # unique constraint de match_id las descarta acá en
                    # vez de fallar todo el backfill del año.
                    continue
            saved += 1
    return saved


def has_cached_year(year: int) -> bool:
    """True si ya hay partidos de ese año en `MatchHistory` con
    `source="sackmann"` (2026-08-09, migrado de `TmlDatabaseMatch`)."""
    from database.db import init_db_historical
    from database.historical.tennis.models import MatchHistory

    init_db_historical()
    return MatchHistory.select().where(
        (MatchHistory.source == "sackmann") & (MatchHistory.match_date.startswith(str(year)))
    ).exists()


def get_matches_range_from_db(start_year: int, end_year: int,
                               levels: frozenset[str] | None = TOUR_LEVELS_250_PLUS) -> list[dict[str, Any]]:
    """Lee partidos [start_year, end_year] SOLO de la DB local
    (`MatchHistory`, `source="sackmann"` -- 2026-08-09, migrado de
    `TmlDatabaseMatch`, eliminada) -- NUNCA toca la red. Esta es la
    función que deben usar todos los scripts de backtest (2026-08-04,
    pedido explícito del usuario). Devuelve exactamente la misma forma
    que la versión vieja (mismos keys de dict) para no romper a ninguno
    de los 4 backtests que la consumen.

    Fail-closed real: si un año no tiene datos en la DB local (nunca se
    corrió el backfill, o se corrió para un rango distinto), ese año se
    excluye del resultado (igual que el fail-closed del scraper de red
    original) PERO con un warning explícito indicando qué años faltan y
    que hay que correr `scripts/tml_database_local_backfill.py` -- a
    diferencia del scraper de red, acá un año faltante casi siempre es
    un error de operación (backfill no corrido), no un fallo
    transitorio, así que el mensaje es más directo."""
    from database.db import init_db_historical
    from database.historical.tennis.models import MatchHistory

    init_db_historical()

    result: list[dict[str, Any]] = []
    missing_years: list[int] = []
    for year in range(start_year, end_year + 1):
        # 2026-08-05, bug real encontrado y corregido (ver también nota
        # 2026-08-09 de migración): SIN order_by() explícito, SQLite no
        # garantiza el orden físico de lectura -- río abajo, los
        # backtests agrupan partidos por día y aplican el cupo de
        # capital del 50% en el orden en que llegan, así que el orden
        # debe ser 100% reproducible. `match_date, id` como desempate
        # estable.
        query = (
            MatchHistory.select()
            .where(
                (MatchHistory.source == "sackmann")
                & (MatchHistory.match_date.startswith(str(year)))
                & (MatchHistory.winner_side.is_null(False))
            )
            .order_by(MatchHistory.match_date, MatchHistory.id)
        )
        rows = list(query)
        if not rows:
            missing_years.append(year)
            continue
        for r in rows:
            if levels is not None and (r.level or "") not in levels:
                continue
            winner_is_a = r.winner_side == "a"
            winner_name = r.player_a_name if winner_is_a else r.player_b_name
            loser_name = r.player_b_name if winner_is_a else r.player_a_name
            winner_rank = r.player_a_ranking if winner_is_a else r.player_b_ranking
            loser_rank = r.player_b_ranking if winner_is_a else r.player_a_ranking
            result.append({
                "tourney_name": r.tournament, "level": r.level, "date": r.match_date,
                "round": r.round, "surface": r.surface, "best_of": r.best_of,
                "winner_name": winner_name, "winner_rank": winner_rank, "winner_age": r.winner_age,
                "loser_name": loser_name, "loser_rank": loser_rank, "loser_age": r.loser_age,
                "score": r.score, "minutes": r.minutes,
            })

    if missing_years:
        _logger.warning(
            "TML-Database (DB local): %d año(s) sin backfill -- %s. "
            "Correr `python3 -m scripts.tennis.tml_database_local_backfill` para poblarlos. "
            "El backtest sigue corriendo con un universo de datos más chico de lo esperado.",
            len(missing_years), missing_years,
        )

    return result
