"""
test_cross_book_settle.py — settlement real de arbs cross-book (Kambi live).

Valida la lógica pura de normalización/matching + el flujo "capturar en vivo →
liquidar al terminar" con el feed Kambi MOCKEADO (sin red): un partido se ve en
vivo (tracker), luego desaparece (terminó) y el arb se liquida según `who`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.orchestration import cross_book_settle as cbs


@pytest.fixture(autouse=True)
def _db():
    from src.database.database import init_db, AppConfig
    init_db()
    AppConfig.set_value(cbs._TRACKER_KEY, "")   # limpiar tracker entre tests
    yield


# ── lógica pura ──────────────────────────────────────────────────────────────

def test_split_fixture_vs_and_dash():
    assert cbs._split_fixture("Arizona vs Miami") == ("Arizona", "Miami")
    assert cbs._split_fixture("Italy (aloha) - Netherlands (x)") == ("Italy (aloha)", "Netherlands (x)")


def test_fixture_key_order_independent():
    k1 = cbs._fixture_key("Norway (mara)", "Sweden (Xella)")
    k2 = cbs._fixture_key("Sweden (other)", "Norway (zzz)")
    assert k1 == k2


def test_norm_strips_parens_and_noise():
    assert cbs._norm("Haugesund (W)") == "haugesund"
    assert cbs._norm("Manchester United FC") == "manchester"


def test_who_to_side():
    assert cbs._winner_side_from_who("HOME") == "p1"
    assert cbs._winner_side_from_who("AWAY") == "p2"
    assert cbs._winner_side_from_who("DRAW") == "draw"
    assert cbs._winner_side_from_who("UNKNOWN") is None


def test_kambi_esports_event_detection_true():
    ev = {
        "group": "CLA World Cup (2x5 min)",
        "path": [
            {"englishName": "Football", "termKey": "football"},
            {"englishName": "|Esports Football|", "termKey": "esports_football"},
        ],
    }
    assert cbs._is_kambi_esports_event(ev) is True


def test_kambi_esports_event_detection_false_for_real_football():
    ev = {
        "group": "LaLiga",
        "path": [
            {"englishName": "Football", "termKey": "football"},
            {"englishName": "Spain", "termKey": "spain"},
        ],
    }
    assert cbs._is_kambi_esports_event(ev) is False


# ── flujo end-to-end con feed Kambi mockeado ─────────────────────────────────

def _make_arb_legs(combo_key, fixture, legs_spec):
    from src.core.math.simulation import SimBet, BetLeg
    from src.core.orchestration.wallet import get_wallet
    for slug, side, odd, stake in legs_spec:
        w = get_wallet(slug)
        bet = SimBet(
            bet_id=f"{combo_key}_{slug[:3]}_{side}", timestamp="2026-06-10T12:00:00",
            fixture_name=fixture, arb_tipo="cross_book_arb_3way" if len(legs_spec) == 3 else "cross_book_arb_2way",
            arb_descripcion="t", total_staked=stake, guaranteed_return=round(stake * odd, 2),
            roi=0.03, combo_key=combo_key,
            legs=[BetLeg(market_label=f"{side}@{odd}", side=side, odd=odd, stake=stake, expected_return=round(stake*odd, 2))],
            platform=slug,
        )
        w.bets.append(bet)
        w.balance = round(w.balance - stake, 2)


def test_capture_then_settle_when_finished():
    """Partido visto en vivo (HOME ganando), luego desaparece → liquida p1 won."""
    from src.core.orchestration.wallet import get_wallet
    _make_arb_legs(
        "cb_kambi2w01", "Inter (femzzi) vs Milan (tommy)",
        [("pinnacle", "p1", 1.9, 5.0), ("unibet", "p2", 2.1, 5.0)],
    )
    live_match = {cbs._fixture_key("Inter", "Milan"): {
        "who": "HOME", "home": "3", "away": "0", "sport": "FOOTBALL", "name": "Inter - Milan"}}

    # Pasada 1: el partido está EN VIVO → se captura, no se liquida.
    with patch.object(cbs, "_fetch_kambi_live", return_value=live_match):
        s1 = cbs.settle_cross_book_bets()
    assert s1 == []
    pin = next(b for b in get_wallet("pinnacle").bets if b.bet_id == "cb_kambi2w01_pin_p1")
    assert pin.status == "pending"

    # Pasada 2: el partido DESAPARECIÓ (terminó) → liquida.
    with patch.object(cbs, "_fetch_kambi_live", return_value={}):
        s2 = cbs.settle_cross_book_bets()
    assert len([x for x in s2 if x.startswith("cb_kambi2w01")]) == 2
    pin = next(b for b in get_wallet("pinnacle").bets if b.bet_id == "cb_kambi2w01_pin_p1")
    uni = next(b for b in get_wallet("unibet").bets if b.bet_id == "cb_kambi2w01_uni_p2")
    assert pin.status == "won"   # HOME → p1
    assert uni.status == "lost"
    assert pin.pnl > 0 and uni.pnl < 0


def test_3way_draw_settles_only_draw():
    from src.core.orchestration.wallet import get_wallet
    _make_arb_legs(
        "cb_kambi3w01", "Senegal vs Saudi Arabia",
        [("pinnacle", "draw", 2.46, 3.33), ("stake", "p2", 5.0, 3.33), ("unibet", "p1", 5.6, 3.33)],
    )
    live = {cbs._fixture_key("Senegal", "Saudi Arabia"): {
        "who": "DRAW", "home": "1", "away": "1", "sport": "FOOTBALL", "name": "Senegal - Saudi Arabia"}}
    with patch.object(cbs, "_fetch_kambi_live", return_value=live):
        cbs.settle_cross_book_bets()
    with patch.object(cbs, "_fetch_kambi_live", return_value={}):
        s = cbs.settle_cross_book_bets()
    assert len([x for x in s if x.startswith("cb_kambi3w01")]) == 3
    draw = next(b for b in get_wallet("pinnacle").bets if b.bet_id == "cb_kambi3w01_pin_draw")
    assert draw.status == "won"
    p1 = next(b for b in get_wallet("unibet").bets if b.bet_id == "cb_kambi3w01_uni_p1")
    p2 = next(b for b in get_wallet("stake").bets if b.bet_id == "cb_kambi3w01_sta_p2")
    assert p1.status == "lost" and p2.status == "lost"


def test_still_live_does_not_settle():
    from src.core.orchestration.wallet import get_wallet
    _make_arb_legs(
        "cb_kambilive01", "Live FC vs Ongoing United",
        [("pinnacle", "p1", 1.9, 5.0), ("unibet", "p2", 2.1, 5.0)],
    )
    live = {cbs._fixture_key("Live FC", "Ongoing United"): {
        "who": "HOME", "home": "1", "away": "0", "sport": "FOOTBALL", "name": "Live FC - Ongoing United"}}
    with patch.object(cbs, "_fetch_kambi_live", return_value=live):
        cbs.settle_cross_book_bets()
        s = cbs.settle_cross_book_bets()
    assert [x for x in s if x.startswith("cb_kambilive01")] == []
    bet = next(b for b in get_wallet("pinnacle").bets if b.bet_id == "cb_kambilive01_pin_p1")
    assert bet.status == "pending"


def test_stat_total_leg_with_inconsistent_side_is_not_settled_by_live_who():
    """
    Bug: el guard usaba AND entre (leg_tipo no-winner) y (side no-winner). Si los
    dos campos quedan inconsistentes (leg_tipo='total' pero side='p1' por algún
    bug de datos previo), el AND fallaba y el bet se liquidaba con el ganador del
    partido (`who`) en vez de dejarlo para el settlement por marcador. Con OR,
    cualquiera de las dos señales de "no es winner" debe bastar para NO liquidar.
    """
    from src.core.math.simulation import SimBet, BetLeg
    from src.core.orchestration.wallet import get_wallet
    w = get_wallet("pinnacle")
    bet = SimBet(
        bet_id="cb_stattotal01_pin_p1", timestamp="2026-06-10T12:00:00",
        fixture_name="Home Stat FC vs Away Stat United",
        arb_tipo="stat", arb_descripcion="t", total_staked=5.0, guaranteed_return=9.5,
        roi=0.03, combo_key="cb_stattotal01",
        legs=[BetLeg(market_label="total@1.9", side="p1", odd=1.9, stake=5.0, expected_return=9.5, tipo="total")],
        platform="pinnacle",
    )
    w.bets.append(bet)
    w.balance = round(w.balance - 5.0, 2)

    live = {cbs._fixture_key("Home Stat FC", "Away Stat United"): {
        "who": "HOME", "home": "2", "away": "0", "sport": "FOOTBALL", "name": "Home Stat FC - Away Stat United"}}
    with patch.object(cbs, "_fetch_kambi_live", return_value=live):
        cbs.settle_cross_book_bets()
    with patch.object(cbs, "_fetch_kambi_live", return_value={}):
        s = cbs.settle_cross_book_bets()

    assert [x for x in s if x.startswith("cb_stattotal01")] == []
    bet = next(b for b in get_wallet("pinnacle").bets if b.bet_id == "cb_stattotal01_pin_p1")
    assert bet.status == "pending"


def test_unknown_who_never_settles():
    from src.core.orchestration.wallet import get_wallet
    _make_arb_legs(
        "cb_kambiunk01", "Tied A vs Tied B",
        [("pinnacle", "p1", 1.9, 5.0), ("unibet", "p2", 2.1, 5.0)],
    )
    live = {cbs._fixture_key("Tied A", "Tied B"): {
        "who": "UNKNOWN", "home": "0", "away": "0", "sport": "FOOTBALL", "name": "Tied A - Tied B"}}
    with patch.object(cbs, "_fetch_kambi_live", return_value=live):
        cbs.settle_cross_book_bets()
    with patch.object(cbs, "_fetch_kambi_live", return_value={}):
        s = cbs.settle_cross_book_bets()
    assert [x for x in s if x.startswith("cb_kambiunk01")] == []
    bet = next(b for b in get_wallet("pinnacle").bets if b.bet_id == "cb_kambiunk01_pin_p1")
    assert bet.status == "pending"
