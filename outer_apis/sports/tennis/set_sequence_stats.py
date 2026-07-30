"""Tendencias de secuencia de sets por jugador/a: P(ganar set 1), P(ganar
set 2 | perdió el set 1) ("comeback"), P(ganar set 2 | ganó el set 1)
("cerrar el partido"). Construido 2026-07-30 a pedido explícito del
usuario para capturar diferencias entre jugadoras con el mismo ranking
pero distinta capacidad de remontar/cerrar sets -- algo que el Markov de
`tennis_markov.py` no modela (asume el mismo `p_serve` set tras set,
independiente del historial de sets del partido en curso).

Fuente de datos: el historial de partidos ya fetcheado/persistido de
Flashscore (`outer_apis/sports/tennis/match_history.py` -> DB-first, ver
`database/historical/tennis/writer.py::get_recent_match_history`), que ya
trae el campo `score` como string "6-4 3-6 6-2" (más reciente primero).
Ningún fetch nuevo — es una transformación sobre datos que Kairos ya
recolecta.

Fail-closed: cualquier partido cuyo `score` no parsee limpio a >=2 sets
completos se descarta de la muestra (nunca se adivina un resultado de set
faltante o mal formado, ej. walkovers/retiros con score parcial). Si la
muestra de partidos a 3+ sets es menor a `_MIN_MATCHES`, la función
correspondiente devuelve None -- no hay señal suficiente para confiar en
la tendencia."""

from __future__ import annotations

import re
from typing import Any

_MIN_MATCHES = 5  # fail-closed: menos partidos utilizables -> sin señal confiable.

_SET_RE = re.compile(r"^(\d+)-(\d+)$")


def parse_set_scores(score: str | None) -> list[tuple[int, int]] | None:
    """Parsea "6-4 3-6 6-2" -> [(6,4), (3,6), (6,2)], siempre desde la
    perspectiva del jugador cuyo historial se está mirando (mismo
    convenio que `flashscore_scraper.get_player_recent_matches`, que ya
    orienta el score a "propios-rivales" antes de guardarlo).

    Fail-closed: None si el string está vacío, tiene un token que no
    matchea "N-N", o tiene menos de 2 sets (partido no resuelto / walkover
    sin secuencia completa) -- nunca se rellena un set faltante."""
    if not score:
        return None
    sets: list[tuple[int, int]] = []
    for token in score.split():
        m = _SET_RE.match(token)
        if not m:
            return None
        sets.append((int(m.group(1)), int(m.group(2))))
    return sets if len(sets) >= 2 else None


def get_set_tendencies(player_name: str, n: int = 30) -> dict[str, Any] | None:
    """Tendencias de secuencia de sets sobre los últimos `n` partidos con
    historial parseable de `player_name`.

    Returns:
        {"p_win_set1": float, "p_win_set2_after_losing_set1": float | None,
         "p_win_set2_after_winning_set1": float | None,
         "n_matches": int, "n_after_losing_set1": int,
         "n_after_winning_set1": int}
        o None si hay menos de `_MIN_MATCHES` partidos parseables en total.

        Los dos campos condicionales son independientemente None si esa
        rama específica (perdió/ganó el set 1) no tiene al menos
        `_MIN_MATCHES` observaciones -- fail-closed por sub-muestra, no
        solo por la muestra total."""
    from outer_apis.sports.tennis.match_history import get_recent_matches

    matches = get_recent_matches(player_name, n=n)
    if not matches:
        return None

    won_set1_count = 0
    won_set2_after_losing_set1 = 0
    n_after_losing_set1 = 0
    won_set2_after_winning_set1 = 0
    n_after_winning_set1 = 0
    n_parseable = 0

    for m in matches:
        sets = parse_set_scores(m.get("score"))
        if sets is None or len(sets) < 2:
            continue
        n_parseable += 1

        (s1_own, s1_opp), (s2_own, s2_opp) = sets[0], sets[1]
        if s1_own == s1_opp:
            continue  # score de set inválido (no debería pasar, defensivo)

        won_set1 = s1_own > s1_opp
        if won_set1:
            won_set1_count += 1

        if s2_own == s2_opp:
            continue  # set 2 sin resolver limpio, se descarta de las ramas condicionales

        won_set2 = s2_own > s2_opp
        if won_set1:
            n_after_winning_set1 += 1
            if won_set2:
                won_set2_after_winning_set1 += 1
        else:
            n_after_losing_set1 += 1
            if won_set2:
                won_set2_after_losing_set1 += 1

    if n_parseable < _MIN_MATCHES:
        return None

    return {
        "p_win_set1": round(won_set1_count / n_parseable, 4),
        "p_win_set2_after_losing_set1": (
            round(won_set2_after_losing_set1 / n_after_losing_set1, 4)
            if n_after_losing_set1 >= _MIN_MATCHES else None
        ),
        "p_win_set2_after_winning_set1": (
            round(won_set2_after_winning_set1 / n_after_winning_set1, 4)
            if n_after_winning_set1 >= _MIN_MATCHES else None
        ),
        "n_matches": n_parseable,
        "n_after_losing_set1": n_after_losing_set1,
        "n_after_winning_set1": n_after_winning_set1,
    }
