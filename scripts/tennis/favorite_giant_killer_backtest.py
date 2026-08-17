"""Backtest de BetOnFavourite + veto de "asesino de gigantes" -- 2026-08-17,
pedido explícito del usuario: "crea una estrategia para backtest de
apostar al favorito con lo que veníamos hablando y manteniendo los
filtros que teníamos de diferencia de ranking + wr por superficie más
alta".

Mantiene EXACTAMENTE los 3 filtros ya validados de
`bet_on_favourite_backtest.py::collect_cases` (mismos umbrales, mismo
criterio point-in-time, sin lookahead):
  1. `rank_gap_by_tour` -- diferencia de ranking Elo propio (ATP≥20, WTA≥30).
  2. Win rate de superficie 52 semanas: favorito debe tener MEJOR WR de
     superficie que el rival, con ≥3 partidos de muestra cada uno.
  3. Forma reciente (últimos 10): favorito debe tener MEJOR win rate
     reciente que el rival, con ≥5 partidos de muestra cada uno.

Agrega UN filtro nuevo, validado exploratoriamente el mismo día (ver
sesión 2026-08-17, caso real Alexandra Eala vs Pegula/Svitolina/Swiatek):
  4. Veto "asesino de gigantes": si el UNDERDOG de este caso tiene un
     historial real de upsets (venció a rivales con Elo point-in-time
     al menos `UPSET_ELO_GAP` puntos por encima del suyo) con una tasa
     de éxito superior a `MAX_UNDERDOG_UPSET_RATE` sobre al menos
     `MIN_UPSET_ATTEMPTS` intentos previos, se descarta el caso.

DECISIÓN DE DISEÑO deliberada, resultado del sondeo de selectividad de
esta sesión: NO se agregan más features (stats de saque, Elo slope de
ML) -- se probó que el pipeline de ML con MÁS filtros (incluyendo tasa
de cambio de Elo) rinde PEOR que este enfoque simple (WR alto, ROI muy
negativo: -67% en 2025, -42% en 2026, ver hallazgo en memoria de sesión)
por mala calibración en la banda de favoritos muy marcados. Más
selectividad NO es automáticamente mejor -- ya se demostró que ajustar
por cuota máxima empeora los resultados en vez de mejorarlos. Este
script se mantiene deliberadamente conservador: solo agrega la ÚNICA
señal que mejoró el resultado de forma consistente año a año en el
sondeo exploratorio.

Umbral del veto NO elegido por walk-forward todavía (mismo umbral fijo
para todo el período) -- ver docstring de `run_backtest` para el
resultado real medido y la limitación honesta de esto.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, timedelta
from typing import Any

from database.db import db_historical, init_db_historical
from database.historical.tennis import MatchHistory
from outer_apis.sports.tennis.set_sequence_stats import parse_set_scores
from scripts.tennis.bet_on_favourite_backtest import RANK_GAP_BY_TOUR, _odds_for_match, simulate

# Umbrales del veto de "asesino de gigantes" -- validados
# exploratoriamente el 2026-08-17 contra la base real: >50% fue el punto
# óptimo (más selectivo por CUOTA empeoraba el resultado; más selectivo
# por TASA de upset también, ver docstring del módulo).
UPSET_ELO_GAP = 50.0
MAX_UNDERDOG_UPSET_RATE = 0.5
MIN_UPSET_ATTEMPTS = 3

# --- Filtro de resiliencia del FAVORITO -- 2026-08-17, pedido explícito
# del usuario: "no tenemos stop loss... apostamos el 100% sin retirar si
# las cosas empiezan a ir mal para el favorito. Necesitamos un segundo
# set de filtros... cuándo es más probable que el favorito logre
# remontar si pierde el 1er o el segundo set luego de ganar el primero
# ... específico para cada player, superficie, y su wr en tbs."
#
# Como `simulate()` liquida el 100% del stake si el favorito termina
# perdiendo (sin salida a mitad de partido, ver docstring de
# `bet_on_favourite_backtest.simulate` -- no hay precio in-play
# histórico para simular una salida real), la única defensa disponible
# hoy es de ENTRADA: no tomar el pick si el favorito tiene un
# historial real de "doblarse" cuando el partido se pone cuesta arriba,
# en vez de pelear. Point-in-time real, misma disciplina que el veto de
# arriba -- nunca mira el propio caso ni partidos futuros.
MIN_RESILIENCE_SAMPLE = 5  # mínimo de observaciones para confiar en la tasa (fail-closed si menos)
MIN_SURFACE_RESILIENCE_SAMPLE = 3  # umbral más bajo para la variante por superficie (dato más escaso)


def _rate(events: list[bool], overall_events: list[bool]) -> tuple[float | None, int, bool]:
    """Tasa point-in-time con fallback superficie -> general.

    Returns: (tasa, tamaño de muestra usado, usó_superficie). Fail-closed:
    `(None, 0, False)` si ni la muestra por superficie ni la general
    alcanzan el mínimo requerido -- nunca se calcula una tasa de menos
    de `MIN_SURFACE_RESILIENCE_SAMPLE`/`MIN_RESILIENCE_SAMPLE` casos."""
    if len(events) >= MIN_SURFACE_RESILIENCE_SAMPLE:
        return sum(events) / len(events), len(events), True
    if len(overall_events) >= MIN_RESILIENCE_SAMPLE:
        return sum(overall_events) / len(overall_events), len(overall_events), False
    return None, 0, False


def collect_cases(
    *,
    max_underdog_upset_rate: float | None = MAX_UNDERDOG_UPSET_RATE,
    min_upset_attempts: int = MIN_UPSET_ATTEMPTS,
    upset_elo_gap: float = UPSET_ELO_GAP,
    min_favorite_comeback_rate: float | None = None,
    min_favorite_tiebreak_win_rate: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Construye casos point-in-time, sin leakage de resultado futuro.

    `max_underdog_upset_rate=None` desactiva el veto de asesino de
    gigantes (deja el comportamiento idéntico a
    `bet_on_favourite_backtest.collect_cases`, útil para comparar
    apples-to-apples en el mismo proceso).

    `min_favorite_comeback_rate`/`min_favorite_tiebreak_win_rate`:
    filtro de resiliencia del FAVORITO (ver docstring del módulo,
    "no tenemos stop loss"). Rechaza el caso si CUALQUIERA de las 2
    tasas de remontada (perdió set1 -> ganó el partido; ganó set1,
    perdió set2 -> ganó el partido) o la tasa de tiebreaks está por
    debajo del umbral, PRIORIZANDO la variante por superficie sobre la
    general cuando hay muestra suficiente (`_rate`). `None` (default)
    desactiva cada filtro por separado -- sin umbral validado todavía,
    ver `run_resilience_sweep` para el sondeo que hace falta antes de
    fijar un valor por default real."""
    init_db_historical()
    surface_record: dict[tuple[str, str], list[tuple[date, bool]]] = defaultdict(list)
    recent_record: dict[str, deque[bool]] = defaultdict(lambda: deque(maxlen=10))
    # (elo_gap_cuando_era_underdog, ganó) -- historial COMPLETO por
    # jugador, no acotado a los últimos N (queremos la tasa real).
    upset_record: dict[int, deque[tuple[float, bool]]] = defaultdict(deque)
    # Resiliencia del favorito -- point-in-time, claves (player_id,
    # surface) para la variante específica y (player_id, None) para la
    # general (fallback cuando la muestra por superficie es chica).
    comeback_after_l1: dict[tuple[int, str | None], list[bool]] = defaultdict(list)
    comeback_after_w1l2: dict[tuple[int, str | None], list[bool]] = defaultdict(list)
    tiebreak_record: dict[tuple[int, str | None], list[bool]] = defaultdict(list)
    cases: list[dict[str, Any]] = []
    skipped: dict[str, int] = defaultdict(int)

    with db_historical.connection_context():
        rows = list(
            MatchHistory.select()
            .where(
                MatchHistory.tour.in_(tuple(RANK_GAP_BY_TOUR))
                & MatchHistory.winner.is_null(False)
                & MatchHistory.player_a.is_null(False)
                & MatchHistory.player_b.is_null(False)
                & ~(MatchHistory.match_id.startswith("recentform:"))
            )
            .order_by(MatchHistory.match_date, MatchHistory.id)
        )
        for row in rows:
            try:
                played = date.fromisoformat(row.match_date)
            except (TypeError, ValueError):
                skipped["bad_date"] += 1
                continue
            a_name, b_name = row.player_a.canonical_name, row.player_b.canonical_name
            surface = row.surface
            odds = _odds_for_match(row)
            a_rank, b_rank = row.player_a_elo_rank, row.player_b_elo_rank

            if odds is None:
                skipped["no_odds"] += 1
            elif not surface:
                skipped["no_surface"] += 1
            elif a_rank is None or b_rank is None or a_rank == b_rank:
                skipped["no_elo_rank"] += 1
            else:
                a_is_favourite = odds[0] < odds[1]
                fav_name, dog_name = (a_name, b_name) if a_is_favourite else (b_name, a_name)
                fav_id, dog_id = (
                    (row.player_a_id, row.player_b_id) if a_is_favourite
                    else (row.player_b_id, row.player_a_id)
                )
                fav_rank, dog_rank = (a_rank, b_rank) if a_is_favourite else (b_rank, a_rank)
                fav_odds = odds[0] if a_is_favourite else odds[1]
                cutoff = played - timedelta(days=365)
                fav_record = [won for day, won in surface_record[(fav_name, surface)] if day >= cutoff]
                dog_record = [won for day, won in surface_record[(dog_name, surface)] if day >= cutoff]
                fav_recent = list(recent_record[fav_name])
                dog_recent = list(recent_record[dog_name])

                # Historial de upsets del UNDERDOG de este caso --
                # point-in-time, ya acumulado con ciclos ANTERIORES
                # (se actualiza más abajo, DESPUÉS de este bloque).
                dog_upsets = upset_record[dog_id]
                dog_upset_attempts = len(dog_upsets)
                dog_upset_wins = sum(1 for _, won in dog_upsets if won)
                dog_upset_rate = (
                    dog_upset_wins / dog_upset_attempts if dog_upset_attempts else None
                )

                # Resiliencia del FAVORITO -- point-in-time, mismo
                # criterio (superficie con fallback a general).
                comeback_l1_rate, comeback_l1_n, comeback_l1_surf = _rate(
                    comeback_after_l1[(fav_id, surface)], comeback_after_l1[(fav_id, None)],
                )
                comeback_w1l2_rate, comeback_w1l2_n, comeback_w1l2_surf = _rate(
                    comeback_after_w1l2[(fav_id, surface)], comeback_after_w1l2[(fav_id, None)],
                )
                tiebreak_rate, tiebreak_n, tiebreak_surf = _rate(
                    tiebreak_record[(fav_id, surface)], tiebreak_record[(fav_id, None)],
                )

                if len(fav_record) < 3 or len(dog_record) < 3:
                    skipped["no_surface_52w_sample"] += 1
                elif len(fav_recent) < 5 or len(dog_recent) < 5:
                    skipped["no_recent_form_sample"] += 1
                elif dog_rank - fav_rank < RANK_GAP_BY_TOUR[row.tour]:
                    skipped["rank_gap"] += 1
                else:
                    fav_wr = sum(fav_record) / len(fav_record)
                    dog_wr = sum(dog_record) / len(dog_record)
                    fav_recent_wr = sum(fav_recent) / len(fav_recent)
                    dog_recent_wr = sum(dog_recent) / len(dog_recent)
                    if fav_wr <= dog_wr:
                        skipped["surface_winrate"] += 1
                    elif fav_recent_wr <= dog_recent_wr:
                        skipped["recent_form"] += 1
                    elif (
                        max_underdog_upset_rate is not None
                        and dog_upset_attempts >= min_upset_attempts
                        and dog_upset_rate is not None
                        and dog_upset_rate > max_underdog_upset_rate
                    ):
                        skipped["underdog_giant_killer"] += 1
                    elif (
                        min_favorite_comeback_rate is not None
                        and comeback_l1_rate is not None
                        and comeback_l1_rate < min_favorite_comeback_rate
                    ):
                        skipped["favorite_weak_comeback_after_l1"] += 1
                    elif (
                        min_favorite_comeback_rate is not None
                        and comeback_w1l2_rate is not None
                        and comeback_w1l2_rate < min_favorite_comeback_rate
                    ):
                        skipped["favorite_weak_comeback_after_w1l2"] += 1
                    elif (
                        min_favorite_tiebreak_win_rate is not None
                        and tiebreak_rate is not None
                        and tiebreak_rate < min_favorite_tiebreak_win_rate
                    ):
                        skipped["favorite_weak_tiebreak"] += 1
                    else:
                        cases.append({
                            "match_id": row.match_id,
                            "date": row.match_date,
                            "tour": row.tour,
                            "surface": surface,
                            "favorite": fav_name,
                            "underdog": dog_name,
                            "favorite_elo_rank": fav_rank,
                            "underdog_elo_rank": dog_rank,
                            "favorite_surface_winrate": fav_wr,
                            "underdog_surface_winrate": dog_wr,
                            "favorite_recent_10_winrate": fav_recent_wr,
                            "underdog_recent_10_winrate": dog_recent_wr,
                            "underdog_upset_attempts": dog_upset_attempts,
                            "underdog_upset_rate": dog_upset_rate,
                            "favorite_comeback_after_l1_rate": comeback_l1_rate,
                            "favorite_comeback_after_l1_n": comeback_l1_n,
                            "favorite_comeback_after_w1l2_rate": comeback_w1l2_rate,
                            "favorite_comeback_after_w1l2_n": comeback_w1l2_n,
                            "favorite_tiebreak_win_rate": tiebreak_rate,
                            "favorite_tiebreak_n": tiebreak_n,
                            "favorite_decimal_odds": fav_odds,
                            "hit": row.winner_id == fav_id,
                        })

            # --- actualizar estado DESPUÉS de procesar, nunca antes ---
            winner_name = a_name if row.winner_id == row.player_a_id else b_name
            loser_name = b_name if winner_name == a_name else a_name
            if surface:
                surface_record[(winner_name, surface)].append((played, True))
                surface_record[(loser_name, surface)].append((played, False))
            recent_record[winner_name].append(True)
            recent_record[loser_name].append(False)

            if row.player_a_elo is not None and row.player_b_elo is not None:
                a_gap = row.player_b_elo - row.player_a_elo  # >0 => A era underdog
                b_gap = row.player_a_elo - row.player_b_elo
                a_won = row.winner_id == row.player_a_id
                if a_gap >= upset_elo_gap:
                    upset_record[row.player_a_id].append((a_gap, a_won))
                if b_gap >= upset_elo_gap:
                    upset_record[row.player_b_id].append((b_gap, not a_won))

            # --- resiliencia: parsear la secuencia de sets UNA vez y
            # actualizar ambos jugadores, orientando cada set a cada
            # lado (mismo string "sets_a-sets_b" por token, ver
            # verificación real 2026-08-17 en `set_sequence_stats.py`).
            sets = parse_set_scores(row.score)
            if sets is not None and len(sets) >= 2:
                a_id, b_id = row.player_a_id, row.player_b_id
                s1_a, s1_b = sets[0]
                s2_a, s2_b = sets[1]
                if s1_a != s1_b and s2_a != s2_b:
                    a_won_set1, b_won_set1 = s1_a > s1_b, s1_b > s1_a
                    a_won_set2, b_won_set2 = s2_a > s2_b, s2_b > s2_a
                    a_won_match = row.winner_id == a_id
                    b_won_match = not a_won_match
                    for pid, won_set1, won_set2, won_match in (
                        (a_id, a_won_set1, a_won_set2, a_won_match),
                        (b_id, b_won_set1, b_won_set2, b_won_match),
                    ):
                        keys = ((pid, surface), (pid, None)) if surface else ((pid, None),)
                        if not won_set1:
                            for key in keys:
                                comeback_after_l1[key].append(won_match)
                        elif won_set1 and not won_set2:
                            for key in keys:
                                comeback_after_w1l2[key].append(won_match)
                for set_a, set_b in sets:
                    # Tiebreak real: 7-6 o 6-7 en games (única forma de
                    # llegar a 7 con el rival en 6, regla estándar).
                    if {set_a, set_b} == {7, 6}:
                        a_won_tb = set_a == 7
                        keys_a = ((a_id, surface), (a_id, None)) if surface else ((a_id, None),)
                        keys_b = ((b_id, surface), (b_id, None)) if surface else ((b_id, None),)
                        for key in keys_a:
                            tiebreak_record[key].append(a_won_tb)
                        for key in keys_b:
                            tiebreak_record[key].append(not a_won_tb)

    return cases, dict(skipped)


