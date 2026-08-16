"""Relleno REACTIVO de huecos de datos en la base de tenis --
2026-08-16, pedido explícito del usuario: "que sea reactivo ese worker,
que escanee qué datos faltan y en base a los fetch que tenemos vaya
llenando los datos que faltan" (con la restricción dura de que NO se
puede reiniciar/rehacer el backfill: todo acá es incremental sobre lo
que YA está en la base).

PRINCIPIO: este módulo NO agrega ninguna fuente de datos nueva -- solo
detecta qué falta y reusa los fetchers/scripts que ya existen y ya están
probados. Cada función escanea su propio hueco, lo llena de a tandas
acotadas, y es idempotente (correrla de nuevo sigue donde quedó).

HUECOS REALES medidos en producción 2026-08-16 (2.955 partidos, 1.429
jugadores, 310 torneos) y qué los llena:

  | hueco                          | cobertura | fuente existente          |
  |--------------------------------|-----------|---------------------------|
  | MatchHistory.player_*_elo      |    0%     | compute_self_elo.py       |
  | MatchHistory.duration          |    0%     | enrich_match_durations.py |
  | MatchHistory.winner/loser_age  |    0%     | CÁLCULO (sin red)         |
  | Player.birthdate/height/...    |  19-39%   | get_match_detail (bio)    |

NO cubiertos acá, documentados honestamente para decisión del usuario
(requieren una fuente que HOY no existe en el repo, así que llenarlos
sería inventar datos o construir un fetcher nuevo, fuera del pedido):
  - `Tournament.lat`/`lon`/`city` (0%): el geocoder Nominatim que los
    poblaba fue purgado (ver `match_weather.py::_get_location_with_
    geocode_cache`). Esto LIMITA directamente al worker de clima, que
    hoy depende de la tabla estática de 73 sedes.
  - `Tournament.level` (0%) para ATP: sin fuente desde la purga de
    TML-Database/Sackmann. El backfill de nivel WTA sí existe
    (`wta_tournament_level_backfill.py`) pero es un script aparte.
  - `MatchHistory.best_of`/`minutes` (0%): columnas legacy sin
    productor. `best_of` podría "inferirse" del número de sets, pero
    eso es fabricar el dato (un 3-0 en Grand Slam y un 3-0 al mejor de
    5 no se distinguen del marcador solo) -- fail-closed, no se hace.
"""

from __future__ import annotations

from datetime import date as _date
from typing import Any

from database.db import init_db_historical, db_historical
from database.historical.tennis.models import MatchHistory, Player


def _parse_date(raw) -> _date | None:
    if not raw:
        return None
    try:
        return _date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def fill_player_ages(limit: int = 2000) -> dict[str, Any]:
    """Completa `winner_age`/`loser_age` de `MatchHistory` a partir de
    `Player.birthdate` + `match_date`.

    SIN RED: es puro cálculo sobre datos que ya tenemos -- por eso corre
    primero y con un tope alto (es barato). Depende de que el jugador ya
    tenga `birthdate` cargada (ver `fill_player_bio`, que la completa
    vía match-detail), así que naturalmente mejora su cobertura a medida
    que ese otro paso avanza.

    `loser` se deriva: es el jugador que NO es `winner` (el modelo tiene
    FK a ganador, no a perdedor). Fail-closed: si falta `winner`,
    `match_date` o la fecha de nacimiento del lado que toca, esa fila se
    saltea -- nunca se fabrica una edad."""
    init_db_historical()

    query = (
        MatchHistory
        .select()
        .where(
            MatchHistory.match_date.is_null(False)
            & MatchHistory.winner.is_null(False)
            & (MatchHistory.winner_age.is_null() | MatchHistory.loser_age.is_null())
        )
        .limit(limit)
    )

    updated = skipped = 0
    for match in query:
        match_day = _parse_date(match.match_date)
        if match_day is None:
            skipped += 1
            continue

        winner_id = match.winner_id
        if match.player_a_id == winner_id:
            loser_id = match.player_b_id
        elif match.player_b_id == winner_id:
            loser_id = match.player_a_id
        else:
            # El ganador no coincide con ninguno de los 2 lados -- dato
            # inconsistente, no se toca (fail-closed).
            skipped += 1
            continue

        changed = False
        for side_id, field in ((winner_id, "winner_age"), (loser_id, "loser_age")):
            if side_id is None or getattr(match, field) is not None:
                continue
            player = Player.get_or_none(Player.id == side_id)
            if player is None or player.birthdate is None:
                continue
            age = (match_day - player.birthdate).days / 365.25
            if age <= 0 or age > 100:
                continue  # dato absurdo -> se descarta, no se guarda
            setattr(match, field, round(age, 2))
            changed = True

        if changed:
            match.save()
            updated += 1
        else:
            skipped += 1

    return {"updated": updated, "skipped": skipped}


