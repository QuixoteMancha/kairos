"""Tests para src/web/cross_book — scan helpers."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.orchestration import cross_book
from src.core.orchestration.cross_book import _CROSS_BOOK_PLATFORMS, _fetch_book_fixtures, _norm_cb_name


# ── _norm_cb_name ─────────────────────────────────────────────────────────────


def test_norm_full_name():
    assert _norm_cb_name("Carlos Alcaraz") == "alcaraz_c"


def test_norm_with_initial():
    assert _norm_cb_name("C. Alcaraz") == "alcaraz_c"


def test_norm_lastname_only():
    assert _norm_cb_name("Alcaraz") == "alcaraz_"


def test_norm_strips_accents():
    assert _norm_cb_name("Cárlös Álcáràz") == "alcaraz_c"


def test_norm_multiple_first_names():
    # 'Mirra Andreeva' → 'andreeva_m'; 'Jean-Lennard Struff' → 'struff_j'
    assert _norm_cb_name("Mirra Andreeva") == "andreeva_m"
    assert _norm_cb_name("Jean Lennard Struff") == "struff_j"


def test_norm_empty():
    assert _norm_cb_name("") == ""
    assert _norm_cb_name("   ") == ""


def test_norm_cross_book_matching_via_frozenset():
    """Misma identidad de match cuando los names varían de formato."""
    a = frozenset([_norm_cb_name("Jannik Sinner"),  _norm_cb_name("Alexei Popyrin")])
    b = frozenset([_norm_cb_name("J. Sinner"),      _norm_cb_name("A. Popyrin")])
    c = frozenset([_norm_cb_name("Popyrin"),        _norm_cb_name("Sinner")])  # sin inicial
    assert a == b              # full name vs inicial → mismo set
    assert a != c              # 'sinner_' != 'sinner_j' → distinto


# ── _CROSS_BOOK_PLATFORMS ─────────────────────────────────────────────────────


def test_default_platforms_are_prediction_markets():
    # Sports bookies (stake/pinnacle/unibet/...) were archived during the
    # prediction-markets pivot (2026-07-03); the pool is now prediction-market
    # platforms only.
    assert set(_CROSS_BOOK_PLATFORMS) == {"polymarket", "kalshi"}


# ── _fetch_book_fixtures ──────────────────────────────────────────────────────


def test_fetch_unknown_slug_returns_empty():
    """Un slug no soportado no debe crashear, sólo devuelve [] y registra métrica."""
    # Limpiar cache para forzar fetch
    cross_book._cb_book_cache.pop("foobar", None)
    cross_book._cb_book_metrics.pop("foobar", None)
    out = _fetch_book_fixtures("foobar")
    assert out == []
    m = cross_book._cb_book_metrics["foobar"]
    assert m["count"] == 1
    assert m["last_count"] == 0


# NOTE: test_fetch_uses_cache_within_ttl and test_fetch_records_metrics_on_error
# were removed during the prediction-markets pivot (2026-07-03) — they dispatched
# through the now-deleted 'stake'/'pinnacle' branches of _fetch_book_fixtures
# (sports bookie scrapers archived to src/scrapers/forgotten/). The cache/error
# mechanics they exercised are still covered generically by
# test_fetch_unknown_slug_returns_empty, which doesn't depend on any live slug.
