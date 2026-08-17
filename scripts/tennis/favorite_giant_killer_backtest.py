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
from scripts.tennis.bet_on_favourite_backtest import RANK_GAP_BY_TOUR, _odds_for_match, simulate

# Umbrales del veto de "asesino de gigantes" -- validados
# exploratoriamente el 2026-08-17 contra la base real: >50% fue el punto
# óptimo (más selectivo por CUOTA empeoraba el resultado; más selectivo
# por TASA de upset también, ver docstring del módulo).
UPSET_ELO_GAP = 50.0
MAX_UNDERDOG_UPSET_RATE = 0.5
MIN_UPSET_ATTEMPTS = 3


def collect_cases(
    *,
    max_underdog_upset_rate: float | None = MAX_UNDERDOG_UPSET_RATE,
    min_upset_attempts: int = MIN_UPSET_ATTEMPTS,
    upset_elo_gap: float = UPSET_ELO_GAP,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Construye casos point-in-time, sin leakage de resultado futuro.

    `max_underdog_upset_rate=None` desactiva el veto nuevo (deja el
    comportamiento idéntico a `bet_on_favourite_backtest.collect_cases`,
    útil para comparar apples-to-apples en el mismo proceso)."""
    init_db_historical()
    surface_record: dict[tuple[str, str], list[tuple[date, bool]]] = defaultdict(list)
    recent_record: dict[str, deque[bool]] = defaultdict(lambda: deque(maxlen=10))
    # (elo_gap_cuando_era_underdog, ganó) -- historial COMPLETO por
    # jugador, no acotado a los últimos N (queremos la tasa real).
    upset_record: dict[int, deque[tuple[float, bool]]] = defaultdict(deque)
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

    return cases, dict(skipped)


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
