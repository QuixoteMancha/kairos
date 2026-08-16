"""Script de control: scrapeo inicial + poblado completo de la base de
tenis (ATP+WTA) -- 2026-08-11, pedido explícito del usuario: "sólo
haremos un script que use los scrapers ya bien armados y guarde la data
en la base, un script control de escrapeo de data y guardado en la
base... con una función definida para el scrapeo inicial y poblado de
la base para tenis".

Contexto: `engine.py` tiene el bloque de invocación de estrategias
comentado a propósito (WIP del usuario, no tocar) -- por ahora el
sistema SOLO hace backtesting, no scan en vivo. Este script es el punto
de entrada único para dejar la DB lista para backtestear: no
reimplementa NINGÚN scraper (usa los ya construidos y probados esta
sesión/sesiones previas), solo ORQUESTA en el orden correcto los
scripts de backfill que ya existían dispersos.

ORDEN (deliberado, cada paso depende del anterior):
  1. Referencia Wikidata de nombres (`build_tennis_player_wikidata_reference`)
     -- base de verdad para el orden nombre/apellido, evita duplicados
     de `Player` por reordenamiento ambiguo ANTES de que el resto
     empiece a crear filas `Player`.
  2. Catálogo de nivel de torneo WTA (`wta_tournament_level_backfill`)
     -- alimenta `Tournament.level`, usado por los backtests de filtro
     de nivel (250+).
  3. Ranking histórico WTA (`wta_player_ranking_backfill`) -- snapshots
     de ranking real por fecha, `PlayerRankingHistory`.
  4. Historial de partidos ATP + WTA (`wta_top200_historical_backfill`,
     nombre legacy) -- recorre el catálogo completo de TennisExplorer:
     países, paginación y enlace de cada jugador; luego backfillea
     `MatchHistory`/`Player`/`Tournament` con los parsers existentes.

GAPS CONOCIDOS, NO resueltos acá (honesto, no se inventa cobertura):
  - Nivel de torneo ATP: sin fuente hoy (la única que existía,
    TML-Database/Sackmann, fue purgada esta sesión). `Tournament.level`
    queda `None` para filas ATP hasta que exista un reemplazo real.
  - Ranking histórico ATP (point-in-time por fecha, no solo snapshot
    actual): mismo motivo, sin fuente hoy.
  - `MatchHistory.retired`/`player_retired`: se completa solo si el
    partido se resuelve vía `save_match_detail` (match-detail real de
    TennisExplorer), no vía el camino "recentform" más liviano.

Fail-closed por paso: cada paso corre en su propio try/except -- un
paso que falla NO frena los siguientes (ej. si Wikidata está caído, el
backfill de historial igual puede correr, solo pierde el beneficio de
la referencia de nombres). El resumen final reporta qué pasos
realmente corrieron vs. cuáles fallaron, nunca se finge éxito."""

from __future__ import annotations

import sys
import time
from typing import Any

from database.db import init_db_historical


def _run_step(name: str, fn, *args, **kwargs) -> dict[str, Any]:
    """Corre un paso del backfill aislado -- fail-closed: cualquier
    excepción se captura y reporta, nunca tumba el script completo."""
    print(f"[initial_tennis_backfill] === paso: {name} ===")
    started = time.time()
    try:
        result = fn(*args, **kwargs)
        elapsed = round(time.time() - started, 1)
        print(f"[initial_tennis_backfill] {name} OK ({elapsed}s): {result}")
        return {"step": name, "ok": True, "result": result, "elapsed_s": elapsed}
    except Exception as e:
        elapsed = round(time.time() - started, 1)
        print(f"[initial_tennis_backfill] {name} FALLÓ ({elapsed}s): {e}")
        return {"step": name, "ok": False, "error": str(e), "elapsed_s": elapsed}


def run_initial_tennis_backfill(
    atp_n: int = 200,
    wta_n: int = 200,
    *,
    include_wikidata: bool = True,
    include_wta_tournament_levels: bool = True,
    include_wta_rankings: bool = True,
    include_self_elo: bool = True,
    max_workers: int = 1,
    max_catalog_players: int | None = None,
) -> dict[str, Any]:
    """Función principal -- scrapeo inicial + poblado completo de la
    base de tenis para ATP+WTA. Idempotente en cada paso (todos los
    scripts subyacentes ya lo son, ver sus propios docstrings) -- correr
    esto de nuevo no duplica filas, solo completa lo que falte.

    `atp_n`/`wta_n` se mantienen solo por compatibilidad con callers
    antiguos. El universo ya no depende del ranking: se procesa el
    catálogo completo, salvo que `max_catalog_players` limite una prueba.

    `include_self_elo`: 2026-08-11, pedido explícito del usuario
    ("sigamos con el Elo propio en base al historial de matches") --
    último paso, DESPUÉS del historial de partidos (necesita
    `MatchHistory` ya poblado para poder recorrerlo cronológicamente,
    ver `scripts/tennis/compute_self_elo.py`). Reemplaza conceptualmente
    la necesidad de una fuente externa de ranking histórico ATP (que no
    existe hoy, ver docstring de ese script).

    Returns: {"steps": [...], "all_ok": bool} -- un dict por paso
    (`_run_step`), nunca excepciona hacia el caller."""
    init_db_historical()

    from scripts.tennis.build_tennis_player_wikidata_reference import build_reference
    from scripts.tennis.wta_tournament_level_backfill import run_backfill as run_wta_tournament_levels
    from scripts.tennis.wta_player_ranking_backfill import run_backfill as run_wta_rankings
    from scripts.tennis.wta_top200_historical_backfill import run_country_catalog_backfill
    from scripts.tennis.compute_self_elo import run_elo_backfill

    steps: list[dict[str, Any]] = []

    if include_wikidata:
        steps.append(_run_step("wikidata_player_reference", build_reference))

    if include_wta_tournament_levels:
        steps.append(_run_step("wta_tournament_levels", run_wta_tournament_levels))

    if include_wta_rankings:
        steps.append(_run_step("wta_ranking_history", run_wta_rankings))

    # Fuente exhaustiva: catálogo TennisExplorer -> países -> paginación ->
    # cada jugador. `atp_n`/`wta_n` se conservan en la firma por
    # compatibilidad con callers antiguos, pero ya no limitan el universo.
    steps.append(_run_step(
        "match_history_country_catalog",
        run_country_catalog_backfill,
        max_players=max_catalog_players,
    ))

    if include_self_elo:
        steps.append(_run_step("self_elo", run_elo_backfill))

    all_ok = all(s["ok"] for s in steps)
    summary = {"steps": steps, "all_ok": all_ok}
    print(f"[initial_tennis_backfill] === resumen final: all_ok={all_ok} ===")
    for s in steps:
        status = "OK" if s["ok"] else "FALLÓ"
        print(f"  - {s['step']}: {status}")
    return summary


def _cli_int_arg(flag: str, default: int) -> int:
    if flag in sys.argv:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv):
            try:
                return int(sys.argv[idx + 1])
            except ValueError:
                pass
    return default


if __name__ == "__main__":
    # Sin --max-players recorre el catálogo completo.
    run_initial_tennis_backfill(
        atp_n=_cli_int_arg("--atp-n", 200),
        wta_n=_cli_int_arg("--wta-n", 200),
        max_catalog_players=(
            _cli_int_arg("--max-players", 0) or None
        ),
    )
