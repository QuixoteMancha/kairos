"""
Jeff Sackmann's Match Charting Project — raw fetch of per-player,
per-match serve stats (`pts` = service points played, `pts_won` = service
points won) for BOTH tours.

Verified live 2026-07-20 (see kairos-tennis-official-rankings-scraper /
kairos-tennis-data-source-jeffsackmann-dead memory): the ORIGINAL
`JeffSackmann/tennis_atp` and `tennis_wta` repos are 404/gone, but
`JeffSackmann/tennis_MatchChartingProject` is alive and actively updated
(pushed 2026-05), and — unlike the old repos — covers BOTH men's (`m`)
and women's (`w`) matches with the same schema, closing the WTA
calibration gap that TML-Database (ATP-only) couldn't.

Data source: `charting-{m,w}-stats-ServeBasics.csv` on the `master`
branch, one row per (match_id, player, row) where `row` is "Total" (whole
match), "1" (1st-serve-only breakdown) or "2" (2nd-serve-only breakdown).
We only use the "Total" rows here — `pts_won / pts` on the Total row is
the player's overall service-points-won fraction for that match, exactly
what `p_serve` calibration needs.

This module ONLY does raw HTTP + CSV parsing (no business logic, no
ranking bucketing, no caching policy) — the public call surface lives in
`outer_apis/tennis_serve_stats.py` per the project's separation: scrapers/ =
raw source access, outer_apis/<facade>.py = call methods used by the rest of
Kairos.
"""

import csv
import io
from typing import Any

import requests

_BASE_URL = "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master"
_REQUEST_TIMEOUT_S = 30


def _fetch_csv(filename: str) -> list[dict[str, str]] | None:
    """Fail-closed: any network error, non-200, or unparseable CSV -> None.
    Never returns partial/garbage rows as if they were valid."""
    url = f"{_BASE_URL}/{filename}"
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


def get_serve_basics(tour: str) -> list[dict[str, Any]]:
    """Per-player, per-match TOTAL serve stats for one tour.

    Args:
        tour: "m" (ATP/men's) or "w" (WTA/women's).

    Returns:
        List of {"match_id", "player", "pts", "pts_won"} dicts, one per
        player per match (only the "Total" row, 1st+2nd serve combined).
        Fail-closed: [] if the fetch/parse fails, or if a row's pts/pts_won
        can't be parsed as a valid positive count (never fabricates a
        stat from a malformed row, just skips it).
    """
    if tour not in ("m", "w"):
        return []

    rows = _fetch_csv(f"charting-{tour}-stats-ServeBasics.csv")
    if rows is None:
        return []

    result: list[dict[str, Any]] = []
    for row in rows:
        if row.get("row") != "Total":
            continue
        player = row.get("player")
        match_id = row.get("match_id")
        if not player or not match_id:
            continue
        try:
            pts = int(row.get("pts", ""))
            pts_won = int(row.get("pts_won", ""))
        except (TypeError, ValueError):
            continue
        if pts <= 0 or pts_won < 0 or pts_won > pts:
            continue
        result.append({"match_id": match_id, "player": player, "pts": pts, "pts_won": pts_won})

    return result


def get_match_surfaces(tour: str) -> dict[str, str]:
    """match_id -> Surface ("Clay"/"Hard"/"Grass"/...), from
    `charting-{tour}-matches.csv`. Used to cross-reference against
    `get_serve_basics`'s per-match rows (same `match_id`) for
    surface-specific p_serve calibration -- see
    `outer_apis/sports/tennis/serve_stats.py::calibrate_serve_pct_by_bucket_surface`.

    Fail-closed: {} if the fetch/parse fails, or if a row is missing
    match_id/Surface (never fabricates a surface for an unparseable row)."""
    if tour not in ("m", "w"):
        return {}

    rows = _fetch_csv(f"charting-{tour}-matches.csv")
    if rows is None:
        return {}

    result: dict[str, str] = {}
    for row in rows:
        match_id = row.get("match_id")
        surface = row.get("Surface")
        if not match_id or not surface:
            continue
        result[match_id] = surface.strip()

    return result


