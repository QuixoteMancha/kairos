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


def save_matches_to_db(year: int, matches: list[dict[str, Any]]) -> int:
    """Persiste partidos de un año (YA parseados, formato de
    `get_year_matches`) en la tabla local `TmlDatabaseMatch` -- usado
    SOLO por `scripts/tml_database_local_backfill.py`. Reemplaza
    cualquier fila previa de ese año (backfill idempotente: correr de
    nuevo actualiza con los datos más recientes del CSV real, no
    duplica). Devuelve el número de filas insertadas."""
    from database.db import init_db_historical
    from database.historical.tennis import TmlDatabaseMatch

    init_db_historical()
    with TmlDatabaseMatch._meta.database.atomic():
        TmlDatabaseMatch.delete().where(TmlDatabaseMatch.year == year).execute()
        if not matches:
            return 0
        rows = [{
            "year": year, "tourney_name": m.get("tourney_name"), "level": m.get("level"),
            "date": m["date"], "round": m.get("round"), "surface": m.get("surface"),
            "best_of": m.get("best_of"), "winner_name": m["winner_name"],
            "winner_rank": m.get("winner_rank"), "winner_age": m.get("winner_age"),
            "loser_name": m["loser_name"], "loser_rank": m.get("loser_rank"),
            "loser_age": m.get("loser_age"), "score": m.get("score"), "minutes": m.get("minutes"),
        } for m in matches]
        # 2026-08-04, hallazgo real corriendo el backfill: TML-Database
        # tiene un puñado de filas duplicadas EXACTAS dentro del mismo
        # año (mismo día/ganador/perdedor/torneo -- calidad de datos de
        # la fuente, no un bug nuestro). `on_conflict_ignore` descarta
        # el duplicado exacto en vez de fallar todo el backfill del año.
        TmlDatabaseMatch.insert_many(rows).on_conflict_ignore().execute()
    return len(matches)


def has_cached_year(year: int) -> bool:
    """True si ya hay partidos de ese año en la DB local (cualquier
    nivel de torneo -- el backfill siempre persiste TODO, el filtro de
    niveles se aplica al leer)."""
    from database.db import init_db_historical
    from database.historical.tennis import TmlDatabaseMatch

    init_db_historical()
    return TmlDatabaseMatch.select().where(TmlDatabaseMatch.year == year).exists()


def get_matches_range_from_db(start_year: int, end_year: int,
                               levels: frozenset[str] | None = TOUR_LEVELS_250_PLUS) -> list[dict[str, Any]]:
    """Lee partidos [start_year, end_year] SOLO de la DB local
    (`TmlDatabaseMatch`) -- NUNCA toca la red. Esta es la función que
    deben usar todos los scripts de backtest (2026-08-04, pedido
    explícito del usuario).

    Fail-closed real: si un año no tiene datos en la DB local (nunca se
    corrió el backfill, o se corrió para un rango distinto), ese año se
    excluye del resultado (igual que el fail-closed del scraper de red
    original) PERO con un warning explícito indicando qué años faltan y
    que hay que correr `scripts/tml_database_local_backfill.py` -- a
    diferencia del scraper de red, acá un año faltante casi siempre es
    un error de operación (backfill no corrido), no un fallo
    transitorio, así que el mensaje es más directo."""
    from database.db import init_db_historical
    from database.historical.tennis import TmlDatabaseMatch

    init_db_historical()

    result: list[dict[str, Any]] = []
    missing_years: list[int] = []
    for year in range(start_year, end_year + 1):
        query = TmlDatabaseMatch.select().where(TmlDatabaseMatch.year == year)
        rows = list(query)
        if not rows:
            missing_years.append(year)
            continue
        for r in rows:
            if levels is not None and (r.level or "") not in levels:
                continue
            result.append({
                "tourney_name": r.tourney_name, "level": r.level, "date": r.date,
                "round": r.round, "surface": r.surface, "best_of": r.best_of,
                "winner_name": r.winner_name, "winner_rank": r.winner_rank, "winner_age": r.winner_age,
                "loser_name": r.loser_name, "loser_rank": r.loser_rank, "loser_age": r.loser_age,
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
