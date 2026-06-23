"""Tests para VirtualWallet.resolve_bet — verifica integridad del balance."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Permite ejecutar `pytest` desde la raíz del repo.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.core.math.simulation import BetLeg, SimBet, VirtualWallet


def _make_bet(stake: float = 10.0, guaranteed: float = 11.0) -> SimBet:
    return SimBet(
        bet_id="abc12345",
        timestamp="2025-01-01T00:00:00",
        fixture_name="A vs B",
        arb_tipo="cross_book",
        arb_descripcion="test",
        total_staked=stake,
        guaranteed_return=guaranteed,
        roi=10.0,
        legs=[
            BetLeg(
                market_label="ML A",
                side="home",
                odd=2.0,
                stake=stake,
                expected_return=guaranteed,
            )
        ],
        status="pending",
        pnl=0.0,
    )


class TestResolveBet:
    def setup_method(self):
        self.w = VirtualWallet(initial_balance=100.0, platform="stake")
        self.w.balance = 90.0  # asume 10€ ya descontados al colocar
        self.bet = _make_bet(stake=10.0, guaranteed=11.0)
        self.w.bets.append(self.bet)

    def test_won_credits_guaranteed_return(self):
        out = self.w.resolve_bet("abc12345", won=True)
        assert out is not None
        assert out.status == "won"
        assert out.pnl == 1.0
        assert self.w.balance == 101.0  # 90 + 11

    def test_won_with_actual_return_overrides_guaranteed(self):
        out = self.w.resolve_bet("abc12345", won=True, actual_return=15.0)
        assert out.guaranteed_return == 15.0
        assert out.pnl == 5.0
        assert self.w.balance == 105.0

    def test_lost_keeps_stake_lost(self):
        out = self.w.resolve_bet("abc12345", won=False)
        assert out.status == "lost"
        assert out.pnl == -10.0
        assert self.w.balance == 90.0  # stake ya estaba descontado

    def test_lost_with_partial_return_credits_partial(self):
        out = self.w.resolve_bet("abc12345", won=False, actual_return=3.0)
        assert out.pnl == -7.0
        assert self.w.balance == 93.0

    def test_resolve_unknown_bet_returns_none(self):
        assert self.w.resolve_bet("does_not_exist", won=True) is None

    def test_resolve_already_resolved_returns_none(self):
        self.w.resolve_bet("abc12345", won=True)
        # segunda llamada no debe duplicar el crédito
        assert self.w.resolve_bet("abc12345", won=True) is None
        assert self.w.balance == 101.0

    def test_reopen_won_undoes_credit(self):
        self.w.resolve_bet("abc12345", won=True)
        self.w.reopen_bet("abc12345")
        assert self.bet.status == "pending"
        assert self.w.balance == 90.0

    def test_reopen_lost_with_partial_undoes_partial(self):
        self.w.resolve_bet("abc12345", won=False, actual_return=3.0)
        self.w.reopen_bet("abc12345")
        assert self.bet.status == "pending"
        # esperado: stake descontado pero partial revertido → 90
        assert self.w.balance == 90.0


class TestPlaceBetAmount:
    """Regresión del bug: trailing comma hacía bet_type una TUPLA (str,) →
    se persistía como '(...,)' y los filtros `bet_type == 'cross-arb'` fallaban."""

    def test_bet_type_is_str_not_tuple(self):
        from src.core.math.arbitrage.opportunity import MarketLine
        from src.core.math.arbitrage.detectors.inside import scan_inside_arb

        w = VirtualWallet(initial_balance=100.0, platform="stake")
        ml = MarketLine(tipo="ganador", odd_p1=2.1, odd_p2=2.1)
        opps = scan_inside_arb([ml], 0.001, lambda x: "match")
        assert opps, "el inside-arb debería detectarse"

        bet = w.place_bet_amount(opps[0], "A vs B", 10.0)
        assert bet is not None
        assert isinstance(bet.bet_type, str), (
            f"bet_type debe ser str, no {type(bet.bet_type).__name__}"
        )
        # no debe verse como tupla serializada
        assert not bet.bet_type.startswith("(")




class TestWinRate:
    """win_rate = won / (won + lost); void/expired NO deben contar (son
    devoluciones, no derrotas) — consistente con /api/wallet/stats."""

    def test_win_rate_excludes_void_from_denominator(self):
        w = VirtualWallet(initial_balance=100.0, platform="stake")
        for i, status in enumerate(["won", "won", "lost", "void"]):
            bet = _make_bet(stake=10.0, guaranteed=11.0)
            bet.bet_id = f"bet{i}"
            bet.status = status
            w.bets.append(bet)
        # 2 won, 1 lost, 1 void → 2/(2+1) = 0.666..., NO 2/4 = 0.5
        assert w.win_rate == pytest.approx(2 / 3)

    def test_win_rate_zero_when_no_resolved_bets(self):
        w = VirtualWallet(initial_balance=100.0, platform="stake")
        bet = _make_bet(stake=10.0, guaranteed=11.0)
        bet.status = "pending"
        w.bets.append(bet)
        assert w.win_rate == 0.0
