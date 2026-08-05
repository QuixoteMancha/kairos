"""Análisis de patrones cuando el favorito PIERDE el set 1 -- pedido
explícito del usuario 2026-08-03: "analicemos si hay algún patrón
cuando el favorito pierde y cuando remonta -- nivel promedio del
favorito, diferencia de ranking, edad promedio, ronda promedio donde
sucede la sorpresa, walkovers recientes del favorito".

Población base: los 9,772 casos de `favorite_by_historical_rank_backtest.py`
donde el favorito (por ranking real) PERDIÓ el set 1 -- de esos, 29.81%
"remonta" (gana el partido igual), 70.19% confirma la sorpresa (pierde).
Este script busca si alguna de esas 5 variables predice mejor que otra
CUÁL de los 2 grupos es más probable.

FUENTES (sin datos nuevos, todo ya estaba en TML-Database sin usar):
- `winner_age`/`loser_age` (agregado a `tml_database_scraper.py` en esta
  misma sesión) -- edad REAL en años al momento del partido.
- Retiros/walkovers recientes: auto-detectado desde el propio historial
  de partidos del jugador en TML-Database (score con "RET" o "W/O"),
  NO desde `outer_apis/sports/tennis/injury_risk.py` (esa fuente usa
  `date.today()` -- lookahead real si se aplicara a partidos históricos,
  mismo problema ya documentado para Tennis Abstract Elo). Point-in-time
  correcto por construcción: solo mira partidos ANTERIORES a la fecha
  del partido en análisis.

CAVEAT: la detección de walkovers está limitada a partidos nivel 250+
(mismo alcance del dataset) -- un retiro en un Challenger/qualy no
aparece acá (tampoco existe en TML-Database)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from outer_apis.scrapers.tennis.tml_database_scraper import get_matches_range_from_db as get_matches_range, TOUR_LEVELS_250_PLUS
from scripts.tennis.favorite_by_historical_rank_backtest import parse_set1_winner, _set1_game_diff

_DEFAULT_START_YEAR = 2016
_DEFAULT_END_YEAR = 2026
_WALKOVER_LOOKBACK_DAYS = 45  # mismo criterio que INJURY_LOOKBACK_DAYS en injury_risk.py

_RANK_BUCKETS = [(1, 11), (11, 21), (21, 51), (51, 101), (101, None)]
_RANK_BUCKET_LABELS = ["Top10", "11-20", "21-50", "51-100", "100+"]

_AGE_BUCKETS = [(0, 23), (23, 26), (26, 29), (29, 32), (32, None)]
_AGE_BUCKET_LABELS = ["<23", "23-25", "26-28", "29-31", "32+"]

_GAP_BUCKETS = [(0, 20), (20, 50), (50, 100), (100, None)]
_GAP_BUCKET_LABELS = ["0-20", "20-50", "50-100", "100+"]

_BEST_OF_LABELS = {3: "bo3", 5: "bo5"}

_SET1_DIFF_BUCKETS = [(1, 3), (3, 5), (5, 7)]
_SET1_DIFF_BUCKET_LABELS = ["cerrado (1-2 games)", "medio (3-4 games)", "paliza (5-6 games)"]


def _is_retirement_score(score: str | None) -> bool:
    if not score:
        return False
    upper = score.upper()
    return "RET" in upper or "W/O" in upper or "WEA" in upper or "DEF" in upper


def _build_retirement_history(matches: list[dict[str, Any]]) -> dict[str, list[str]]:
    """player_name -> fechas ISO (ordenadas) en las que ese jugador
    ABANDONÓ/no se presentó (fue el LOSER de un match con score de
    retiro/walkover) -- el ganador de un RET nunca es quien se retira."""
    history: dict[str, list[str]] = {}
    for m in matches:
        if _is_retirement_score(m.get("score")):
            history.setdefault(m["loser_name"], []).append(m["date"])
    for name in history:
        history[name].sort()
    return history


def _had_recent_walkover(player_name: str, match_date: str, history: dict[str, list[str]]) -> bool:
    """True si `player_name` tuvo un retiro/walkover REAL (ver arriba)
    en los `_WALKOVER_LOOKBACK_DAYS` días ANTERIORES a `match_date`
    (fail-closed hacia False si no hay historial -- ausencia de dato no
    es lo mismo que "sin riesgo", pero acá se reporta como agrupación
    binaria simple, no como señal de trading)."""
    dates = history.get(player_name)
    if not dates:
        return False
    cutoff = date.fromisoformat(match_date).toordinal() - _WALKOVER_LOOKBACK_DAYS
    match_ord = date.fromisoformat(match_date).toordinal()
    return any(cutoff <= date.fromisoformat(d).toordinal() < match_ord for d in dates)


def _bucket_label(value: float, buckets: list[tuple[float, float | None]], labels: list[str]) -> str | None:
    for (low, high), label in zip(buckets, labels):
        if value >= low and (high is None or value < high):
            return label
    return None


def _grouped_comeback_rate(cases: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, dict[str, int]] = {}
    for c in cases:
        bucket = c[key]
        if bucket is None:
            continue
        g = groups.setdefault(bucket, {"n": 0, "comebacks": 0})
        g["n"] += 1
        g["comebacks"] += 1 if c["hit"] else 0
    return {
        label: {
            "n": g["n"],
            "comeback_rate_pct": round(g["comebacks"] / g["n"] * 100, 2) if g["n"] else None,
        }
        for label, g in groups.items()
    }


def _build_cases(start_year: int, end_year: int) -> tuple[list[dict[str, Any]], int]:
    """Extraído de `run_analysis` (2026-08-03) para poder reutilizar la
    misma población de casos en `find_high_comeback_intersection` sin
    duplicar el fetch/parseo."""
    matches = get_matches_range(start_year, end_year, levels=TOUR_LEVELS_250_PLUS)
    retirement_history = _build_retirement_history(matches)

    cases = []
    skipped_no_rank = 0
    for m in matches:
        if m["winner_rank"] is None or m["loser_rank"] is None:
            skipped_no_rank += 1
            continue

        favorite_is_winner = m["winner_rank"] < m["loser_rank"]
        favorite_rank = min(m["winner_rank"], m["loser_rank"])
        underdog_rank = max(m["winner_rank"], m["loser_rank"])
        rank_gap = underdog_rank - favorite_rank
        favorite_name = m["winner_name"] if favorite_is_winner else m["loser_name"]
        favorite_age = m["winner_age"] if favorite_is_winner else m["loser_age"]
        hit = favorite_is_winner

        set1_winner = parse_set1_winner(m.get("score"))
        if set1_winner is None:
            continue  # sin dato de set1 (walkover/tie inconsistente) -- fuera de la población de análisis
        set1_favorite_win = (set1_winner == "winner") == favorite_is_winner
        if set1_favorite_win:
            continue  # población = SOLO casos donde el favorito PERDIÓ el set1

        best_of = m.get("best_of")
        set1_diff = _set1_game_diff(m.get("score"))

        cases.append({
            "hit": hit,
            "favorite_rank_bucket": _bucket_label(favorite_rank, _RANK_BUCKETS, _RANK_BUCKET_LABELS),
            "rank_gap_bucket": _bucket_label(rank_gap, _GAP_BUCKETS, _GAP_BUCKET_LABELS),
            "favorite_age_bucket": (
                _bucket_label(favorite_age, _AGE_BUCKETS, _AGE_BUCKET_LABELS) if favorite_age is not None else None
            ),
            "round": m.get("round"),
            "favorite_had_recent_walkover": _had_recent_walkover(favorite_name, m["date"], retirement_history),
            "best_of_bucket": _BEST_OF_LABELS.get(best_of),
            "set1_diff_bucket": (
                _bucket_label(set1_diff, _SET1_DIFF_BUCKETS, _SET1_DIFF_BUCKET_LABELS) if set1_diff is not None else None
            ),
        })
    return cases, skipped_no_rank


def run_analysis(start_year: int = _DEFAULT_START_YEAR, end_year: int = _DEFAULT_END_YEAR) -> dict[str, Any]:
    cases, skipped_no_rank = _build_cases(start_year, end_year)

    n = len(cases)
    overall_comebacks = sum(1 for c in cases if c["hit"])

    return {
        "population": "favorito perdió el set 1 (mismo universo que favorite_comeback_rate_after_losing_set1)",
        "n": n, "skipped_no_rank": skipped_no_rank,
        "overall_comeback_rate_pct": round(overall_comebacks / n * 100, 2) if n else None,
        "by_favorite_rank_level": _grouped_comeback_rate(cases, "favorite_rank_bucket"),
        "by_rank_gap": _grouped_comeback_rate(cases, "rank_gap_bucket"),
        "by_favorite_age": _grouped_comeback_rate(cases, "favorite_age_bucket"),
        "by_round": _grouped_comeback_rate(cases, "round"),
        "by_favorite_recent_walkover": _grouped_comeback_rate(cases, "favorite_had_recent_walkover"),
        "by_best_of": _grouped_comeback_rate(cases, "best_of_bucket"),
        "by_set1_closeness": _grouped_comeback_rate(cases, "set1_diff_bucket"),
        "caveat": (
            "'comeback_rate_pct' = % de casos donde el favorito, pese a "
            "perder el set 1, IGUAL ganó el partido -- más alto = mejor "
            "para esa categoría, más bajo = la sorpresa se confirma más "
            "seguido. Walkovers detectados SOLO dentro de partidos nivel "
            "250+ (mismo alcance del dataset), point-in-time correcto "
            "(nunca mira partidos futuros al del análisis)."
        ),
    }


_INTERSECTION_CATEGORIES = [
    ("favorite_rank_bucket", "by_favorite_rank_level"),
    ("rank_gap_bucket", "by_rank_gap"),
    ("favorite_age_bucket", "by_favorite_age"),
    ("round", "by_round"),
    ("favorite_had_recent_walkover", "by_favorite_recent_walkover"),
    ("best_of_bucket", "by_best_of"),
    ("set1_diff_bucket", "by_set1_closeness"),
]


def _qualifying_buckets(grouped: dict[str, dict[str, Any]], min_rate_pct: float) -> set[Any]:
    return {
        label for label, stats in grouped.items()
        if stats["comeback_rate_pct"] is not None and stats["comeback_rate_pct"] >= min_rate_pct
    }


def find_high_comeback_intersection(
    start_year: int = _DEFAULT_START_YEAR, end_year: int = _DEFAULT_END_YEAR,
    min_rate_pct: float = 30.0, exclude_categories: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Pedido explícito del usuario 2026-08-03: "intersección de los
    grupos encontrados con >=30% de probabilidad de remontar para
    encontrar las condiciones claves de alta remontada".

    Para cada una de las 5 categorías de `run_analysis`, toma los
    buckets INDIVIDUALES con comeback_rate_pct >= `min_rate_pct`, y
    filtra la población completa a los casos que caen SIMULTÁNEAMENTE
    en un bucket calificado de CADA categoría (AND entre categorías, OR
    dentro de una categoría si tiene más de un bucket calificado).

    `exclude_categories`: nombres de columna (ver `_INTERSECTION_CATEGORIES`)
    a excluir del filtro -- útil para dejar fuera categorías con muestra
    chica/ruidosa (ej. `favorite_had_recent_walkover`, n=655 vs miles en
    el resto) sin tocar el resto del análisis.

    Fail-closed: si una categoría no tiene NINGÚN bucket calificado, esa
    categoría queda "sin restricción" (no filtra nada por ella) en vez
    de devolver una intersección vacía sin explicación."""
    cases, skipped_no_rank = _build_cases(start_year, end_year)

    qualifying: dict[str, set[Any]] = {}
    per_category_summary: dict[str, Any] = {}
    for field, grouped_key in _INTERSECTION_CATEGORIES:
        if field in exclude_categories:
            continue
        grouped = _grouped_comeback_rate(cases, field)
        qset = _qualifying_buckets(grouped, min_rate_pct)
        qualifying[field] = qset
        per_category_summary[field] = {
            "qualifying_buckets": sorted(qset, key=str),
            "all_buckets": grouped,
        }

    filtered = [
        c for c in cases
        if all(c[field] in buckets for field, buckets in qualifying.items() if buckets)
    ]
    n = len(filtered)
    comebacks = sum(1 for c in filtered if c["hit"])

    return {
        "min_rate_pct_used": min_rate_pct,
        "excluded_categories": list(exclude_categories),
        "per_category_qualifying_buckets": {
            field: v["qualifying_buckets"] for field, v in per_category_summary.items()
        },
        "intersection_n": n,
        "intersection_comeback_rate_pct": round(comebacks / n * 100, 2) if n else None,
        "baseline_comeback_rate_pct": round(sum(1 for c in cases if c["hit"]) / len(cases) * 100, 2) if cases else None,
        "caveat": (
            "Intersección de condiciones EX-POST (encontradas en el mismo "
            "dataset que se usa para medir la tasa resultante) -- riesgo "
            "real de sobreajuste con muestras chicas. Ver `intersection_n` "
            "antes de confiar en `intersection_comeback_rate_pct`: si es "
            "chico (decenas, no cientos), la tasa puede ser ruido, no una "
            "señal real explotable."
        ),
    }


