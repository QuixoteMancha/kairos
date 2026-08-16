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
  3. `match_durations` -- `enrich_match_durations`, ya reactivo de por
     sí (busca los partidos sin duración). Cuesta 2 requests a
     Flashscore por partido (resolver id + fetch), tanda chica.
  4. `self_elo` -- `run_elo_backfill`, SIN RED pero es un pase
     cronológico sobre TODO el historial (no es incremental por
     diseño: el Elo de un partido depende de todos los anteriores).
     Por eso NO corre cada ciclo: solo cada `SELF_ELO_EVERY_N_CYCLES`
     (default 6 = ~cada 6h con el intervalo default), que es lo
     razonable para un dato que solo cambia cuando entran partidos
     nuevos.

Los huecos que este worker NO puede llenar (fuente inexistente hoy,
NO se inventan) están documentados en
`scripts/tennis/fill_data_gaps.py`: `Tournament.lat/lon/city`
(geocoder Nominatim purgado -- limita al worker de clima),
`Tournament.level` para ATP, y `MatchHistory.best_of`/`minutes`.

Env vars:
  `TENNIS_GAP_FILLER_INTERVAL_SECONDS` (default 3600 = 1h)
  `TENNIS_GAP_FILLER_AGES_LIMIT`       (default 2000, sin red)
  `TENNIS_GAP_FILLER_BIO_LIMIT`        (default 40)
  `TENNIS_GAP_FILLER_DURATIONS_LIMIT`  (default 40)
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

    # 3. Duraciones -- import perezoso: arrastra los scrapers de
    # Flashscore, no hace falta cargarlos si el paso no corre.
    def _durations():
        from scripts.tennis.enrich_match_durations import enrich_match_durations
        return enrich_match_durations(limit=_DURATIONS_LIMIT)

    result["match_durations"] = _step("match_durations", _durations)

    # 4. Elo propio -- pase completo, solo cada N ciclos.
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
