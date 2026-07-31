"""Tests de la consulta ponderada de volatilidad observada real
(data reciente + histórica archivada, más peso a la reciente) --
2026-07-31, pedido explícito del usuario, punto 3."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from outer_apis.sports.tennis.volatility_calibration import (
    _match_volatility, get_weighted_observed_volatility, MIN_MATCHES_FOR_STAT,
)


def test_match_volatility_none_with_fewer_than_2_prices():
    assert _match_volatility([0.5]) is None
    assert _match_volatility([]) is None


def test_match_volatility_zero_for_constant_price():
    assert _match_volatility([0.5, 0.5, 0.5]) == 0.0


def test_match_volatility_matches_population_stddev():
    # precios 0.4/0.6 -- media 0.5, varianza = ((0.1)^2+(0.1)^2)/2 = 0.01, std=0.1
    assert _match_volatility([0.4, 0.6]) == pytest.approx(0.1)


def test_get_weighted_observed_volatility_none_when_no_source_has_data():
    with patch("outer_apis.sports.tennis.volatility_calibration.get_recent_observed_volatility", return_value=None), \
         patch("outer_apis.sports.tennis.volatility_calibration.get_archived_observed_volatility", return_value=None):
        assert get_weighted_observed_volatility("Hard", "ATP") is None


def test_get_weighted_observed_volatility_falls_back_to_recent_only():
    recent = {"mean_volatility": 0.20, "n_matches": 10}
    with patch("outer_apis.sports.tennis.volatility_calibration.get_recent_observed_volatility", return_value=recent), \
         patch("outer_apis.sports.tennis.volatility_calibration.get_archived_observed_volatility", return_value=None):
        result = get_weighted_observed_volatility("Hard", "ATP")
    assert result["sources_used"] == "recent_only"
    assert result["mean_volatility"] == 0.20


def test_get_weighted_observed_volatility_favors_recent_over_archived():
    recent = {"mean_volatility": 0.30, "n_matches": 5}
    archived = {"mean_volatility": 0.10, "n_matches": 500}  # muestra MUCHO más grande, pero menos peso a propósito
    with patch("outer_apis.sports.tennis.volatility_calibration.get_recent_observed_volatility", return_value=recent), \
         patch("outer_apis.sports.tennis.volatility_calibration.get_archived_observed_volatility", return_value=archived):
        result = get_weighted_observed_volatility("Hard", "ATP", recent_weight=0.8)

    expected = 0.8 * 0.30 + 0.2 * 0.10
    assert result["mean_volatility"] == pytest.approx(round(expected, 4))
    assert result["sources_used"] == "both"
    # el resultado queda mucho más cerca de "recent" (0.30) que de un
    # promedio simple ponderado por muestra (que daría ~0.104, dominado
    # por los 500 partidos archivados) -- confirma que NO es un promedio
    # ponderado por tamaño de muestra.
    assert result["mean_volatility"] > 0.25
