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


def run_backfill(n_players: int = 200, tour: str = "WTA") -> list[dict]:
    """`tour`: "WTA" | "ATP" -- generalizado 2026-08-03, pedido explícito
    del usuario tras el backfill WTA ("puedes sacar las cuotas
    históricas de los matches de ATP?"). El scraper de TennisExplorer
    no depende del tour (misma URL/estructura por jugador/a), así que
    reusa exactamente la misma lógica -- solo cambia la fuente del
    ranking (`get_top_rankings(tour=...)`)."""
    init_db_historical()
    players = get_top_rankings(n=n_players, tour=tour)
    tag = tour.lower()
    print(f"[{tag}_backfill] {len(players)} jugadores/as reales del top-{n_players} {tour}", flush=True)

    summary = []
    for i, p in enumerate(players, start=1):
        name = p["nombre"]
        t0 = time.monotonic()
        result = backfill_player(name)
        elapsed = time.monotonic() - t0
        print(
            f"[{tag}_backfill] ({i}/{len(players)}) {name} rank={p['ranking']} "
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


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    run_backfill(n_players=n, tour="WTA")