# ── Importación completa 2026-08-02 (pedido explícito del usuario:
# "importemos todos los matches. Con estadísticas... verificar hacia 10
# años atrás de todos los torneos") ─────────────────────────────────────

def _parse_charting_date(raw: str) -> str | None:
    """'20260521' -> '2026-05-21'. Fail-closed: None si no matchea."""
    if not raw or len(raw) != 8 or not raw.isdigit():
        return None
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def get_matches_index(tour: str, since_date: str | None = None) -> list[dict[str, Any]]:
    """Índice completo de partidos CARGADOS (no todo el tour, ver caveat
    de sesgo de selección documentado en `scripts/import_match_charting_project.py`)
    de `charting-{tour}-matches.csv`.

    `since_date`: ISO 'YYYY-MM-DD', filtra partidos con fecha >= esa
    (ej. últimos 10 años, pedido explícito del usuario) -- None trae
    todo el histórico (desde 1960s).

    NOTA IMPORTANTE (ver data_dictionary.txt oficial del repo):
    "Player1"/"Player2" NO indica quién ganó -- Player1 es simplemente
    "quien sacó primero". El ganador real hay que derivarlo de los
    archivos de puntos, ver `derive_match_winners`.

    Fail-closed: [] si el fetch/parse falla o `tour` no es 'm'/'w'."""
    if tour not in ("m", "w"):
        return []

    rows = _fetch_csv(f"charting-{tour}-matches.csv")
    if rows is None:
        return []

    result: list[dict[str, Any]] = []
    for row in rows:
        match_id = row.get("match_id")
        date = _parse_charting_date(row.get("Date", ""))
        player1, player2 = row.get("Player 1"), row.get("Player 2")
        if not match_id or not date or not player1 or not player2:
            continue
        if since_date and date < since_date:
            continue
        best_of_raw = row.get("Best of", "")
        try:
            best_of = int(best_of_raw)
        except (TypeError, ValueError):
            best_of = None
        result.append({
            "match_id": match_id, "player1": player1.strip(), "player2": player2.strip(),
            "date": date, "tournament": (row.get("Tournament") or "").strip() or None,
            "round": (row.get("Round") or "").strip() or None,
            "surface": (row.get("Surface") or "").strip() or None,
            "best_of": best_of,
        })

    return result


_OVERVIEW_STAT_KEYS = (
    "serve_pts", "aces", "dfs", "first_in", "first_won", "second_in", "second_won",
    "bk_pts", "bp_saved", "return_pts", "return_pts_won",
    "winners", "winners_fh", "winners_bh", "unforced", "unforced_fh", "unforced_bh",
)


def get_overview_stats(tour: str) -> dict[str, dict[str, dict[str, tuple[str | None, str | None]]]]:
    """Estadísticas reales de partido, por set Y totales, de
    `charting-{tour}-stats-Overview.csv` -- mismo shape de salida que
    `flashscore_scraper.get_match_statistics` ({"match": {stat:
    (p1_val, p2_val)}, "set_1": {...}, ...}) para poder combinarlas/
    compararlas con las que ya vienen de Flashscore.

    `player1`/`player2` acá SÍ importa el orden real del CSV (columna
    'player' de cada fila coincide con 'Player 1'/'Player 2' de
    `get_matches_index` para el mismo `match_id` -- se resuelve la
    posición p1/p2 comparando el nombre de la fila contra ambos).

    Fail-closed: {} si el fetch/parse falla."""
    if tour not in ("m", "w"):
        return {}

    matches_rows = _fetch_csv(f"charting-{tour}-matches.csv")
    stats_rows = _fetch_csv(f"charting-{tour}-stats-Overview.csv")
    if matches_rows is None or stats_rows is None:
        return {}

    player1_by_match: dict[str, str] = {
        r["match_id"]: r.get("Player 1", "").strip()
        for r in matches_rows if r.get("match_id")
    }

    result: dict[str, dict[str, dict[str, tuple[str | None, str | None]]]] = {}
    for row in stats_rows:
        match_id = row.get("match_id")
        player = (row.get("player") or "").strip()
        set_raw = row.get("set")
        if not match_id or not player or not set_raw:
            continue
        p1_name = player1_by_match.get(match_id)
        if p1_name is None:
            continue
        is_p1 = player == p1_name

        section_key = "match" if set_raw == "Total" else f"set_{set_raw}"
        match_dict = result.setdefault(match_id, {})
        section = match_dict.setdefault(section_key, {})

        for stat_key in _OVERVIEW_STAT_KEYS:
            value = row.get(stat_key)
            if value is None or value == "":
                continue
            existing = section.get(stat_key, (None, None))
            if is_p1:
                section[stat_key] = (value, existing[1])
            else:
                section[stat_key] = (existing[0], value)

    return result


