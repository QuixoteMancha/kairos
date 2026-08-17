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
_SUP_TAG_RE = re.compile(r"<sup>.*?</sup>", re.DOTALL)


def parse_set_scores(score: str | None) -> list[tuple[int, int]] | None:
    """Parsea "6-4, 3-6, 6-2" -> [(6,4), (3,6), (6,2)], siempre desde la
    perspectiva del jugador cuyo historial se está mirando (mismo
    convenio que `flashscore_scraper.get_player_recent_matches`, que ya
    orienta el score a "propios-rivales" antes de guardarlo).

    2026-08-17, bug real encontrado ANTES de construir el filtro de
    resiliencia point-in-time que pidió el usuario: esta función nunca
    funcionó contra el formato real de `MatchHistory.score` -- separador
    real es COMA, no espacio (`"6-1, 6-1"`), y el marcador de tiebreak
    trae el tag `<sup>N</sup>` sin limpiar (`"7-6<sup>3</sup>, 6-1"`,
    mismo patrón ya encontrado y arreglado en el parser de Flashscore
    2026-08-12). Verificado: `parse_set_scores` devolvía `None` para
    el 100% de los scores reales de producción, nunca se detectó porque
    el módulo entero nunca se deployó. Ahora: se quitan los tags
    `<sup>` (igual que en `flashscore_scraper._parse_score_sets`) y se
    separa por coma.

    Fail-closed: None si el string está vacío, tiene un token que no
    matchea "N-N", o tiene menos de 2 sets (partido no resuelto / walkover
    sin secuencia completa) -- nunca se rellena un set faltante."""
    if not score:
        return None
    cleaned = _SUP_TAG_RE.sub("", score)
    sets: list[tuple[int, int]] = []
    for token in cleaned.split(","):
        token = token.strip()
        m = _SET_RE.match(token)
        if not m:
            return None
        sets.append((int(m.group(1)), int(m.group(2))))
    return sets if len(sets) >= 2 else None


def get_set_tendencies(player_name: str, n: int = 30, surface: str | None = None) -> dict[str, Any] | None:
    """Tendencias de secuencia de sets sobre los últimos `n` partidos con
    historial parseable de `player_name`.

    Si `surface` se provee, solo usa partidos en esa superficie.

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
    n_after_losing_set1_winning_set2 = 0
    won_set3_after_losing_set1_winning_set2 = 0
    n_after_winning_set1_winning_set2 = 0
    won_set3_after_winning_set1_winning_set2 = 0
    n_after_losing_set1_losing_set2 = 0
    won_set3_after_losing_set1_losing_set2 = 0
    n_after_winning_set1_losing_set2 = 0
    won_set3_after_winning_set1_losing_set2 = 0
    n_parseable = 0

    for m in matches:
        if surface and m.get("surface") != surface:
            continue

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

        if len(sets) < 3:
            continue

        s3_own, s3_opp = sets[2]
        if s3_own == s3_opp:
            continue  # score de set3 inválido, omitimos del branch

        won_set3 = s3_own > s3_opp
        if won_set1 and won_set2:
            n_after_winning_set1_winning_set2 += 1
            if won_set3:
                won_set3_after_winning_set1_winning_set2 += 1
        elif won_set1 and not won_set2:
            n_after_winning_set1_losing_set2 += 1
            if won_set3:
                won_set3_after_winning_set1_losing_set2 += 1
        elif not won_set1 and won_set2:
            n_after_losing_set1_winning_set2 += 1
            if won_set3:
                won_set3_after_losing_set1_winning_set2 += 1
        else:
            n_after_losing_set1_losing_set2 += 1
            if won_set3:
                won_set3_after_losing_set1_losing_set2 += 1

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
        "p_win_set3_after_losing_set1_winning_set2": (
            round(won_set3_after_losing_set1_winning_set2 / n_after_losing_set1_winning_set2, 4)
            if n_after_losing_set1_winning_set2 else None
        ),
        "p_win_set3_after_winning_set1_winning_set2": (
            round(won_set3_after_winning_set1_winning_set2 / n_after_winning_set1_winning_set2, 4)
            if n_after_winning_set1_winning_set2 else None
        ),
        "p_win_set3_after_losing_set1_losing_set2": (
            round(won_set3_after_losing_set1_losing_set2 / n_after_losing_set1_losing_set2, 4)
            if n_after_losing_set1_losing_set2 else None
        ),
        "p_win_set3_after_winning_set1_losing_set2": (
            round(won_set3_after_winning_set1_losing_set2 / n_after_winning_set1_losing_set2, 4)
            if n_after_winning_set1_losing_set2 else None
        ),
        "n_matches": n_parseable,
        "n_after_losing_set1": n_after_losing_set1,
        "n_after_winning_set1": n_after_winning_set1,
        "n_after_losing_set1_winning_set2": n_after_losing_set1_winning_set2,
        "n_after_winning_set1_winning_set2": n_after_winning_set1_winning_set2,
        "n_after_losing_set1_losing_set2": n_after_losing_set1_losing_set2,
        "n_after_winning_set1_losing_set2": n_after_winning_set1_losing_set2,
    }


def get_set_win_rates(player_name: str, n: int = 30, surface: str | None = None) -> dict[str, Any] | None:
    """Winrates por set en los últimos `n` partidos del jugador.

    Si `surface` se provee, el cálculo se restringe a esa superficie.

    Returns:
        {"p_win_set1": float | None, "p_win_set2": float | None,
         "p_win_set3": float | None, "n_matches": int,
         "n_set1": int, "n_set2": int, "n_set3": int}
        o None si no hay matches parseables.

    Fail-closed: si no hay partidos con score parseable, devuelve None.
    """
    from outer_apis.sports.tennis.match_history import get_recent_matches

    matches = get_recent_matches(player_name, n=n)
    if not matches:
        return None

    set1_wins = set2_wins = set3_wins = 0
    set1_total = set2_total = set3_total = 0
    n_parseable = 0

    for m in matches:
        if surface and m.get("surface") != surface:
            continue

        sets = parse_set_scores(m.get("score"))
        if sets is None:
            continue

        n_parseable += 1
        if len(sets) >= 1:
            set1_total += 1
            if sets[0][0] > sets[0][1]:
                set1_wins += 1
        if len(sets) >= 2:
            set2_total += 1
            if sets[1][0] > sets[1][1]:
                set2_wins += 1
        if len(sets) >= 3:
            set3_total += 1
            if sets[2][0] > sets[2][1]:
                set3_wins += 1

    if n_parseable == 0:
        return None

    return {
        "p_win_set1": round(set1_wins / set1_total, 4) if set1_total else None,
        "p_win_set2": round(set2_wins / set2_total, 4) if set2_total else None,
        "p_win_set3": round(set3_wins / set3_total, 4) if set3_total else None,
        "n_matches": n_parseable,
        "n_set1": set1_total,
        "n_set2": set2_total,
        "n_set3": set3_total,
    }
