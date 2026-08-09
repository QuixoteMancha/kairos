"""Worker UNIFICADO de backfill de `MatchHistory` -- 2026-08-09, pedido
explícito del usuario: "para los 2 scripts y unifica funciones,
revisando el match-detail, para ir llenando todos los campos
faltantes. Si no se puede sacar data de match-detail o de las página
del player, derivar/inferir con la data que ya tenemos".

Reemplaza correr `validate_unresolved_via_live_fetch.py` y
`backfill_match_history_tour_via_sex.py` como 2 procesos separados por
UN solo loop con 3 fases, en ORDEN DE PRIORIDAD real (mejor fuente
primero, más cara; fallback más barato al final):

  FASE 1 -- MATCH-DETAIL (mejor fuente, resuelve identidad completa +
  tour + tournament/round/surface/score/winner_side de una sola vez):
  candidatos reales de `TennisMatchHistoryUnvalidated`, re-fetcheando
  la página del jugador para encontrar el `match_id` real y
  confirmando con `get_match_detail`. Reusa
  `validate_unresolved_via_live_fetch.run_once` tal cual (mismo
  mecanismo, sin duplicar lógica).

  FASE 2 -- PÁGINA DEL JUGADOR / sexo (fallback de red, más barato que
  match-detail pero solo resuelve `tour`): para jugadores que sigan
  con filas sin `tour` después de la fase 1 (partidos ya guardados por
  otra vía -- ej. `recentform:` en vivo -- que nunca van a pasar por
  match-detail). Reusa `backfill_match_history_tour_via_sex.run_backfill`.

  FASE 3 -- DERIVAR de datos que YA tenemos en la propia DB (sin red,
  la más barata de las 3): `level` (y `tour` como bonus) vía los
  catálogos ATP (Sackmann) / WTA (`WtaTournamentLevel`) ya presentes.
  Reusa `backfill_matchhistory_level_from_sackmann.run_backfill`. Es
  un full-table-scan liviano pero no gratis -- corre cada
  `_DERIVE_EVERY_N_CYCLES` ciclos, no en cada uno, para no repetir
  trabajo redundante entre corridas sucesivas donde casi nada cambió.

Cada fase es fail-closed e independiente -- si una fase no encuentra
nada que resolver, o falla, el loop sigue con la siguiente fase/ciclo
sin bloquear.

PROGRESO VISUAL (2026-08-09, pedido explícito del usuario: "que vaya
mostrando como barra los campos que va llenando y su % de progreso"):
al final de cada ciclo se imprime una barra ASCII de cobertura real
(filas CON dato / total) para los campos que este worker puede llenar
-- separado por fuente (`sackmann`/`tennisexplorer`, estructuras de
dato distintas, ver `models.py`), para poder ver en vivo cómo avanza
sin tener que consultar la DB a mano."""

from __future__ import annotations

import time
from typing import Any

from scripts.tennis.validate_unresolved_via_live_fetch import run_once as run_match_detail_phase
from scripts.tennis.backfill_match_history_tour_via_sex import run_backfill as run_tour_by_sex_phase
from scripts.tennis.backfill_matchhistory_level_from_sackmann import run_backfill as run_derive_phase

_SLEEP_BETWEEN_CYCLES_S = 30.0
# 2026-08-09, pedido explícito del usuario: "tiene que hacer el
# backfill de los matches que efectivamente carecen de los campos que
# estamos backfilleando, no de 200 jugadores" -- `None` = sin tope
# artificial, procesa TODO lo que esté realmente pendiente en cada
# ciclo (ambas funciones reusadas ya soportan `None` vía slice `[:None]`
# de Python, que devuelve la lista completa -- no requirió cambio en
# esas funciones). El ritmo real sigue estando gobernado por el
# throttle de request (0.5s+ delay aleatorio por fetch), no por este
# tope -- subir/bajar el tope solo cambiaba cada cuánto se imprimía el
# progreso, nunca la velocidad real de la red.
_MATCH_DETAIL_BATCH_PLAYERS = None
_TOUR_BY_SEX_BATCH_PLAYERS = None
_DERIVE_EVERY_N_CYCLES = 20  # full-table scan -- no repetir en cada ciclo de 30s

