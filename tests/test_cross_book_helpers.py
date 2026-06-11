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


def test_default_platforms_contain_stake_pinnacle_unibet():
    assert set(_CROSS_BOOK_PLATFORMS) >= {"stake", "pinnacle", "unibet"}


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


def test_fetch_uses_cache_within_ttl():
    """Llamadas consecutivas dentro del TTL no deben re-llamar al scraper."""
    cross_book._cb_book_cache.pop("stake", None)
    cross_book._cb_book_metrics.pop("stake", None)
    fake_fixtures = ["fx1", "fx2", "fx3"]

    call_count = {"n": 0}

    class _FakeScraper:
        def __enter__(self):  return self
        def __exit__(self, *a): return False
        def get_tennis_fixtures(self):
            call_count["n"] += 1
            return fake_fixtures

    with patch("src.scrapers.stake_scraper.StakeScraper", _FakeScraper):
        a = _fetch_book_fixtures("stake")
        b = _fetch_book_fixtures("stake")
        c = _fetch_book_fixtures("stake")
    assert a == b == c == fake_fixtures
    assert call_count["n"] == 1, "second/third calls should hit cache"


def test_fetch_records_metrics_on_error():
    """Si el scraper crashea, fetch devuelve [] y registra last_error."""
    cross_book._cb_book_cache.pop("pinnacle", None)
    cross_book._cb_book_metrics.pop("pinnacle", None)

    class _BoomScraper:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get_tennis_fixtures(self):
            raise RuntimeError("boom")

    with patch("src.scrapers.pinnacle_scraper.PinnacleScraper", _BoomScraper):
        out = _fetch_book_fixtures("pinnacle")
    assert out == []
    m = cross_book._cb_book_metrics["pinnacle"]
    assert m["errors"] >= 1
    assert m["last_error"] is not None
    assert "boom" in m["last_error"]
