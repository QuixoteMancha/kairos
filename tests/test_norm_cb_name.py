"""Tests para _norm_cb_name — normalización de nombres cross-book."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.core.orchestration.cross_book import _norm_cb_name


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Carlos Alcaraz", "alcaraz_c"),
        ("C. Alcaraz", "alcaraz_c"),
        ("Alcaraz", "alcaraz_"),
        ("CARLOS ALCARAZ", "alcaraz_c"),
        ("  Carlos   Alcaraz  ", "alcaraz_c"),
        ("Stefanos Tsitsipas", "tsitsipas_s"),
        ("Novak Djokovic", "djokovic_n"),
        # Acentos → normalizados con NFKD ASCII
        ("Cristian Garín", "garin_c"),
        ("Jérémy Chardy", "chardy_j"),
        # Casos borde
        ("", ""),
        ("   ", ""),
    ],
)
def test_norm_cb_name(raw, expected):
    assert _norm_cb_name(raw) == expected


def test_norm_cb_name_handles_multiple_dots():
    # "J. R. Lopez" → último token = "lopez", primer carácter del primero = "j"
    assert _norm_cb_name("J. R. Lopez") == "lopez_j"
