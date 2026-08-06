"""Backfill masivo del historial real de partidos + cuotas históricas
del top-200 WTA actual, vía TennisExplorer -- 2026-08-03, pedido
explícito del usuario: "toma las top 200 actuales de tennis wta y haz
un fetch histórico, toma pausas entre los requests y entre cada
jugadora. completemos la base con más matches de WTA... añadir tabla
donde se pongan las odds históricas".

FUENTES:
- Ranking real: `outer_apis.sports.tennis.rankings.get_top_rankings`
  (WTA oficial, ya wireado, verificado en vivo 2026-08-03: 200 nombres
  reales).
- Historial + cuotas: `outer_apis.scrapers.tennis.tennisexplorer_player_scraper`
  (construido en esta misma sesión, sin proxy, ver ese módulo para el
  detalle de la estructura real).

RATE LIMITING (pedido explícito -- "toma pausas"): sin cifra oficial de
límite de TennisExplorer confirmada, se usa un criterio conservador
propio (no calibrado contra ningún límite real publicado):
  - `_PAUSE_BETWEEN_REQUESTS_S` entre cada request individual (por año).
  - `_PAUSE_BETWEEN_PLAYERS_S` adicional entre jugadora y jugadora.

ALCANCE DE AÑOS: `_YEARS` (2016-2026, mismo rango que TML-Database para
consistencia con el resto de la investigación de esta sesión) -- para
jugadoras con debut posterior, los años sin partidos simplemente no
aportan filas (fail-closed, no un error).

Idempotente: usa el mismo criterio de "ya existe" (player+opponent+date)
que ya tenían `TennisMatchHistory`/`TennisHistoricalOdds` -- correr esto
de nuevo no duplica filas, solo agrega lo nuevo (útil para reanudar si
se corta a mitad de camino)."""

from __future__ import annotations

import sys
import time
from datetime import date as dt_date, datetime, timezone

from database.db import init_db_historical, db_historical
from database.historical.tennis.tennismatchhistory import TennisMatchHistory
from database.historical.tennis.tennishistoricalodds import TennisHistoricalOdds
from outer_apis.sports.tennis.rankings import get_top_rankings
from outer_apis.scrapers.tennis.tennisexplorer_player_scraper import (
    guess_player_slug, _fetch, _parse_year_html,
)

_YEARS = list(range(2016, 2027))
_PAUSE_BETWEEN_REQUESTS_S = 1.5
_PAUSE_BETWEEN_PLAYERS_S = 3.0


def _to_iso_date(raw_date: str, reference_year: int) -> str | None:
    """'03.08.' (sin año, TennisExplorer solo lo omite para filas del
    año en curso -- se usa `reference_year`, el año pedido en el fetch,
    que es correcto porque cada fetch es por año explícito) -> ISO."""
    parts = [p for p in raw_date.strip().split(".") if p]
    if len(parts) < 2:
        return None
    day, month = parts[0], parts[1]
    try:
        return dt_date(reference_year, int(month), int(day)).isoformat()
    except ValueError:
        return None


