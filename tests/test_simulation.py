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
