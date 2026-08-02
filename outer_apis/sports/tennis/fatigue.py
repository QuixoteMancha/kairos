"""Índice de fatiga acumulada por jugador/a (2026-08-01, pedido explícito
del usuario: "considere la fatiga acumulada de ambos players... horas
jugadas en el torneo y en el último torneo + días de descanso respecto
al último torneo y entre partidos recientes... en el índice también se
puede añadir la edad como peso ya que jugadores sobre 30 no toleran bien
tanta fatiga acumulada").

FUENTE DE DATOS: `recent_results_cache.fetch_and_cache` (mismo caché
DB-first que ya usan `injury_risk.py`/`tournament_momentum.py`, sin
fetch adicional) -- cada fila trae `duration_minutes` real (Tennis
Abstract, columna 15 de `recent-results`, agregada 2026-08-01, ver
`tennisabstract_player_scraper.get_recent_results`). Edad real vía
`player_age.get_age` (Tennis Abstract bio page, cache permanente).

MODELO -- HAND-REASONED, NO CALIBRADO (mismo criterio que el resto de
heurísticas de este proyecto, ej. `injury_risk.py`, documentado
explícitamente como tal, no una regresión entrenada):

  hours_current_tournament = suma de duraciones reales (horas) de los
    partidos del torneo ACTUAL jugados hasta ahora (streak desde el más
    reciente hacia atrás, corta en el primer partido de un torneo
    distinto -- mismo patrón de agrupación que
    `tournament_momentum.get_current_tournament_round_wins`, pero sin
    cortar en la primera derrota: jugar cansa aunque se pierda).

  hours_last_tournament = suma de duraciones del torneo INMEDIATAMENTE
    anterior (el próximo bloque de torneo distinto tras el actual).
  rest_days_since_last_tournament = días entre el último partido de ese
    torneo anterior y el partido actual que se está evaluando.
  decay = max(0, 1 - rest_days_since_last_tournament / 14) -- la fatiga
    del torneo anterior se considera totalmente disipada a partir de 14
    días de descanso, decae linealmente antes de eso.

  density_penalty = promedio de 1/(gap_days+1) entre partidos
    CONSECUTIVOS del torneo actual (gap_days=0 -> mismo día, penalty 1.0;
    gap_days=1 -> día siguiente, penalty 0.5; más días entre partidos ->
    penalty menor). 0.0 si es el primer partido del torneo (sin gaps que
    medir todavía).

  raw_fatigue = hours_current_tournament
              + 0.4 * hours_last_tournament * decay
              + 2.0 * density_penalty

  age_multiplier = 1.0 si age es None o <=30, si no
                   min(1.5, 1.0 + 0.05*(age-30)) -- jugadores 30+
                   acumulan hasta 50% más de peso de fatiga a los 40+.

  fatigue_index = raw_fatigue * age_multiplier

Fail-closed: `calculate_fatigue_index` devuelve None si no hay NINGUNA
duración real para el torneo actual (nunca asume 0 horas por ausencia de
dato -- distinto de encontrar partidos reales con 0h, que no debería
pasar en la práctica)."""

from __future__ import annotations

from datetime import date
from typing import Any

from outer_apis.sports.tennis.recent_results_cache import fetch_and_cache
from outer_apis.sports.tennis.player_age import get_age

_LAST_TOURNAMENT_DECAY_DAYS = 14.0
_LAST_TOURNAMENT_WEIGHT = 0.4
_DENSITY_WEIGHT = 2.0
_AGE_THRESHOLD = 30
_AGE_WEIGHT_PER_YEAR = 0.05
_AGE_MULTIPLIER_CAP = 1.5


def _tournament_blocks(rows: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Agrupa `rows` (más reciente primero) en bloques consecutivos del
    mismo torneo -- ej. [(torneo_actual, [...]), (torneo_anterior, [...]), ...]."""
    blocks: list[tuple[str, list[dict[str, Any]]]] = []
    for row in rows:
        tournament = row.get("tournament")
        if not tournament:
            continue
        if blocks and blocks[-1][0] == tournament:
            blocks[-1][1].append(row)
        else:
            blocks.append((tournament, [row]))
    return blocks


def calculate_fatigue_index(
    player_name: str, current_tournament: str, current_match_date: date, age: int | None = None,
) -> dict[str, Any] | None:
    """Índice de fatiga para `player_name` de cara al partido en
    `current_tournament` el `current_match_date`. `age` opcional (si no
    se pasa, se busca vía `player_age.get_age`).

    Fail-closed: None si `current_tournament` no aparece en el historial
    reciente real del jugador (no se puede calcular fatiga del torneo
    actual sin datos reales de ese torneo), o si ninguna de sus filas
    tiene `duration_minutes` real."""
    if not current_tournament:
        return None

    rows = fetch_and_cache(player_name)
    if not rows:
        return None

    blocks = _tournament_blocks(rows)
    tournament_norm = current_tournament.strip().lower()

    current_block = None
    last_block = None
    for name, block_rows in blocks:
        if name.strip().lower() == tournament_norm:
            if current_block is None:
                current_block = block_rows
                continue
        elif current_block is not None and last_block is None:
            last_block = block_rows
            break

    if current_block is None:
        return None

    current_durations = [r["duration_minutes"] for r in current_block if r.get("duration_minutes")]
    if not current_durations:
        return None  # sin ninguna duración real para el torneo actual -- fail-closed

    hours_current = sum(current_durations) / 60.0

    hours_last = 0.0
    rest_days_since_last = None
    decay = 0.0
    if last_block:
        last_durations = [r["duration_minutes"] for r in last_block if r.get("duration_minutes")]
        if last_durations:
            hours_last = sum(last_durations) / 60.0
            last_match_date_str = last_block[0].get("date")
            try:
                last_match_date = date.fromisoformat(last_match_date_str) if last_match_date_str else None
            except ValueError:
                last_match_date = None
            if last_match_date is not None:
                rest_days_since_last = (current_match_date - last_match_date).days
                decay = max(0.0, 1.0 - rest_days_since_last / _LAST_TOURNAMENT_DECAY_DAYS)

    density_penalty = 0.0
    if len(current_block) > 1:
        gaps = []
        for i in range(len(current_block) - 1):
            try:
                d1 = date.fromisoformat(current_block[i]["date"])
                d2 = date.fromisoformat(current_block[i + 1]["date"])
            except (ValueError, TypeError, KeyError):
                continue
            gap_days = (d1 - d2).days
            if gap_days >= 0:
                gaps.append(1.0 / (gap_days + 1))
        if gaps:
            density_penalty = sum(gaps) / len(gaps)

    raw_fatigue = (
        hours_current
        + _LAST_TOURNAMENT_WEIGHT * hours_last * decay
        + _DENSITY_WEIGHT * density_penalty
    )

    if age is None:
        age = get_age(player_name, as_of=current_match_date)
    age_multiplier = 1.0
    if age is not None and age > _AGE_THRESHOLD:
        age_multiplier = min(_AGE_MULTIPLIER_CAP, 1.0 + _AGE_WEIGHT_PER_YEAR * (age - _AGE_THRESHOLD))

    fatigue_index = raw_fatigue * age_multiplier

    return {
        "hours_current_tournament": round(hours_current, 2),
        "hours_last_tournament": round(hours_last, 2),
        "rest_days_since_last_tournament": rest_days_since_last,
        "density_penalty": round(density_penalty, 4),
        "age": age,
        "age_multiplier": round(age_multiplier, 4),
        "raw_fatigue": round(raw_fatigue, 4),
        "fatigue_index": round(fatigue_index, 4),
    }