def backfill_player(player_name: str) -> dict:
    slug = guess_player_slug(player_name)
    if slug is None:
        return {"player": player_name, "status": "skipped_no_slug", "matches_inserted": 0, "odds_inserted": 0}

    matches_by_year: list[tuple[int, dict]] = []
    for year in _YEARS:
        html = _fetch(slug, year=year)
        time.sleep(_PAUSE_BETWEEN_REQUESTS_S)
        if html is None:
            continue
        for m in _parse_year_html(html, player_name):
            matches_by_year.append((year, m))

    now = datetime.now(timezone.utc)
    inserted_matches = inserted_odds = repaired_matches = 0
    with db_historical.connection_context():
        for year, m in matches_by_year:
            iso_date = _to_iso_date(m["date"], year)
            if iso_date is None:
                continue

            existing_row = TennisMatchHistory.get_or_none(
                (TennisMatchHistory.player_name == player_name)
                & (TennisMatchHistory.opponent_name == m["opponent"])
                & (TennisMatchHistory.match_date == iso_date)
            )
            if existing_row is None:
                # W/L real: contar sets ganados vs perdidos en el score reorientado
                sets_won = sets_lost = 0
                for set_str in m["score"].split(","):
                    set_str = set_str.strip()
                    if "-" not in set_str:
                        continue
                    a, b = set_str.split("-")[:2]
                    try:
                        a, b = int(a), int(b)
                    except ValueError:
                        continue
                    if a > b:
                        sets_won += 1
                    elif b > a:
                        sets_lost += 1
                result = "W" if sets_won > sets_lost else "L"

                TennisMatchHistory.create(
                    player_name=player_name, opponent_name=m["opponent"], match_date=iso_date,
                    result=result, score=m["score"], source="tennisexplorer",
                    tournament=m["tournament"], round=m["round"], surface=m["surface"],
                    fetched_at=now,
                )
                inserted_matches += 1
            elif existing_row.tournament is None and m["tournament"] is not None:
                # 2026-08-04, pedido explícito -- REPARAR filas ya
                # existentes que quedaron con tournament=NULL por el
                # bug real de `type-women2` hardcodeado (ver
                # tennisexplorer_player_scraper.py), ahora corregido.
                # Sin esto, re-correr el backfill saltearía estas filas
                # para siempre (el chequeo de existencia de arriba las
                # trata como "ya migradas") sin repararlas nunca.
                existing_row.tournament = m["tournament"]
                existing_row.round = m["round"]
                existing_row.surface = m["surface"]
                existing_row.save()
                repaired_matches += 1

            if m["odds_self"] is not None or m["odds_opponent"] is not None:
                if not TennisHistoricalOdds.select().where(
                    (TennisHistoricalOdds.player_name == player_name)
                    & (TennisHistoricalOdds.opponent_name == m["opponent"])
                    & (TennisHistoricalOdds.match_date == iso_date)
                ).exists():
                    TennisHistoricalOdds.create(
                        player_name=player_name, opponent_name=m["opponent"], match_date=iso_date,
                        odds_self=m["odds_self"], odds_opponent=m["odds_opponent"],
                        source="tennisexplorer", fetched_at=now,
                    )
                    inserted_odds += 1

    return {
        "player": player_name, "status": "ok", "slug": slug,
        "matches_found": len(matches_by_year),
        "matches_inserted": inserted_matches, "odds_inserted": inserted_odds,
        "matches_repaired": repaired_matches,
    }


def _run_backfill_for_names(names: list[str], tag: str) -> list[dict]:
    """Loop real compartido -- extraído 2026-08-05, pedido explícito del
    usuario ("¿tenemos menos matches de WTA en base? ampliemos el
    backfill") para poder correr contra un roster AMPLIO (no solo el
    top-200 actual), sin duplicar la lógica de fetch/pausas/reporte."""
    init_db_historical()
    print(f"[{tag}_backfill] {len(names)} jugadoras/es reales a procesar", flush=True)

    summary = []
    for i, name in enumerate(names, start=1):
        t0 = time.monotonic()
        result = backfill_player(name)
        elapsed = time.monotonic() - t0
        print(
            f"[{tag}_backfill] ({i}/{len(names)}) {name} "
            f"-> {result['status']}, matches+{result.get('matches_inserted', 0)}, "
            f"repaired+{result.get('matches_repaired', 0)}, "
            f"odds+{result.get('odds_inserted', 0)} ({elapsed:.1f}s)",
            flush=True,
        )
        summary.append(result)
        time.sleep(_PAUSE_BETWEEN_PLAYERS_S)

    ok = sum(1 for r in summary if r["status"] == "ok")
    skipped = sum(1 for r in summary if r["status"] != "ok")
    total_matches = sum(r.get("matches_inserted", 0) for r in summary)
    total_repaired = sum(r.get("matches_repaired", 0) for r in summary)
    total_odds = sum(r.get("odds_inserted", 0) for r in summary)
    print(
        f"[{tag}_backfill] TERMINADO: {ok} ok, {skipped} sin slug/skip, "
        f"{total_matches} partidos nuevos, {total_repaired} partidos reparados "
        f"(tournament=NULL -> real), {total_odds} cuotas nuevas",
        flush=True,
    )
    return summary