_BAR_WIDTH = 24

# Campos que este worker puede llenar, por fuente -- cada fuente tiene
# una estructura de dato distinta (ver models.py), no tiene sentido
# mostrar "minutes"/"level" para tennisexplorer (siempre van a estar
# vacíos, son campos exclusivos de sackmann) ni bio/slug para sackmann
# (Sackmann/TML-Database nunca trae eso, solo match-detail).
_PROGRESS_FIELDS_BY_SOURCE = {
    "tennisexplorer": [
        "tour", "level", "tournament",
        "player_a_ranking", "player_b_ranking",
        "player_a_slug", "player_b_slug",
        "player_a_birthdate", "player_b_birthdate",
        "player_a_height_cm", "player_b_height_cm",
        "player_a_weight_kg", "player_b_weight_kg",
        "player_a_plays", "player_b_plays",
    ],
    "sackmann": [
        "level", "best_of", "winner_age", "loser_age", "minutes",
        "player_a_ranking", "player_b_ranking",
    ],
}


def _bar(pct: float, width: int = _BAR_WIDTH) -> str:
    filled = round(width * max(0.0, min(100.0, pct)) / 100)
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def print_field_coverage() -> dict[str, dict[str, float]]:
    """Imprime una barra de progreso por campo, por fuente -- % de
    filas CON el dato (no vacío/NULL), no % de filas SIN el dato (más
    intuitivo leído como "cuánto se llenó" a medida que corre el
    worker). Devuelve el mismo dict para poder testear sin parsear
    stdout."""
    from database.db import init_db_historical
    from database.historical.tennis.models import MatchHistory

    init_db_historical()
    result: dict[str, dict[str, float]] = {}
    for source, fields in _PROGRESS_FIELDS_BY_SOURCE.items():
        total = MatchHistory.select().where(MatchHistory.source == source).count()
        print(f"\n=== cobertura de campos -- source={source} (total={total}) ===")
        result[source] = {}
        if not total:
            continue
        for fname in fields:
            field = getattr(MatchHistory, fname)
            filled = MatchHistory.select().where((MatchHistory.source == source) & field.is_null(False)).count()
            pct = round(100 * filled / total, 1)
            result[source][fname] = pct
            print(f"  {fname:22s} {_bar(pct)} {pct:5.1f}%")
    print(flush=True)
    return result


def run_cycle(cycle_number: int) -> dict[str, Any]:
    """1 ciclo completo -- fases 1 y 2 siempre, fase 3 cada
    `_DERIVE_EVERY_N_CYCLES` ciclos. Cada fase envuelta en su propio
    try/except -- un fallo real en una fase (ej. red caída) no debe
    frenar las demás."""
    result: dict[str, Any] = {"cycle": cycle_number}

    try:
        result["match_detail_phase"] = run_match_detail_phase(batch_players=_MATCH_DETAIL_BATCH_PLAYERS)
    except Exception as e:
        result["match_detail_phase"] = {"error": str(e)}

    try:
        result["tour_by_sex_phase"] = run_tour_by_sex_phase(limit_players=_TOUR_BY_SEX_BATCH_PLAYERS)
    except Exception as e:
        result["tour_by_sex_phase"] = {"error": str(e)}

    if cycle_number % _DERIVE_EVERY_N_CYCLES == 0:
        try:
            result["derive_phase"] = run_derive_phase()
        except Exception as e:
            result["derive_phase"] = {"error": str(e)}
    else:
        result["derive_phase"] = "skipped_this_cycle"

    return result


def run_forever(sleep_between_cycles_s: float = _SLEEP_BETWEEN_CYCLES_S) -> None:
    cycle = 0
    while True:
        cycle += 1
        result = run_cycle(cycle)
        print(result, flush=True)
        try:
            print_field_coverage()
        except Exception as e:
            print(f"print_field_coverage failed: {e}", flush=True)
        time.sleep(sleep_between_cycles_s)


if __name__ == "__main__":
    run_forever()