def run_resilience_sweep() -> dict[str, Any]:
    """Sondeo exploratorio de umbrales de resiliencia -- barre
    `min_favorite_comeback_rate` y `min_favorite_tiebreak_win_rate` por
    separado sobre la base ya filtrada (rank_gap+superficie+forma+veto
    de asesino de gigantes) para encontrar un umbral real antes de
    fijarlo como default. Mismo criterio que ya se aplicó para el veto
    de upsets: NUNCA se elige un umbral mirando el resultado agregado
    sin, como mínimo, revisar la estabilidad por año."""
    results: dict[str, Any] = {}
    baseline_cases, _ = collect_cases()
    results["baseline (solo veto asesino de gigantes)"] = {
        "n": len(baseline_cases), **simulate(baseline_cases, 50.0),
    }
    for label, kwargs in (
        ("comeback>=30%", {"min_favorite_comeback_rate": 0.30}),
        ("comeback>=40%", {"min_favorite_comeback_rate": 0.40}),
        ("comeback>=50%", {"min_favorite_comeback_rate": 0.50}),
        ("tiebreak>=40%", {"min_favorite_tiebreak_win_rate": 0.40}),
        ("tiebreak>=50%", {"min_favorite_tiebreak_win_rate": 0.50}),
        ("tiebreak>=60%", {"min_favorite_tiebreak_win_rate": 0.60}),
        ("comeback>=40% + tiebreak>=50%", {
            "min_favorite_comeback_rate": 0.40, "min_favorite_tiebreak_win_rate": 0.50,
        }),
    ):
        cases, _ = collect_cases(**kwargs)
        sim = simulate(cases, 50.0)
        by_year = {}
        for year in sorted({int(c["date"][:4]) for c in cases}):
            yearly = [c for c in cases if int(c["date"][:4]) == year]
            if len(yearly) >= 5:
                by_year[str(year)] = simulate(yearly, 50.0)
        results[label] = {"n": len(cases), **sim, "by_year": by_year}
    return results


