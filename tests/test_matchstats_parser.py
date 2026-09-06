"""Tests del parseo de estadísticas crudas a columnas TIPADAS --
2026-08-16. Las columnas existían en el modelo pero NADIE las poblaba:
42/42 filas de producción tenían `aces_a` en NULL con el JSON completo
al lado, y `MatchStatsSet` estaba en 0.

Los formatos testeados son los REALES medidos sobre las filas de
producción, no inventados."""

from __future__ import annotations

import pytest

from database.historical.tennis.matchstats_parser import (
    parse_fraction, parse_int, parse_pct, set_number_from_key,
    typed_columns_from_stats,
)


# --- parsers individuales ---

@pytest.mark.parametrize("raw,expected", [
    ("2", 2), ("8", 8), ("0", 0),
    ("75%", None),          # porcentaje no es entero pelado
    ("4/7", None),          # fracción tampoco
    ("182 km/h", None),     # número con unidad tampoco
    (None, None), ("", None), ("-", None),
])
def test_parse_int(raw, expected):
    assert parse_int(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("75%", 75.0),
    ("58% (32/55)", 58.0),
    ("100%", 100.0),
    ("2", None), ("4/7", None), (None, None),
])
def test_parse_pct(raw, expected):
    assert parse_pct(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("4/7", (4, 7)),
    ("58% (32/55)", (32, 55)),
    ("0/1", (0, 1)),
    ("75%", (None, None)), ("2", (None, None)), (None, (None, None)),
])
def test_parse_fraction(raw, expected):
    assert parse_fraction(raw) == expected


def test_set_number_from_key():
    assert set_number_from_key("set_1") == 1
    assert set_number_from_key("set_3") == 3
    assert set_number_from_key("match") is None
    assert set_number_from_key("raro") is None


# --- mapeo completo, con la sección real de un partido de producción ---

_REAL_SECTION = {
    "Aces": ["2", "8"],
    "Double Faults": ["3", "3"],
    "1st serve percentage": ["75%", "63%"],
    "1st serve points won": ["58% (32/55)", "81% (29/36)"],
    "2nd serve points won": ["44% (8/18)", "57% (12/21)"],
    "1st return points won": ["27% (10/37)", "35% (12/34)"],
    "2nd return points won": ["56% (24/43)", "38% (11/29)"],
    "Break Points Saved": ["4/7", "0/1"],
    "Break Points Converted": ["1/1", "3/7"],
    "Total Points Won": ["43% (56/130)", "57% (74/130)"],
    "Service games won": ["70% (7/10)", "90% (9/10)"],
    "Return games won": ["10% (1/10)", "30% (3/10)"],
    "Winners": ["9", "29"],
    "Unforced errors": ["20", "24"],
    # Estas NO tienen columna propia -- deben ignorarse sin romper.
    "Average 1st serve speed": ["182 km/h", "199 km/h"],
    "Distance covered (metres)": ["1519", "1666"],
    "Last 10 balls": ["3", "7"],
}


def test_typed_columns_from_real_section():
    cols = typed_columns_from_stats(_REAL_SECTION)

    assert cols["aces_a"] == 2 and cols["aces_b"] == 8
    assert cols["double_faults_a"] == 3
    assert cols["winners_a"] == 9 and cols["winners_b"] == 29
    assert cols["unforced_errors_b"] == 24
    assert cols["first_serve_pct_a"] == 75.0 and cols["first_serve_pct_b"] == 63.0
    assert cols["first_serve_points_won_pct_a"] == 58.0
    assert cols["second_serve_points_won_pct_b"] == 57.0
    assert cols["first_return_points_won_pct_a"] == 27.0
    assert cols["second_return_points_won_pct_b"] == 38.0
    # "4/7" -> salvados 4, enfrentados 7
    assert cols["break_points_saved_a"] == 4 and cols["break_points_faced_a"] == 7
    assert cols["break_points_won_b"] == 3
    # "70% (7/10)" -> ganados 7, jugados 10
    assert cols["service_games_won_a"] == 7 and cols["serve_games_played_a"] == 10
    assert cols["return_games_won_b"] == 3
    # "43% (56/130)" -> el numerador es el dato real de puntos ganados
    assert cols["total_points_won_a"] == 56 and cols["total_points_won_b"] == 74


def test_total_points_won_is_internally_consistent():
    """Chequeo de cordura real: los puntos ganados por ambos suman el
    total jugado (denominador de la fracción)."""
    cols = typed_columns_from_stats(_REAL_SECTION)
    assert cols["total_points_won_a"] + cols["total_points_won_b"] == 130


def test_unmapped_stats_are_ignored_without_crashing():
    cols = typed_columns_from_stats(_REAL_SECTION)
    assert not any("speed" in k or "distance" in k for k in cols)


def test_fail_closed_on_garbage_and_bad_shapes():
    assert typed_columns_from_stats({}) == {}
    assert typed_columns_from_stats(None) == {}
    # valor que no es un par [a, b]
    assert typed_columns_from_stats({"Aces": "2"}) == {}
    assert typed_columns_from_stats({"Aces": ["2"]}) == {}
    # valores no parseables -> columna ausente, nunca un número inventado
    assert typed_columns_from_stats({"Aces": ["-", "n/a"]}) == {}


def test_partial_values_keep_the_side_that_parsed():
    """Si un lado no parsea, el otro igual se guarda -- fail-closed por
    valor, no por estadística entera."""
    cols = typed_columns_from_stats({"Aces": ["5", "-"]})
    assert cols == {"aces_a": 5}
