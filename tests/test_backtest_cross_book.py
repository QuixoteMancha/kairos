"""Tests para backtest cross-book histórico."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.core.math.backtest_cross_book import _bucket_key, backtest_cross_book


class _FakeSnap:
    """Mock de OddsSnapshot row."""
    def __init__(self, ts, platform, fixture, tipo="ganador", linea=None,
                 odd_p1=2.0, odd_p2=2.0):
        self.timestamp = ts
        self.platform = platform
        self.fixture = fixture
        self.market_tipo = tipo
        self.linea = linea
        self.odd_p1 = odd_p1
        self.odd_p2 = odd_p2


def test_bucket_key_groups_close_timestamps():
    t0 = datetime(2026, 5, 15, 12, 0, 0)
    t1 = t0 + timedelta(seconds=30)
    t2 = t0 + timedelta(seconds=120)
    assert _bucket_key(t0, 120) == _bucket_key(t1, 120)
    assert _bucket_key(t0, 120) != _bucket_key(t2, 120)


def test_backtest_detects_cross_book_arb():
    t = datetime.now() - timedelta(hours=1)
    snaps = [
        _FakeSnap(t, "stake",    "A vs B", odd_p1=2.10, odd_p2=1.80),
        _FakeSnap(t, "pinnacle", "A vs B", odd_p1=1.85, odd_p2=2.15),
        # Cross-arb: stake p1 (2.10) + pinnacle p2 (2.15) → 1/2.10+1/2.15 = 0.941 < 1
    ]
    with patch("src.core.math.backtest_cross_book._iter_snapshots", return_value=snaps):
        report = backtest_cross_book(hours=24, min_roi=0.0)
    assert report.arbs_found == 1
    arb = report.arbs[0]
    assert arb.book_p1 == "stake"
    assert arb.book_p2 == "pinnacle"
    assert arb.roi > 0.0
    assert abs(arb.impl_sum - (1/2.10 + 1/2.15)) < 1e-4


def test_backtest_skips_same_book_best():
    t = datetime.now() - timedelta(minutes=10)
    snaps = [
        _FakeSnap(t, "stake",    "A vs B", odd_p1=3.00, odd_p2=2.00),
        _FakeSnap(t, "pinnacle", "A vs B", odd_p1=2.00, odd_p2=1.50),
    ]
    # stake tiene la mejor odd_p1 Y mejor odd_p2 → no es cross-book
    with patch("src.core.math.backtest_cross_book._iter_snapshots", return_value=snaps):
        report = backtest_cross_book(hours=24, min_roi=0.0)
    assert report.arbs_found == 0


def test_backtest_skips_no_arb_when_impl_sum_ge_1():
    t = datetime.now() - timedelta(minutes=5)
    snaps = [
        _FakeSnap(t, "stake",    "A vs B", odd_p1=1.90, odd_p2=1.80),
        _FakeSnap(t, "pinnacle", "A vs B", odd_p1=1.85, odd_p2=1.95),
    ]
    # 1/1.90 + 1/1.95 = 0.526 + 0.513 = 1.039 → overround positivo, no arb
    with patch("src.core.math.backtest_cross_book._iter_snapshots", return_value=snaps):
        report = backtest_cross_book(hours=24, min_roi=0.0)
    assert report.arbs_found == 0


def test_backtest_min_roi_filter():
    t = datetime.now() - timedelta(minutes=5)
    snaps = [
        _FakeSnap(t, "stake",    "A vs B", odd_p1=2.05, odd_p2=1.95),
        _FakeSnap(t, "pinnacle", "A vs B", odd_p1=1.95, odd_p2=2.05),
        # 1/2.05 + 1/2.05 = 0.976 → roi ≈ 2.5%
    ]
    with patch("src.core.math.backtest_cross_book._iter_snapshots", return_value=snaps):
        r_low = backtest_cross_book(hours=24, min_roi=0.001)
        r_high = backtest_cross_book(hours=24, min_roi=0.10)
    assert r_low.arbs_found == 1
    assert r_high.arbs_found == 0


def test_backtest_respects_time_buckets():
    # Anclar a un timestamp que cae en un cruce de bucket de 600s para
    # garantizar determinismo: t0 dentro de [bucket_n], t1 = t0 + 300s
    # dentro de [bucket_n+1] cuando bucket=60, pero dentro de [bucket_n]
    # cuando bucket=600 sólo si t0 cae en la primera mitad del bucket.
    # Forzamos t0 al inicio exacto de un bucket de 600s.
    base = datetime.now() - timedelta(hours=2)
    base_ts = int(base.timestamp() // 600) * 600  # alineado a bucket 600
    t0 = datetime.fromtimestamp(base_ts + 10)     # 10s dentro del bucket
    t1 = t0 + timedelta(seconds=300)              # 310s → mismo bucket de 600
    snaps = [
        _FakeSnap(t0, "stake",    "A vs B", odd_p1=2.10, odd_p2=1.80),
        _FakeSnap(t1, "pinnacle", "A vs B", odd_p1=1.85, odd_p2=2.15),
    ]
    with patch("src.core.math.backtest_cross_book._iter_snapshots", return_value=snaps):
        r_small_bucket = backtest_cross_book(hours=24, min_roi=0.0, time_bucket_sec=60)
        r_large_bucket = backtest_cross_book(hours=24, min_roi=0.0, time_bucket_sec=600)
    assert r_small_bucket.arbs_found == 0  # snapshots no caen en mismo bucket
    assert r_large_bucket.arbs_found == 1  # mismo bucket → arb detectado


def test_backtest_report_to_dict():
    with patch("src.core.math.backtest_cross_book._iter_snapshots", return_value=[]):
        report = backtest_cross_book(hours=1)
    d = report.to_dict()
    assert d["arbs_found"] == 0
    assert d["arbs"] == []
    assert "generated_at" in d
