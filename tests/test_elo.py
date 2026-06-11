#!/usr/bin/env python3
"""
Tests para el módulo de cálculos matemáticos Elo
"""

import math

import pytest

from src.core.math.elo import EloCalculator, EloResult, calculate_elo_update


class TestEloCalculator:
    """Tests para la calculadora Elo"""

    def test_expected_score_symmetry(self):
        """La puntuación esperada debe ser simétrica"""
        # Dos jugadores con mismo rating deben tener 0.5 de puntuación esperada
        assert EloCalculator.expected_score(1500, 1500) == 0.5

        # Si A es mucho mejor que B, E_A debe ser > 0.5
        assert EloCalculator.expected_score(1800, 1400) > 0.5
        assert EloCalculator.expected_score(1400, 1800) < 0.5

    def test_expected_score_formula(self):
        """Verificar la fórmula matemática exacta"""
        rating_a, rating_b = 1600, 1500
        expected = 1 / (1 + math.pow(10, (rating_b - rating_a) / 400))
        assert EloCalculator.expected_score(rating_a, rating_b) == expected

    def test_update_rating_win(self):
        """Test cuando un jugador gana"""
        result = EloCalculator.update_rating(1500, 1400, 1.0)

        # Debe ganar puntos
        assert result.rating_change > 0
        assert result.new_rating > 1500
        assert result.actual_score == 1.0
        assert 0 < result.expected_score < 1

    def test_update_rating_loss(self):
        """Test cuando un jugador pierde"""
        result = EloCalculator.update_rating(1500, 1600, 0.0)

        # Debe perder puntos
        assert result.rating_change < 0
        assert result.new_rating < 1500
        assert result.actual_score == 0.0
        assert 0 < result.expected_score < 1

    def test_simulate_match(self):
        """Test simulación completa de partido"""
        result_a, result_b = EloCalculator.simulate_match(1500, 1500, winner='A')

        # Los cambios deben ser opuestos
        assert result_a.rating_change == -result_b.rating_change
        assert result_a.new_rating == 1500 + result_a.rating_change
        assert result_b.new_rating == 1500 + result_b.rating_change

        # Scores deben ser correctos
        assert result_a.actual_score == 1.0
        assert result_b.actual_score == 0.0

    def test_invalid_scores(self):
        """Test validación de scores inválidos"""
        with pytest.raises(ValueError, match="score actual debe estar entre 0 y 1"):
            EloCalculator.update_rating(1500, 1500, 1.5)

        with pytest.raises(ValueError, match="score actual debe estar entre 0 y 1"):
            EloCalculator.update_rating(1500, 1500, -0.1)

    def test_negative_ratings(self):
        """Test validación de ratings negativos"""
        with pytest.raises(ValueError, match="ratings no pueden ser negativos"):
            EloCalculator.update_rating(-100, 1500, 1.0)

        with pytest.raises(ValueError, match="ratings no pueden ser negativos"):
            EloCalculator.update_rating(1500, -100, 1.0)

    def test_calculate_elo_update_function(self):
        """Test función de conveniencia"""
        new_rating = calculate_elo_update(1500, 1400, True)
        assert new_rating > 1500

        new_rating = calculate_elo_update(1500, 1600, False)
        assert new_rating < 1500

    def test_custom_k_factor(self):
        """Test con factor K personalizado"""
        result_default = EloCalculator.update_rating(1500, 1500, 1.0)
        result_custom = EloCalculator.update_rating(1500, 1500, 1.0, k_factor=16)

        # Con K más pequeño, el cambio debe ser la mitad
        assert abs(result_custom.rating_change) == abs(result_default.rating_change) / 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
