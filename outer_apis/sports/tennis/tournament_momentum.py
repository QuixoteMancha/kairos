"""Avance de rondas en el torneo ACTUAL como señal de forma reciente --
pedido explícito del usuario 2026-08-01: "el avance de rondas demuestra
que el nivel del player va mejorando, el ranking puede ser engañoso".

Mide cuántas rondas CONSECUTIVAS ganó un jugador/a en el torneo actual
(contando hacia atrás desde el partido más reciente, mientras la
`tournament` coincida y el resultado sea victoria) -- un jugador que
llegó a semifinal ganando 3 rondas seguidas ESTA semana está mostrando
una forma más fuerte de lo que su ranking (una medida lenta, acumulada
durante meses) puede reflejar.

Fuente: mismos datos de `recent-results` de Tennis Abstract que
`injury_risk.py` (reusa el mismo caché DB-first, ver
`recent_results_cache.py`) -- sin fetch adicional."""

from __future__ import annotations

from typing import Any

from outer_apis.sports.tennis.recent_results_cache import fetch_and_cache

MAX_ROUNDS_FOR_NORMALIZATION = 6  # tope razonable (R128->R64->R32->R16->QF->SF->F son 6 rondas)


def get_current_tournament_round_wins(player_name: str, tournament: str | None) -> int | None:
    """Cuenta rondas ganadas SEGUIDAS en `tournament`, empezando por el
    partido más reciente del jugador/a hacia atrás -- se corta en la
    primera derrota, o en el primer partido de un torneo distinto.

    Fail-closed: None si `tournament` es None/vacío (no se sabe en qué
    torneo está el partido actual, ver `outer_apis/kalshi/markets/
    tennis.py`), o si no hay datos reales del jugador."""
    if not tournament:
        return None

    rows = fetch_and_cache(player_name)
    if not rows:
        return None

    tournament_norm = tournament.strip().lower()
    wins = 0
    for row in rows:  # más reciente primero, ya viene ordenado así
        row_tournament = (row.get("tournament") or "").strip().lower()
        if row_tournament != tournament_norm:
            break  # se acabó la racha de ESTE torneo
        if not row.get("subject_won"):
            break  # perdió una ronda -- la racha termina ahí
        wins += 1

    return wins


def get_momentum_implied_win_prob(rounds_a: int | None, rounds_b: int | None) -> float | None:
    """Traduce la diferencia de rondas ganadas a una probabilidad
    implícita simétrica (0.5 = sin diferencia), normalizando cada lado a
    [0,1] sobre `MAX_ROUNDS_FOR_NORMALIZATION` antes de restar -- mismo
    patrón que los demás blends de diferencia (surface-form, career-split
    win%, etc.).

    Fail-closed: None si falta cualquiera de los 2 lados."""
    if rounds_a is None or rounds_b is None:
        return None
    norm_a = min(rounds_a, MAX_ROUNDS_FOR_NORMALIZATION) / MAX_ROUNDS_FOR_NORMALIZATION
    norm_b = min(rounds_b, MAX_ROUNDS_FOR_NORMALIZATION) / MAX_ROUNDS_FOR_NORMALIZATION
    implied_a = 0.5 + (norm_a - norm_b) / 2
    return max(0.0, min(1.0, implied_a))
