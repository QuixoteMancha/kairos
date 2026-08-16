"""Worker REACTIVO de relleno de huecos de datos -- 2026-08-16, pedido
explícito del usuario: "que sea reactivo ese worker, que escanee qué
datos faltan y en base a los fetch que tenemos vaya llenando los datos
que faltan", con la restricción dura de que "no podemos reiniciar
nuevamente" (nada acá rehace el backfill: todo es incremental sobre lo
que YA está en la base).

Cada ciclo escanea el estado real de los huecos y corre los pasos en
orden de COSTO CRECIENTE, cada uno acotado y fail-closed por separado
(un paso que falla no frena a los siguientes ni tumba el worker):

  1. `player_ages` -- SIN RED, puro cálculo (`Player.birthdate` +
     `match_date` -> `winner_age`/`loser_age`). Barato, tope alto.
  2. `player_bio` -- 1 request por jugador a TennisExplorer,
     re-resolviendo un partido que YA tenemos (`get_match_detail` trae
     el bio de ambos lados y `_resolve_player_id` rellena solo los
     campos NULL). Tanda chica.
  3. `tournament_levels` / `tournament_locations` (2026-08-16) --
     nivel derivado del nombre real del torneo (SIN red: TennisExplorer
     ya codifica "challenger"/"ITF" como sufijo) + tabla estática de
     niveles altos; después ubicación vía Nominatim (1 req/s por
     política del servicio, por eso la tanda es chica). Ver
     `scripts/tennis/fill_tournament_metadata.py`.
  4. `match_durations` / `match_stats` -- `enrich_match_durations` y
     `enrich_match_stats`, ambos reactivos (buscan los partidos que
     todavía no tienen el dato). Cuestan ~2 requests a Flashscore por
     partido (resolver id + fetch), tandas chicas. `match_stats` solo
     mira partidos TERMINADOS: los que están en curso devuelven un feed
     incompleto ("Last 10 balls") y ensuciarían la tabla.
  5. `self_elo` -- `run_elo_backfill`, SIN RED pero es un pase
     cronológico sobre TODO el historial (no es incremental por
     diseño: el Elo de un partido depende de todos los anteriores).
     Por eso NO corre cada ciclo: solo cada `SELF_ELO_EVERY_N_CYCLES`
     (default 6 = ~cada 6h con el intervalo default), que es lo
     razonable para un dato que solo cambia cuando entran partidos
     nuevos.

El nivel EXACTO (500 vs 250) SÍ se resuelve, vía
`tournament_levels_refined` -> página de torneo de TennisExplorer
(puntos de ranking del ganador). El genérico "ATP Tour"/"WTA Tour" que
pone el paso sin-red es solo un estado transitorio hasta que ese paso
lo refine.

El único hueco que sigue SIN fuente es `MatchHistory.best_of`/`minutes`
(columnas legacy) -- no se inventan. Ver `scripts/tennis/fill_data_gaps.py`.

Env vars:
  `TENNIS_GAP_FILLER_INTERVAL_SECONDS` (default 3600 = 1h)
  `TENNIS_GAP_FILLER_AGES_LIMIT`       (default 2000, sin red)
  `TENNIS_GAP_FILLER_BIO_LIMIT`        (default 40)
  `TENNIS_GAP_FILLER_DURATIONS_LIMIT`  (default 40)
  `TENNIS_GAP_FILLER_STATS_LIMIT`      (default 40, ~2 req/partido)
  `TENNIS_GAP_FILLER_TYPED_STATS_LIMIT` (default 500, SIN red)
  `TENNIS_GAP_FILLER_GEOCODE_LIMIT`    (default 25, Nominatim = 1 req/s)
  `TENNIS_GAP_FILLER_SELF_ELO_EVERY_N_CYCLES` (default 6, 0 = nunca)
"""

import os
import time

from scripts.tennis.fill_data_gaps import (
    count_data_gaps, fill_player_ages, fill_player_bio,
)

_INTERVAL_S = int(os.getenv("TENNIS_GAP_FILLER_INTERVAL_SECONDS", "3600"))
_AGES_LIMIT = int(os.getenv("TENNIS_GAP_FILLER_AGES_LIMIT", "2000"))
_BIO_LIMIT = int(os.getenv("TENNIS_GAP_FILLER_BIO_LIMIT", "40"))
_DURATIONS_LIMIT = int(os.getenv("TENNIS_GAP_FILLER_DURATIONS_LIMIT", "40"))
_SELF_ELO_EVERY_N = int(os.getenv("TENNIS_GAP_FILLER_SELF_ELO_EVERY_N_CYCLES", "6"))
_GEOCODE_LIMIT = int(os.getenv("TENNIS_GAP_FILLER_GEOCODE_LIMIT", "25"))
_REFINE_LIMIT = int(os.getenv("TENNIS_GAP_FILLER_REFINE_LIMIT", "30"))
_STATS_LIMIT = int(os.getenv("TENNIS_GAP_FILLER_STATS_LIMIT", "40"))
_TYPED_STATS_LIMIT = int(os.getenv("TENNIS_GAP_FILLER_TYPED_STATS_LIMIT", "500"))