def fill_player_bio(limit: int = 40) -> dict[str, Any]:
    """Completa el bio de `Player` (birthdate/height_cm/weight_kg/plays)
    re-resolviendo el match-detail real de UN partido suyo.

    CÓMO, sin fuente nueva: `get_match_detail` (TennisExplorer) ya
    devuelve el bio de AMBOS jugadores, y `save_match_detail` ->
    `_resolve_player_id` ya enriquece progresivamente los campos NULL de
    un `Player` existente (nunca pisa un valor ya confirmado). O sea:
    volver a resolver un partido que YA tenemos rellena el bio que
    faltaba, sin duplicar nada (`save_match_detail` es idempotente por
    `match_id` y devuelve "unchanged" si el partido no cambió).

    Se prioriza a los jugadores con MÁS partidos en la base (los más
    relevantes para backtests) y se saltea a los que no tengan ningún
    partido con `match_id` real (los sintéticos `recentform:` no tienen
    página de detalle que resolver).

    `limit`: jugadores por corrida -- cada uno cuesta 1 request real a
    TennisExplorer (throttle global compartido), así que se mantiene
    bajo y se completa de a tandas."""
    from database.historical.tennis.writer import _resolve_and_save_via_match_detail

    init_db_historical()

    incomplete = (
        Player
        .select()
        .where(
            Player.birthdate.is_null()
            | Player.height_cm.is_null()
            | Player.weight_kg.is_null()
            | Player.plays.is_null()
        )
    )

    processed = resolved = no_match = 0
    for player in incomplete:
        if processed >= limit:
            break

        match = (
            MatchHistory
            .select()
            .where(
                ((MatchHistory.player_a == player) | (MatchHistory.player_b == player))
                & ~(MatchHistory.match_id.startswith("recentform:"))
            )
            .first()
        )
        if match is None:
            no_match += 1
            continue

        processed += 1
        try:
            if _resolve_and_save_via_match_detail(player.canonical_name, match.match_id):
                resolved += 1
        except Exception:
            # Fail-closed por jugador: un fallo puntual de red/parseo no
            # frena la tanda (mismo criterio que el resto del proyecto).
            continue

    return {"processed": processed, "resolved": resolved, "players_without_real_match": no_match}


def count_data_gaps() -> dict[str, Any]:
    """Foto REAL del estado de los huecos -- se loguea en cada ciclo del
    worker para poder ver el progreso (o la falta de progreso) sin tener
    que entrar a consultar la DB a mano."""
    init_db_historical()
    with db_historical.connection_context():
        total_matches = MatchHistory.select().count()
        total_players = Player.select().count()
        return {
            "matches": total_matches,
            "matches_sin_elo": MatchHistory.select().where(MatchHistory.player_a_elo.is_null()).count(),
            "matches_sin_duracion": MatchHistory.select().where(MatchHistory.duration.is_null()).count(),
            "matches_sin_edades": MatchHistory.select().where(MatchHistory.winner_age.is_null()).count(),
            "players": total_players,
            "players_sin_bio": Player.select().where(
                Player.birthdate.is_null() | Player.height_cm.is_null()
                | Player.weight_kg.is_null() | Player.plays.is_null()
            ).count(),
        }
