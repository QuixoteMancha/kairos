"""Parseo de las estadísticas crudas de Flashscore a las columnas
TIPADAS de `MatchStats`/`MatchStatsSet` -- 2026-08-16.

POR QUÉ EXISTE: `save_match_stats`/`save_match_stats_for_history`
guardaban ÚNICAMENTE `raw_stats_json`. Las ~40 columnas tipadas que los
modelos definen (`aces_a`, `first_serve_pct_a`, `break_points_won_a`...)
**no las poblaba nadie** -- confirmado con datos reales en producción:
42 filas con JSON completo y 0 con `aces_a`. Y `MatchStatsSet` nunca
recibía filas, pese a que el feed SÍ trae `set_1`/`set_2`/`set_3`.

Consecuencia práctica: un backtest que quisiera filtrar por saque tenía
que traer todas las filas y parsear JSON en Python, en vez de un
`WHERE aces_a > 10`. Las columnas existen justamente para evitar eso.

FORMATOS REALES (medidos sobre las filas de producción, no supuestos):
  "2"              entero pelado           -> Aces, Double Faults, Winners
  "75%"            porcentaje              -> 1st serve percentage
  "58% (32/55)"    porcentaje + fracción   -> 1st serve points won, games won
  "4/7"            fracción sola           -> Break Points Saved/Converted
  "182 km/h"       número con unidad       -> velocidades (no mapeadas hoy)

Fail-closed en todo: cualquier valor que no matchee un formato conocido
deja la columna en NULL en vez de guardar un número inventado.
"""

from __future__ import annotations

import re
from typing import Any

_INT_RE = re.compile(r"^\s*(-?\d+)\s*$")
_PCT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*%")
_FRACTION_RE = re.compile(r"(\d+)\s*/\s*(\d+)")


def parse_int(raw: Any) -> int | None:
    """Entero pelado ("2"). Fail-closed ante cualquier otra cosa."""
    if raw is None:
        return None
    match = _INT_RE.match(str(raw))
    return int(match.group(1)) if match else None


def parse_pct(raw: Any) -> float | None:
    """Porcentaje inicial ("75%", "58% (32/55)") -> 75.0 / 58.0."""
    if raw is None:
        return None
    match = _PCT_RE.match(str(raw))
    return float(match.group(1)) if match else None


def parse_fraction(raw: Any) -> tuple[int | None, int | None]:
    """Fracción ("4/7", "58% (32/55)") -> (numerador, denominador).
    (None, None) si no hay fracción real en el valor."""
    if raw is None:
        return (None, None)
    match = _FRACTION_RE.search(str(raw))
    if match is None:
        return (None, None)
    return (int(match.group(1)), int(match.group(2)))


# Nombre REAL del feed -> (columna base, cómo extraer el valor).
# Verificado contra las estadísticas que Flashscore devuelve de verdad
# (ver docstring). Lo que no está acá se conserva igual en
# `raw_stats_json` -- no se pierde nada, solo no tiene columna propia.
_INT_STATS = {
    "Aces": "aces",
    "Double Faults": "double_faults",
    "Winners": "winners",
    "Unforced errors": "unforced_errors",
}

_PCT_STATS = {
    "1st serve percentage": "first_serve_pct",
    "1st serve points won": "first_serve_points_won_pct",
    "2nd serve points won": "second_serve_points_won_pct",
    # 2026-09-05, pedido explícito del usuario ("qué otras stats
    # podríamos guardar" -> "prográmalo") -- medido en producción: 89%
    # de cobertura (mismo feed, mismo formato "44% (33/75)" que las de
    # saque de arriba), nunca se habían mapeado a columna tipada pese a
    # venir gratis en `raw_stats_json` desde siempre.
    "1st return points won": "first_return_points_won_pct",
    "2nd return points won": "second_return_points_won_pct",
}

# Estadísticas cuyo valor útil es la FRACCIÓN: (columna del numerador,
# columna del denominador o None si no interesa).
_FRACTION_STATS = {
    "Break Points Saved": ("break_points_saved", "break_points_faced"),
    "Break Points Converted": ("break_points_won", None),
    "Total Points Won": ("total_points_won", None),
    "Service games won": ("service_games_won", "serve_games_played"),
    "Return games won": ("return_games_won", None),
}


def typed_columns_from_stats(section: dict[str, Any]) -> dict[str, Any]:
    """Convierte UNA sección del feed (`match`, `set_1`, ...) en el dict
    de columnas tipadas listo para `Model.create(**cols)`.

    Cada estadística trae `[valor_jugador_a, valor_jugador_b]`. Los
    valores que no parseen quedan fuera del dict (columna NULL), nunca
    se inventa un número."""
    columns: dict[str, Any] = {}
    if not isinstance(section, dict):
        return columns

    for stat_name, raw_pair in section.items():
        if not isinstance(raw_pair, (list, tuple)) or len(raw_pair) != 2:
            continue
        value_a, value_b = raw_pair

        if stat_name in _INT_STATS:
            base = _INT_STATS[stat_name]
            for suffix, raw in (("a", value_a), ("b", value_b)):
                parsed = parse_int(raw)
                if parsed is not None:
                    columns[f"{base}_{suffix}"] = parsed

        elif stat_name in _PCT_STATS:
            base = _PCT_STATS[stat_name]
            for suffix, raw in (("a", value_a), ("b", value_b)):
                parsed = parse_pct(raw)
                if parsed is not None:
                    columns[f"{base}_{suffix}"] = parsed

        elif stat_name in _FRACTION_STATS:
            num_col, den_col = _FRACTION_STATS[stat_name]
            for suffix, raw in (("a", value_a), ("b", value_b)):
                numerator, denominator = parse_fraction(raw)
                if numerator is not None:
                    columns[f"{num_col}_{suffix}"] = numerator
                if den_col and denominator is not None:
                    columns[f"{den_col}_{suffix}"] = denominator

    return columns


def set_number_from_key(key: str) -> int | None:
    """"set_1" -> 1. Fail-closed: None para "match" o claves raras."""
    match = re.fullmatch(r"set_(\d+)", str(key).strip())
    return int(match.group(1)) if match else None