def derive_match_winners(tour: str, since_date: str | None = None) -> dict[str, int]:
    """Ganador REAL por `match_id` (1 o 2, referido a 'Player 1'/'Player
    2' de `get_matches_index`) -- derivado del ÚLTIMO punto jugado de
    cada partido en los archivos de puntos (`Set1`/`Set2` = sets
    ganados por cada jugador HASTA ESE PUNTO; en el último punto del
    partido, quien tenga más sets ganó).

    `since_date`: ISO 'YYYY-MM-DD' -- determina qué archivos de puntos
    hace falta descargar (2010s/2020s cubren 2010-2026; pasar una fecha
    >= '2010-01-01' evita bajar el archivo `to-2009` completo, pedido
    explícito del usuario: acotar a los últimos 10 años).

    Fail-closed: partidos sin puntos parseables, o con Set1==Set2 en el
    último punto (dato inconsistente, no debería pasar en un partido
    real terminado), se excluyen del dict -- nunca se adivina un
    ganador."""
    if tour not in ("m", "w"):
        return {}

    files = []
    if since_date is None or since_date < "2010-01-01":
        files.append(f"charting-{tour}-points-to-2009.csv")
    if since_date is None or since_date < "2020-01-01":
        files.append(f"charting-{tour}-points-2010s.csv")
    files.append(f"charting-{tour}-points-2020s.csv")

    # `Set1`/`Set2` son sets ganados ANTES de jugarse este punto (no
    # incluyen el resultado del punto en curso) -- si el ÚLTIMO punto
    # real del partido es justo el que decide el set/partido (caso
    # típico: 1-1 en sets, deciding set en curso), Set1==Set2 en ese
    # punto pese a que el partido SÍ terminó ahí. Se guarda también
    # `PtWinner` de ese último punto para desempatar ese caso real, en
    # vez de descartarlo como "inconsistente".
    last_point: dict[str, tuple[int, int, str | None]] = {}
    for filename in files:
        rows = _fetch_csv(filename)
        if rows is None:
            continue
        for row in rows:
            match_id = row.get("match_id")
            if not match_id:
                continue
            try:
                set1 = int(row.get("Set1", ""))
                set2 = int(row.get("Set2", ""))
            except (TypeError, ValueError):
                continue
            last_point[match_id] = (set1, set2, row.get("PtWinner"))  # se sobreescribe -- el último visto es el final real

    winners: dict[str, int] = {}
    for match_id, (set1, set2, pt_winner) in last_point.items():
        if set1 > set2:
            winners[match_id] = 1
        elif set2 > set1:
            winners[match_id] = 2
        elif pt_winner in ("1", "2"):
            # empate en sets antes del último punto -- ese punto decidió
            # el set/partido, el ganador real es quien ganó ese punto.
            winners[match_id] = int(pt_winner)
        # si tampoco hay PtWinner utilizable, se excluye -- nunca se adivina.

    return winners