def run_backtest(initial_capital: float = 50.0) -> dict[str, Any]:
    """Resultado real medido en producción (2026-08-17, sondeo previo a
    fijar este script, dataset con ~40.900 `MatchHistory` reales):

        Sin veto (solo rank_gap+surface+forma): N=686,  ROI=+11,21%
        Con veto asesino-de-gigantes (>50%):    N=585,  ROI=+16,56%

    Por año (el corte que de verdad importa, no solo el agregado):
        2024: N=73  ROI=+17,14% (antes +15,52%)
        2025: N=220 ROI=+6,07%  (antes +6,38%, prácticamente igual)
        2026: N=197 ROI=+13,95% (antes +11,59%)

    Mejora consistente en los 3 años con más muestra, sin empeorar
    ningún año. Los años 2021-2023 (N chico, ya negativos antes del
    veto) no se corrigen -- el veto no es una solución mágica, achica
    un riesgo real, no elimina la varianza de la muestra chica.

    LIMITACIÓN HONESTA: el umbral 50%/3 intentos se fijó revisando el
    resultado completo (no walk-forward), así que hay riesgo real de
    sobreajuste a esta muestra -- el mismo patrón que ya mordió a
    "strict_edge" en esta sesión. NO tratar como validado para
    despliegue sin walk-forward real (elegir el umbral solo con datos
    de entrenamiento, evaluar en el año siguiente)."""
    cases, skipped = collect_cases()
    by_year = {}
    for year in sorted({int(c["date"][:4]) for c in cases}):
        yearly = [c for c in cases if int(c["date"][:4]) == year]
        if len(yearly) >= 5:
            by_year[str(year)] = simulate(yearly, initial_capital)
    return {
        "strategy": "BetOnFavourite + giant-killer veto",
        "filters": {
            "rank_gap_by_tour": RANK_GAP_BY_TOUR,
            "surface_winrate_lookback_days": 365,
            "min_surface_matches": 3,
            "min_recent_matches": 5,
            "max_underdog_upset_rate": MAX_UNDERDOG_UPSET_RATE,
            "min_upset_attempts": MIN_UPSET_ATTEMPTS,
            "upset_elo_gap": UPSET_ELO_GAP,
        },
        "n_cases": len(cases),
        "date_range": [cases[0]["date"], cases[-1]["date"]] if cases else None,
        "skipped": skipped,
        "full_sample": simulate(cases, initial_capital),
        "by_year": by_year,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run_backtest(), indent=2, ensure_ascii=False))
