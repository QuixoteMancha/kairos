"""
Unit tests for _compute_live_hedge (core, multi-sport).

Covers:
  - Same-book and cross-book rival hedge selection (sport-agnostic market scanning by tipo + linea).
  - No matching market / no useful rival odd → None.
  - ARB / HEDGE / FALLBACK / DANGER- HEDGE modes.
  - Pre-match vs in-play gating.
  - Exercises the sport + leg_tipo delegation (not just tennis "ganador"/"total_juegos").
  - Bets carry .sport (stamped at placement) so the correct per-sport get_live_stat_danger is used.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.core.orchestration.live_monitor import (
    HEDGE_FALLBACK_TICKS,
    HEDGE_IMPL_FALLBACK,
    HEDGE_IMPL_TARGET,
    _compute_live_hedge,
    _hedge_stakes_for_lock,
)


# ── Mocks mínimos ─────────────────────────────────────────────────────────────


@dataclass
class _MarketStub:
    tipo: str
    odd_p1: float
    odd_p2: float
    set_num: int | None = None
    linea: float | None = None
    player_side: str | None = None


@dataclass
class _FixtureStub:
    markets: list = field(default_factory=list)
    score_p1: int | None = None
    score_p2: int | None = None
    fixture_id: str = "fx1"
    p1_name: str = "PlayerA"
    p2_name: str = "PlayerB"  # generic fixture names; sport + leg_tipo drive the logic


@dataclass
class _LegStub:
    side: str
    odd: float
    tipo: str
    linea: float | None = None
    set_num: int | None = None
    player_side: str | None = None

    def to_dict(self) -> dict:
        return {
            "side":        self.side,
            "odd":         self.odd,
            "tipo":        self.tipo,
            "linea":       self.linea,
            "set_num":     self.set_num,
            "player_side": self.player_side,
        }


@dataclass
class _BetStub:
    legs: list
    total_staked: float = 100.0
    sport: str = "tennis"  # simulate stamped .sport from placement (DB + SimBet)


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_ganador_same_book_arb():
    """ARB puro: stat bet p1@2.0, rival p2@2.50 → impl=0.9 → arb."""
    bet = _BetStub(legs=[_LegStub(side="p1", odd=2.0, tipo="ganador")], sport="tennis")
    fx  = _FixtureStub(markets=[_MarketStub(tipo="ganador", odd_p1=1.50, odd_p2=2.50)])
    info = _compute_live_hedge(bet, fx, score_p1=0, score_p2=0)
    assert info is not None
    assert info["mode"]       == "arb"
    assert info["hedge_side"] == "p2"
    assert info["hedge_odd"]  == 2.50
    assert info["impl"]       < 1.0
    assert info["lock_profit"] > 0


def test_total_juegos_matching_by_linea():
    """Stat bet over 17.5 → hedge debe ser under 17.5 (mismo mercado)."""
    bet = _BetStub(legs=[_LegStub(side="p1", odd=2.10, tipo="total_juegos", linea=17.5)], sport="tennis")
    fx  = _FixtureStub(markets=[
        _MarketStub(tipo="total_juegos", odd_p1=1.90, odd_p2=1.95, linea=17.5),
        _MarketStub(tipo="total_juegos", odd_p1=2.40, odd_p2=1.55, linea=19.5),  # no debe matchear
        _MarketStub(tipo="ganador",      odd_p1=1.20, odd_p2=4.50),               # no debe matchear
    ])
    info = _compute_live_hedge(bet, fx, score_p1=0, score_p2=0)
    assert info is not None
    assert info["market_tipo"]  == "total_juegos"
    assert info["market_linea"] == 17.5
    assert info["hedge_odd"]    == 1.95  # odd_p2 de la línea 17.5
    assert info["hedge_side"]   == "p2"


def test_cross_book_picks_best_rival_odd():
    """Pool {stake, pinnacle} — pinnacle ofrece mejor cuota rival."""
    bet = _BetStub(legs=[_LegStub(side="p1", odd=2.0, tipo="ganador")], sport="tennis")
    stake_fx    = _FixtureStub(markets=[_MarketStub(tipo="ganador", odd_p1=2.0, odd_p2=1.90)])
    pinnacle_fx = _FixtureStub(markets=[_MarketStub(tipo="ganador", odd_p1=1.95, odd_p2=2.10)])
    info = _compute_live_hedge(
        bet, {"stake": stake_fx, "pinnacle": pinnacle_fx},
        score_p1=0, score_p2=0, source_book="stake",
    )
    assert info is not None
    assert info["hedge_book"] == "pinnacle"
    assert info["hedge_odd"]  == 2.10  # mejor que 1.90 de stake
    assert info["mode"]       in ("arb", "hedge")  # 1/2 + 1/2.10 = 0.976 → arb


def test_no_matching_market_returns_none():
    """Stat bet with a tennis line leg_tipo but fixture only has winner markets (sport-agnostic market scan)."""
    bet = _BetStub(legs=[_LegStub(side="p1", odd=1.85, tipo="handicap_juego", linea=-2.5)], sport="tennis")
    fx  = _FixtureStub(markets=[_MarketStub(tipo="ganador", odd_p1=1.50, odd_p2=2.50)])
    assert _compute_live_hedge(bet, fx, score_p1=0, score_p2=0) is None


def test_no_rival_odd_returns_none():
    """Mercado existe pero cuota rival es 1.0 (no útil)."""
    bet = _BetStub(legs=[_LegStub(side="p1", odd=2.0, tipo="ganador")], sport="tennis")
    fx  = _FixtureStub(markets=[_MarketStub(tipo="ganador", odd_p1=2.0, odd_p2=1.0)])
    assert _compute_live_hedge(bet, fx, score_p1=0, score_p2=0) is None


def test_hedge_mode_when_impl_between_1_and_target():
    """impl ∈ [1.0, HEDGE_IMPL_TARGET) y partido en vivo → modo hedge.

    Escenario: la apuesta se colocó pre-match; el partido ya comenzó (score 0-0,
    primer set en progreso) y las cuotas bajaron ligeramente → impl < 1.04.
    is_live=True habilita HEDGE/FALLBACK incluso cuando no hay sets cerrados.
    """
    # odd0=2.0 + rival=1.98 → impl ≈ 0.5 + 0.5051 = 1.0051 → entre 1.0 y 1.04 → HEDGE
    bet = _BetStub(legs=[_LegStub(side="p1", odd=2.0, tipo="ganador")], sport="tennis")
    fx  = _FixtureStub(markets=[_MarketStub(tipo="ganador", odd_p1=2.0, odd_p2=1.98)])
    info = _compute_live_hedge(bet, fx, score_p1=0, score_p2=0, is_live=True)
    assert info is not None, "debería disparar modo hedge cuando el partido está vivo"
    assert info["mode"] == "hedge"
    assert 1.0 <= info["impl"] < HEDGE_IMPL_TARGET


def test_pre_match_no_fallback_when_above_target():
    """Pre-match con impl > HEDGE_IMPL_TARGET → esperar, no fallback."""
    # odd0=2.0 + rival=1.80 → impl ≈ 0.5 + 0.5556 = 1.0556 → entre TARGET y FALLBACK
    bet = _BetStub(legs=[_LegStub(side="p1", odd=2.0, tipo="ganador")], sport="tennis")
    fx  = _FixtureStub(markets=[_MarketStub(tipo="ganador", odd_p1=2.0, odd_p2=1.80)])
    info = _compute_live_hedge(bet, fx, score_p1=0, score_p2=0, ticks_waiting=99)
    # Pre-match (sets 0-0) no activa fallback aunque ticks_waiting sea alto
    assert info is None


def test_fallback_in_play_when_ticks_exceeded():
    """In-play (sets > 0) + ticks_waiting >= HEDGE_FALLBACK_TICKS + impl < FALLBACK → fallback."""
    bet = _BetStub(legs=[_LegStub(side="p1", odd=2.0, tipo="ganador")], sport="tennis")
    fx  = _FixtureStub(markets=[_MarketStub(tipo="ganador", odd_p1=2.0, odd_p2=1.80)],
                       score_p1=1, score_p2=0)
    info = _compute_live_hedge(bet, fx, score_p1=1, score_p2=0,
                                ticks_waiting=HEDGE_FALLBACK_TICKS)
    assert info is not None
    assert info["mode"] == "fallback"
    assert info["impl"] < HEDGE_IMPL_FALLBACK


def test_empty_legs_returns_none():
    bet = _BetStub(legs=[])
    fx  = _FixtureStub(markets=[_MarketStub(tipo="ganador", odd_p1=2.0, odd_p2=2.0)])
    assert _compute_live_hedge(bet, fx, score_p1=0, score_p2=0) is None


def test_p1_p2_swap_in_set_total_juegos():
    """Stat bet under 17.5 (side=p2) → hedge over 17.5 (side=p1)."""
    bet = _BetStub(legs=[_LegStub(side="p2", odd=1.95, tipo="total_juegos", linea=17.5)])
    fx  = _FixtureStub(markets=[_MarketStub(tipo="total_juegos", odd_p1=2.10, odd_p2=1.80, linea=17.5)])
    info = _compute_live_hedge(bet, fx, score_p1=0, score_p2=0)
    assert info is not None
    assert info["hedge_side"] == "p1"
    assert info["hedge_odd"]  == 2.10


# ── Multi-sport examples (generalized, no tennis favoritism) ──

def test_football_1x2_same_book_arb():
    """Football 1x2 (supported leg_tipo). Same-book arb via the general path."""
    bet = _BetStub(legs=[_LegStub(side="p1", odd=2.0, tipo="1x2")], sport="football")
    fx = _FixtureStub(markets=[_MarketStub(tipo="1x2", odd_p1=1.50, odd_p2=2.50)])
    info = _compute_live_hedge(bet, fx, score_p1=0, score_p2=0)  # goals as score units
    assert info is not None
    assert info["mode"] in ("arb", "hedge")
    assert info["market_tipo"] == "1x2"


def test_basketball_total_points_cross_book():
    """Basketball total_points (supported). Cross-book selection still works with sport=basketball."""
    bet = _BetStub(legs=[_LegStub(side="p1", odd=1.95, tipo="total_points", linea=205.5)], sport="basketball")
    stake_fx = _FixtureStub(markets=[_MarketStub(tipo="total_points", odd_p1=1.80, odd_p2=1.95, linea=205.5)])
    other_fx = _FixtureStub(markets=[_MarketStub(tipo="total_points", odd_p1=1.70, odd_p2=2.10, linea=205.5)])
    info = _compute_live_hedge(
        bet, {"stake": stake_fx, "pinnacle": other_fx},
        score_p1=40, score_p2=45, source_book="stake",
    )
    assert info is not None
    assert info["hedge_book"] == "pinnacle"  # pinnacle ofrece mejor cuota rival (p2=2.10 > 1.95)
    assert info["market_tipo"] == "total_points"


def test_baseball_run_line_danger():
    """Baseball 'run_line' (supported). Runs as score units."""
    bet = _BetStub(legs=[_LegStub(side="p1", odd=1.90, tipo="run_line", linea=-1.5)], sport="baseball")
    fx = _FixtureStub(markets=[_MarketStub(tipo="run_line", odd_p1=1.85, odd_p2=1.90, linea=-1.5)],
                      score_p1=2, score_p2=5)  # runs
    info = _compute_live_hedge(bet, fx, score_p1=2, score_p2=5, ticks_waiting=2, is_live=True)
    assert info is not None
    assert info["market_tipo"] == "run_line"



def test_stat_over_side_hedges_with_under_odd_not_own_odd():
    """
    Bug fijado: rival_side se computaba con un ternario que solo distinguia
    'p1' de "cualquier otra cosa" -> side='over' caia al else y resolvia
    rival_side='p1', es decir buscaba SU PROPIA odd (odd_p1, el slot de
    'over') en vez de la odd contraria (odd_p2, 'under'). odd_p1=over /
    odd_p2=under es la convencion real (ver hedge_routes.py "Over = outcome
    P1 en Stake"). El hedge debe usar odd_p2 (under) como rival de un 'over'.
    """
    bet = _BetStub(legs=[_LegStub(side="over", odd=1.90, tipo="total", linea=22.5)], sport="tennis")
    fx = _FixtureStub(markets=[
        _MarketStub(tipo="total", odd_p1=1.90, odd_p2=2.05, linea=22.5),
    ])
    info = _compute_live_hedge(bet, fx, score_p1=0, score_p2=0, is_live=True)
    assert info is not None
    # El rival de "over" debe ser la odd "under" (odd_p2), no la propia (odd_p1).
    assert info["hedge_odd"] == 2.05
    assert info["hedge_side"] == "p2"


def test_stat_under_side_hedges_with_over_odd():
    """Caso simetrico: 'under' debe usar odd_p1 (over) como rival."""
    bet = _BetStub(legs=[_LegStub(side="under", odd=2.05, tipo="total", linea=22.5)], sport="tennis")
    fx = _FixtureStub(markets=[
        _MarketStub(tipo="total", odd_p1=1.90, odd_p2=2.05, linea=22.5),
    ])
    info = _compute_live_hedge(bet, fx, score_p1=0, score_p2=0, is_live=True)
    assert info is not None
    assert info["hedge_odd"] == 1.90
    assert info["hedge_side"] == "p1"


# ── _hedge_stakes_for_lock (moved here 2026-07-10 from test_cross_book.py when
# orchestration/cross_book.py was decommissioned; the function now lives in
# live_monitor.py alongside _compute_live_hedge) ────────────────────────────────

def test_hedge_stakes_feasible():
    """odd_a=2.0 + odd_b=2.1 → 1/2.0 + 1/2.1 = 0.976 < 1 → arb feasible."""
    stake_b, profit, roi = _hedge_stakes_for_lock(odd_a=2.0, stake_a=10.0, odd_b=2.1)
    assert stake_b > 0
    assert profit > 0
    assert roi > 0
    # stake_b = 10 * 2.0 / 2.1 = 9.5238...
    assert stake_b == pytest.approx(9.52, abs=0.01)


def test_hedge_stakes_unfeasible():
    """odd_a=2.0 + odd_b=1.9 → 1/2.0 + 1/1.9 = 1.026 > 1 → sin arb."""
    stake_b, profit, roi = _hedge_stakes_for_lock(odd_a=2.0, stake_a=10.0, odd_b=1.9)
    assert stake_b == 0.0
    assert profit == 0.0
    assert roi == 0.0


def test_hedge_stakes_invalid_odds():
    """Odds <= 1.0 → no es una cuota válida, retorna ceros."""
    assert _hedge_stakes_for_lock(0.5, 10.0, 2.0) == (0.0, 0.0, 0.0)
    assert _hedge_stakes_for_lock(2.0, 10.0, 1.0) == (0.0, 0.0, 0.0)


def test_hedge_stakes_returns_equalized():
    """Con par feasible, retorno final debe ser idéntico en ambos lados."""
    odd_a, stake_a, odd_b = 2.5, 100.0, 1.85
    stake_b, profit, roi = _hedge_stakes_for_lock(odd_a, stake_a, odd_b)
    assert stake_b > 0   # sanity: feasible
    return_a = stake_a * odd_a
    return_b = stake_b * odd_b
    assert return_a == pytest.approx(return_b, abs=0.05)
    # Profit consistente con cualquier lado
    total = stake_a + stake_b
    assert profit == pytest.approx(return_a - total, abs=0.05)
    assert roi == pytest.approx(profit / total, abs=0.001)


def test_hedge_stakes_roi_matches_implicit_arb():
    """
    ROI calculado debe equivaler a `(1 / (1/odd_a + 1/odd_b)) - 1`,
    que es la definición canónica de ROI de un arb 2-way.
    """
    odd_a, odd_b = 3.0, 1.6
    stake_b, profit, roi = _hedge_stakes_for_lock(odd_a, 50.0, odd_b)
    expected_roi = (1.0 / (1.0 / odd_a + 1.0 / odd_b)) - 1.0
    assert roi == pytest.approx(expected_roi, abs=0.001)