def _step(name: str, fn, *args, **kwargs) -> dict:
    """Corre un paso aislado -- fail-closed: cualquier excepción se
    reporta y se sigue con el resto (mismo criterio que
    `initial_tennis_backfill._run_step`)."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _run_once(cycle: int = 0) -> dict:
    result = {"gaps_before": _step("gaps", count_data_gaps)}

    # 1. Edades -- sin red, barato.
    result["player_ages"] = _step("player_ages", fill_player_ages, limit=_AGES_LIMIT)

    # 2. Bio de jugadores -- 1 request por jugador.
    result["player_bio"] = _step("player_bio", fill_player_bio, limit=_BIO_LIMIT)

    # 3. Metadata de torneos (2026-08-16) -- nivel primero (sin red,
    # derivado del nombre real) y después ubicación (Nominatim, 1 req/s
    # por política del servicio, tanda chica). Desbloquea el filtro
    # "250+" de los backtests y la cobertura del worker de clima.
    def _levels():
        from scripts.tennis.fill_tournament_metadata import fill_tournament_levels
        return fill_tournament_levels()

    def _locations():
        from scripts.tennis.fill_tournament_metadata import fill_tournament_locations
        return fill_tournament_locations(limit=_GEOCODE_LIMIT)

    def _refine():
        from scripts.tennis.fill_tournament_metadata import refine_generic_levels
        return refine_generic_levels(limit=_REFINE_LIMIT)

    result["tournament_levels"] = _step("tournament_levels", _levels)
    result["tournament_levels_refined"] = _step("tournament_levels_refined", _refine)
    result["tournament_locations"] = _step("tournament_locations", _locations)

    # 4. Duraciones -- import perezoso: arrastra los scrapers de
    # Flashscore, no hace falta cargarlos si el paso no corre.
    def _durations():
        from scripts.tennis.enrich_match_durations import enrich_match_durations
        return enrich_match_durations(limit=_DURATIONS_LIMIT)

    result["match_durations"] = _step("match_durations", _durations)

    # 4b. Estadísticas de partido (2026-08-16, pedido explícito del
    # usuario). Hace falta aparte porque el otro productor
    # (`sync_worker::fill_missing_stats_for_player`) solo cubre a los
    # jugadores de partidos NUEVOS de Kalshi -- el histórico nunca
    # recibía stats por ahí. Mismo costo que duraciones (~2 requests a
    # Flashscore por partido).
    def _stats():
        from scripts.tennis.enrich_match_stats import enrich_match_stats
        return enrich_match_stats(limit=_STATS_LIMIT)

    result["match_stats"] = _step("match_stats", _stats)

    # 4c. Columnas TIPADAS de estadísticas (2026-08-16) -- SIN RED: las
    # filas ya guardadas tienen el `raw_stats_json` completo, esto solo
    # lo re-parsea. Repara las escritas por el código viejo (que solo
    # guardaba JSON) y las filas por set que nunca se creaban.
    def _typed_stats():
        from scripts.tennis.backfill_typed_match_stats import backfill_typed_match_stats
        return backfill_typed_match_stats(limit=_TYPED_STATS_LIMIT)

    result["match_stats_typed"] = _step("match_stats_typed", _typed_stats)

    # 5. Elo propio -- pase completo, solo cada N ciclos.
    if _SELF_ELO_EVERY_N > 0 and cycle % _SELF_ELO_EVERY_N == 0:
        def _elo():
            from scripts.tennis.compute_self_elo import run_elo_backfill
            return run_elo_backfill()

        result["self_elo"] = _step("self_elo", _elo)
    else:
        result["self_elo"] = {"skipped": "no toca en este ciclo"}

    print(f"[tennis_gap_filler] {result}", flush=True)
    return result


if __name__ == "__main__":
    print(
        f"[tennis_gap_filler] loop mode, interval={_INTERVAL_S}s, "
        f"ages_limit={_AGES_LIMIT}, bio_limit={_BIO_LIMIT}, "
        f"durations_limit={_DURATIONS_LIMIT}, self_elo_every={_SELF_ELO_EVERY_N} ciclos",
        flush=True,
    )
    cycle = 0
    while True:
        try:
            _run_once(cycle)
        except Exception as e:
            print(f"[tennis_gap_filler] error: {e}", flush=True)
        cycle += 1
        time.sleep(_INTERVAL_S)
