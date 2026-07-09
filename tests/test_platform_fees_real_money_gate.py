"""
test_platform_fees_real_money_gate.py — fail-closed invariant for is_real_money_arb().

Regression guard: is_real_money_arb() used to fail-OPEN — if both legs were
unknown/unregistered platforms (not just a legacy non-real-money platform),
it incorrectly returned True, letting the caller (scanning.py cross-platform
hedge check) treat an unhedgeable arb as capital-hedged. The gate must be
fail-closed: True only when BOTH legs are explicitly in REAL_MONEY_PLATFORMS.

POLICY (2026-07-09): Polymarket was removed from this project (unfillable
limit orders, apparent bot-driven queue-jumping on their book) —
REAL_MONEY_PLATFORMS is now {"kalshi"} only, so "polymarket" is treated the
same as any other non-real-money/unknown platform below.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.orchestration.platform_fees import is_real_money_arb


def test_both_real_money_platforms_true():
    assert is_real_money_arb("kalshi", "kalshi") is True
    assert is_real_money_arb("KALSHI", "kalshi") is True


def test_legacy_non_real_money_leg_is_never_real_money():
    assert is_real_money_arb("kalshi", "legacy_virtual") is False
    assert is_real_money_arb("legacy_virtual", "kalshi") is False
    assert is_real_money_arb("legacy_virtual", "legacy_virtual") is False


def test_both_unknown_platforms_fail_closed():
    """Regression: previously returned True (fail-open) for two unregistered
    platforms — must be False."""
    assert is_real_money_arb("mystery_exchange", "another_unknown") is False


def test_one_known_one_unknown_is_false():
    assert is_real_money_arb("kalshi", "mystery_exchange") is False
    assert is_real_money_arb("mystery_exchange", "kalshi") is False


def test_removed_polymarket_platform_is_no_longer_real_money():
    # Polymarket was removed from REAL_MONEY_PLATFORMS entirely 2026-07-09 —
    # any stray historical "polymarket" leg is now treated as non-real-money.
    assert is_real_money_arb("polymarket", "kalshi") is False
    assert is_real_money_arb("polymarket", "polymarket") is False


def test_empty_or_none_platform_is_false():
    assert is_real_money_arb("", "") is False
    assert is_real_money_arb(None, None) is False
    assert is_real_money_arb("kalshi", None) is False