def _filter_by_qualifying(cases: list[dict[str, Any]], qualifying: dict[str, set[Any]]) -> list[dict[str, Any]]:
    return [
        c for c in cases
        if all(c[field] in buckets for field, buckets in qualifying.items() if buckets)
    ]


def validate_out_of_sample(
    train_start_year: int, train_end_year: int,
    test_start_year: int, test_end_year: int,
    min_rate_pct: float = 30.0, exclude_categories: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Pedido explícito del usuario 2026-08-03 (sugerencia #4 de mejora
    del backtesting, aplicada en código -- backtest real pendiente
    hasta tener toda la data en base): valida si la condición de alta
    remontada de `find_high_comeback_intersection` (encontrada sobre un
    período) SE SOSTIENE en un período distinto, o es sobreajuste
    ex-post.

    Metodología correcta (la condición NO se recalcula en el período de
    test -- eso sería trampa, seguiría siendo ex-post): se encuentran
    los buckets calificados SOLO con datos de `train_*`, y esos MISMOS
    buckets se aplican tal cual sobre los casos de `test_*` para medir
    la tasa real ahí.

    Fail-closed: si `test_n` es 0 (la condición de train no matchea
    ningún caso en el período de test), `test_comeback_rate_pct` queda
    None -- nunca se fabrica una tasa de una población vacía."""
    train_cases, _ = _build_cases(train_start_year, train_end_year)
    test_cases, _ = _build_cases(test_start_year, test_end_year)

    qualifying: dict[str, set[Any]] = {}
    for field, _grouped_key in _INTERSECTION_CATEGORIES:
        if field in exclude_categories:
            continue
        grouped = _grouped_comeback_rate(train_cases, field)
        qualifying[field] = _qualifying_buckets(grouped, min_rate_pct)

    train_filtered = _filter_by_qualifying(train_cases, qualifying)
    test_filtered = _filter_by_qualifying(test_cases, qualifying)

    def _rate(pool: list[dict[str, Any]]) -> tuple[int, float | None]:
        n = len(pool)
        if n == 0:
            return 0, None
        hits = sum(1 for c in pool if c["hit"])
        return n, round(hits / n * 100, 2)

    train_n, train_rate = _rate(train_filtered)
    test_n, test_rate = _rate(test_filtered)

    return {
        "train_years": f"{train_start_year}-{train_end_year}",
        "test_years": f"{test_start_year}-{test_end_year}",
        "min_rate_pct_used": min_rate_pct,
        "qualifying_buckets_found_in_train": {
            field: sorted(buckets, key=str) for field, buckets in qualifying.items()
        },
        "train_n": train_n, "train_comeback_rate_pct": train_rate,
        "test_n": test_n, "test_comeback_rate_pct": test_rate,
        "holds_up": (
            None if (train_rate is None or test_rate is None)
            else abs(train_rate - test_rate) <= 10.0  # tolerancia arbitraria de 10pp, no calibrada
        ),
        "caveat": (
            "'holds_up' usa una tolerancia de 10pp SIN calibrar (juicio "
            "propio, no un umbral estadístico real) -- mirar `test_n` "
            "antes que nada: si es chico, ninguna conclusión de este "
            "resultado es confiable, sostenga o no la tasa."
        ),
    }


if __name__ == "__main__":
    print(json.dumps(run_analysis(), indent=2, ensure_ascii=False))