def run_backfill(n_players: int = 200, tour: str = "WTA") -> list[dict]:
    """`tour`: "WTA" | "ATP" -- generalizado 2026-08-03, pedido explícito
    del usuario tras el backfill WTA ("puedes sacar las cuotas
    históricas de los matches de ATP?"). El scraper de TennisExplorer
    no depende del tour (misma URL/estructura por jugador/a), así que
    reusa exactamente la misma lógica -- solo cambia la fuente del
    ranking (`get_top_rankings(tour=...)`)."""
    players = get_top_rankings(n=n_players, tour=tour)
    names = [p["nombre"] for p in players]
    return _run_backfill_for_names(names, tag=tour.lower())


def run_backfill_from_wta_roster() -> list[dict]:
    """2026-08-05, pedido explícito del usuario: "¿tenemos menos matches
    de WTA en base [que ATP]? ampliemos el backfill" -- hallazgo real
    que lo motivó: `TennisMatchHistory` solo tenía el historial de
    quienes estaban en el TOP-200 ACTUAL al momento del backfill
    original (`run_backfill(tour="WTA")`), a diferencia de ATP
    (TML-Database trae TODO el tour 250+ sin depender de quién esté
    rankeada HOY) -- 12,153 casos evaluables WTA vs 24,030 ATP en el
    mismo rango de años, la mitad.

    Corre el mismo backfill de partidos/cuotas de TennisExplorer pero
    contra el roster AMPLIO ya construido en
    `wta_player_ranking_backfill.py` (666 jugadoras reales, unión de
    snapshots históricos 2016-2026, no solo el top-N de hoy) -- lee
    directamente de `WtaPlayerRankingHistory`, ya poblada."""
    from database.historical.tennis import WtaPlayerRankingHistory

    init_db_historical()
    names = sorted({r.player_name for r in WtaPlayerRankingHistory.select(WtaPlayerRankingHistory.player_name).distinct()})
    return _run_backfill_for_names(names, tag="wta_full_roster")


def run_backfill_from_atp_roster() -> list[dict]:
    """2026-08-05, pedido explícito del usuario: "verificar si tenemos
    todos los matches históricos con sus respectivas cuotas, sino
    lanzar el backfill para fetchear las cuotas históricas" -- hallazgo
    real que lo motivó: `TennisHistoricalOdds` solo tenía cobertura real
    para 486 jugadores/as en total (mezcla ATP+WTA de backfills previos,
    principalmente top-200 WTA), de los cuales apenas 102 coincidían con
    algún jugador real de TML-Database (7,536 jugadores distintos
    históricos) -- el pool ATP filtrado (12,513 casos, favorito+gap≥20+
    superficie) prácticamente no tenía cuota real de mercado vinculable.

    A diferencia de WTA (que necesitó `WtaPlayerRankingHistory` para el
    roster porque TML-Database no tiene equivalente ATP), acá el
    roster real sale directo de TML-Database: TODOS los nombres
    (ganador+perdedor) de partidos 2016+ nivel 250+ (`TOUR_LEVELS_250_PLUS`)
    -- exactamente el universo que puede llegar a aparecer en el pool
    evaluable de `favorite_surface_winrate_filter_backtest.py`. Medido
    en vivo: 739 jugadores reales (vs 7,536 del historial completo
    1968-2026 sin filtrar) -- acotar al rango/nivel relevante evita un
    backfill de +50h innecesario sobre jugadores que nunca calificarían
    para el pool filtrado igual."""
    from database.historical.tennis.tmldatabasematch import TmlDatabaseMatch
    from outer_apis.scrapers.tennis.tml_database_scraper import TOUR_LEVELS_250_PLUS

    init_db_historical()
    names: set[str] = set()
    for r in TmlDatabaseMatch.select(TmlDatabaseMatch.winner_name, TmlDatabaseMatch.loser_name).where(
        (TmlDatabaseMatch.date >= "2016-01-01") & (TmlDatabaseMatch.level.in_(list(TOUR_LEVELS_250_PLUS)))
    ):
        names.add(r.winner_name)
        names.add(r.loser_name)
    return _run_backfill_for_names(sorted(names), tag="atp_full_roster")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "full_roster":
        run_backfill_from_wta_roster()
    elif len(sys.argv) > 1 and sys.argv[1] == "atp_full_roster":
        run_backfill_from_atp_roster()
    else:
        n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
        run_backfill(n_players=n, tour="WTA")
