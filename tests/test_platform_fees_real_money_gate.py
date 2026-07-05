"""
test_platform_fees_real_money_gate.py — fail-closed invariant for is_real_money_arb().

Regression guard: is_real_money_arb() used to fail-OPEN — if both legs were
unknown/unregistered platforms (not just Manifold), it incorrectly returned
True, letting the caller (scanning.py cross-platform hedge check) treat an
unhedgeable arb as capital-hedged. The gate must be fail-closed: True only
when BOTH legs are explicitly in REAL_MONEY_PLATFORMS.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.orchestration.platform_fees import is_real_money_arb


def test_both_real_money_platforms_true():
    assert is_real_money_arb("polymarket", "kalshi") is True
    assert is_real_money_arb("kalshi", "polymarket") is True
    assert is_real_money_arb("Polymarket", "KALSHI") is True


def test_manifold_leg_is_never_real_money():
    assert is_real_money_arb("polymarket", "manifold") is False
    assert is_real_money_arb("manifold", "kalshi") is False
    assert is_real_money_arb("manifold", "manifold") is False


def test_both_unknown_platforms_fail_closed():
    """Regression: previously returned True (fail-open) for two unregistered
    platforms — must be False."""
    assert is_real_money_arb("mystery_exchange", "another_unknown") is False


def test_one_known_one_unknown_is_false():
    assert is_real_money_arb("polymarket", "mystery_exchange") is False
    assert is_real_money_arb("mystery_exchange", "kalshi") is False


def test_empty_or_none_platform_is_false():
    assert is_real_money_arb("", "") is False
    assert is_real_money_arb(None, None) is False
    assert is_real_money_arb("polymarket", None) is False
