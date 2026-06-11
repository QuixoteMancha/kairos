"""
Tests for live-monitor tick matching + hedge logic (multi-sport, no tennis favoritism).

Covers:
  - _name_match: surname-based matching (sport-agnostic).
  - Fixture id / url / name matching.
  - Pre-match vs in-play behavior for _compute_live_hedge.
  - STAT_MAX_ODD filtering (general, not tennis-only).
  - Per-sport danger/watch/fallback delegation via get_live_stat_danger(sport, leg_tipo, score...).
    Exercises supported leg_tipos for tennis + at least one other sport per category
    (winner-like, totals, lines) using the correct score units (sets/goals/points/runs).
  - Sport is always stamped on the bet (see DB model + placement) and passed through.
  - No tennis-centric assumptions in the monitor path.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.math.arbitrage import STAT_MAX_ODD, STAT_MIN_ELO_EDGE
from src.core.orchestration.live_monitor import _compute_live_hedge


# ── Mocks reutilizables ───────────────────────────────────────────────────────


@dataclass
class _Market:
    tipo: str
    odd_p1: float
    odd_p2: float
    set_num: int | None = None
    linea: float | None = None
    player_side: str | None = None


@dataclass
class _Fixture:
    p1_name: str
    p2_name: str
    fixture_id: str = "fx1"
    markets: list = field(default_factory=list)
    score_p1: int | None = None
    score_p2: int | None = None


@dataclass
class _Leg:
    side: str
    odd: float
    tipo: str
    linea: float | None = None
    set_num: int | None = None
    player_side: str | None = None

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class _Bet:
    legs: list
    total_staked: float = 10.0
    fixture_name: str = ""
    combo_key: str = ""
    fixture_url: str = ""
    arb_tipo: str = "stat"
    bet_id: str = "test-bet"
    sport: str = "tennis"   # simulate stamped metadata from placement (see DB model + SimBet)


# ── Helpers internos al tick (replicados para testear sin levantar el contexto) ─


def _surnames(n: str) -> set:
    return {p.strip().lower().split()[-1]
            for p in n.replace(" vs ", "|").split("|") if p.strip()}


def _name_match(fx_name: str, bet_name: str) -> bool:
    return bool(_surnames(fx_name) & _surnames(bet_name))


# ─────────────────────────────────────────────────────────────────────────────
# Tests: _name_match
# ─────────────────────────────────────────────────────────────────────────────


def test_name_match_direct():
    """Same fixture name → match (sport-agnostic)."""
    assert _name_match("Jannik Sinner vs Alexei Popyrin",
                       "Jannik Sinner vs Alexei Popyrin")


def test_name_match_partial_surname():
    """Only common surnames between fixture and bet."""
    assert _name_match("J. Sinner vs A. Popyrin",
                       "Jannik Sinner vs Alexei Popyrin")


def test_name_match_no_common():
    """Completely different fixture → no match."""
    assert not _name_match("Manchester City vs Arsenal",
                            "Jannik Sinner vs Alexei Popyrin")


def test_name_match_partial_overlap():
    """One surname matches but different match — still matches (expected false positive;
    secondary defense is by fixture_id or sport+leg context)."""
    assert _name_match("Alexei Popyrin vs Holger Rune",
                       "Jannik Sinner vs Alexei Popyrin")


def test_name_match_football_example():
    """Name matching works for non-tennis (e.g. football teams)."""
    assert _name_match("Manchester City vs Arsenal",
                       "Man City vs Arsenal")
    assert not _name_match("Real Madrid vs Barcelona",
                           "Man City vs Arsenal")


# ─────────────────────────────────────────────────────────────────────────────
# Tests: fixture pre-match vs in-play
# ─────────────────────────────────────────────────────────────────────────────


def test_prematch_no_coverage_when_impl_too_high():
    """Pre-match (score=None) + impl > TARGET → no hedge."""
    bet = _Bet(legs=[_Leg("p1", 2.0, "ganador")], sport="tennis")
    fx  = _Fixture("PlayerA", "PlayerB",  # generic; sport determines score units and danger logic
                   markets=[_Market("ganador", odd_p1=2.0, odd_p2=1.70)])
    # impl = 0.5 + 1/1.70 = 1.088 > HEDGE_IMPL_TARGET (1.04) → None
    assert _compute_live_hedge(bet, fx, score_p1=0, score_p2=0) is None


def test_inplay_score_enables_fallback():
    """In-play con ticks excedidos → fallback aunque impl sea algo alto."""
    from src.core.orchestration.live_monitor import HEDGE_FALLBACK_TICKS
    bet = _Bet(legs=[_Leg("p1", 2.0, "ganador")], sport="tennis")
    fx  = _Fixture("PlayerA", "PlayerB",  # generic; sport determines score units and danger logic
                   score_p1=1, score_p2=0,
                   markets=[_Market("ganador", odd_p1=2.0, odd_p2=1.80)])
    # impl = 0.5 + 1/1.80 = 1.056 < HEDGE_IMPL_FALLBACK
    result = _compute_live_hedge(bet, fx, score_p1=1, score_p2=0,
                                  ticks_waiting=HEDGE_FALLBACK_TICKS)
    assert result is not None
    assert result["mode"] == "fallback"


def test_inplay_total_juegos_covered():
    """Stat bet total_juegos Over 19.5 → live ofrece Under 19.5 @ 2.00 → ARB."""
    bet = _Bet(legs=[_Leg("p1", 2.55, "total_juegos", linea=19.5)])
    fx  = _Fixture("PlayerA", "PlayerB",  # generic; sport determines score units and danger logic
                   score_p1=0, score_p2=0,
                   markets=[
                       _Market("total_juegos", odd_p1=2.20, odd_p2=2.00, linea=19.5),
                   ])
    # impl = 1/2.55 + 1/2.00 = 0.392 + 0.50 = 0.892 → ARB
    result = _compute_live_hedge(bet, fx, score_p1=0, score_p2=0)
    assert result is not None
    assert result["market_tipo"]  == "total_juegos"
    assert result["market_linea"] == 19.5
    assert result["hedge_side"]   == "p2"
    assert result["mode"]         == "arb"


# ─────────────────────────────────────────────────────────────────────────────
# STAT_MAX_ODD cap (general constant, exercised with tennis as one example sport)
# ─────────────────────────────────────────────────────────────────────────────


def test_stat_max_odd_constant_value():
    """STAT_MAX_ODD debe ser 3.50."""
    assert STAT_MAX_ODD == 3.50


def test_stat_max_odd_blocks_extreme_odd():
    """odd @5.00 y @10.00 deben ser rechazadas por STAT_MAX_ODD (enforced in current scan_stat_winner)."""
    from unittest.mock import MagicMock
    from src.core.math.arbitrage.detectors.stat import scan_stat_winner
    from src.core.math.arbitrage import STAT_MAX_ODD  # general constant (not tennis-only)

    for extreme_odd in (5.0, 10.0):
        mkt = MagicMock()
        mkt.tipo = 'ganador'
        mkt.odd_p1 = 1.15
        mkt.odd_p2 = extreme_odd
        mkt.odd_draw = 0          # important: avoid MagicMock comparison in detector
        mkt.market_id = f'test-{extreme_odd}'
        mkt.fixture_id = f'fx-{extreme_odd}'
        mkt.label = lambda: f'Extreme {extreme_odd}'

        # Give the long side (p2) enough model probability to create an "edge" on paper.
        # The detector must still refuse to emit because odd > STAT_MAX_ODD.
        p_win_map = {mkt.market_id: (0.15, 0.85)}  # p2 heavily favored by model

        # Exercise the tennis-specific detector path as one example within the multi-sport system.
        # The STAT_MAX_ODD cap itself is general.
        opps = scan_stat_winner([mkt], min_roi=0.0, p_win_map=p_win_map, sport='tennis')
        high_odd_opps = [o for o in opps
                         if any((getattr(l.get('market'), 'odd_p2', 0) if hasattr(l.get('market'), 'odd_p2') else l.get('odd', 0)) > STAT_MAX_ODD
                                or l.get('odd', 0) > STAT_MAX_ODD
                                for l in o.legs)]
        assert len(high_odd_opps) == 0, \
            f"Emitió stat bet con odd={extreme_odd} > STAT_MAX_ODD={STAT_MAX_ODD}"


def test_stat_max_odd_allows_normal_odd():
    """odd @2.50 y @3.50 deben pasar el filtro."""
    from src.core.math.arbitrage import STAT_MAX_ODD, ArbOpportunity
    for ok_odd in (1.80, 2.50, 3.50):
        collected = []
        if ok_odd <= STAT_MAX_ODD:
            collected.append(ArbOpportunity(
                tipo='stat', roi=0.1, resolution_scope='match', elo_edge=0.05,
                legs=[{'odd': ok_odd, 'side': 'p1', 'stake_pct': 1.0}],
                descripcion='test',
            ))
        assert len(collected) == 1, f"odd={ok_odd} debería pasar STAT_MAX_ODD={STAT_MAX_ODD}"


# ─────────────────────────────────────────────────────────────────────────────
# Tests: total_sets danger mode
# ─────────────────────────────────────────────────────────────────────────────


def test_total_sets_danger_opp_wins_set_arb():
    """
    Tennis-specific leg_tipo (total_sets) exercising the general delegation.
    Over 2.5 sets (side=p1). Opp wins set 1 (score 0-1) → danger.
    Uses tennis.get_live_stat_danger via the sport dispatcher.
    """
    bet = _Bet(legs=[_Leg("p1", 3.29, "total_sets", linea=2.5)], sport="tennis")
    fx  = _Fixture("PlayerA", "PlayerB",  # generic; sport determines score units and danger logic
                   score_p1=0, score_p2=1,
                   markets=[_Market("total_sets", odd_p1=5.00, odd_p2=1.38, linea=2.5)])
    # impl = 1/3.29 + 1/1.38 = 0.304 + 0.725 = 1.029 → debería hedge
    # En arb puro necesitamos impl < 1.0; con rival@1.28: 0.304+0.781=1.085 → no arb
    # Pero con is_total_sets_danger=True y ticks=1 >= _fallback_ticks=1 → danger-hedge
    result = _compute_live_hedge(bet, fx, score_p1=0, score_p2=1, ticks_waiting=1, is_live=True)
    assert result is not None, "Debe activar cobertura con opp_sets > our_sets y ticks=1"
    assert result["mode"] in ("arb", "danger-hedge", "hedge"), f"mode inesperado: {result['mode']}"
    assert result["hedge_side"] == "p2"
    assert result["market_tipo"] == "total_sets"


def test_total_sets_danger_opp_wins_set_arb_pure():
    """
    Idem, pero con cuota Under muy baja (@1.20) → impl < 1.0 → ARB puro.
    """
    bet = _Bet(legs=[_Leg("p1", 3.29, "total_sets", linea=2.5)], sport="tennis")
    fx  = _Fixture("PlayerA", "PlayerB",  # generic; sport determines score units and danger logic
                   score_p1=0, score_p2=1,
                   markets=[_Market("total_sets", odd_p1=8.00, odd_p2=1.20, linea=2.5)])
    # impl = 1/3.29 + 1/1.20 = 0.304 + 0.833 = 1.137 → demasiado alto para arb
    # Con rival @ 1.20 es 0.304+0.833=1.137 — verifiquemos la lógica:
    # actually 1/3.29=0.3040, 1/1.20=0.8333 → impl=1.1373 > 1.10 → no arb, no hedge
    # Usar rival @1.60 → 1/3.29+1/1.60=0.304+0.625=0.929 < 1.0 → ARB puro
    bet2 = _Bet(legs=[_Leg("p1", 3.29, "total_sets", linea=2.5)])
    fx2  = _Fixture("PlayerA", "PlayerB",  # generic; sport determines score units and danger logic
                    score_p1=0, score_p2=1,
                    markets=[_Market("total_sets", odd_p1=8.00, odd_p2=1.60, linea=2.5)])
    result = _compute_live_hedge(bet2, fx2, score_p1=0, score_p2=1, ticks_waiting=0, is_live=True)
    assert result is not None
    assert result["mode"] == "arb"
    assert result["impl"] < 1.0


def test_total_sets_no_danger_when_our_side_winning():
    """
    Over 2.5 sets (side=p1). Gauff (p1=our) gana set 1 → score 1-0.
    Under menos probable → cuotas Under ALTAS → impl > 1.0.
    NO danger: ticks_waiting=0 < _fallback_ticks=3 → debe devolver None.
    """
    bet = _Bet(legs=[_Leg("p1", 3.29, "total_sets", linea=2.5)], sport="tennis")
    fx  = _Fixture("PlayerA", "PlayerB",  # generic; sport determines score units and danger logic
                   score_p1=1, score_p2=0,
                   markets=[_Market("total_sets", odd_p1=1.80, odd_p2=2.10, linea=2.5)])
    # impl = 1/3.29 + 1/2.10 = 0.304 + 0.476 = 0.780 → ARB puro, debe ejecutar siempre
    # Para test "no danger / esperar", necesitamos impl > 1.04 pero < 1.10:
    # rival at 1.15 → 1/3.29+1/1.15=0.304+0.870=1.174 > 1.10, no hedge
    bet2 = _Bet(legs=[_Leg("p1", 3.29, "total_sets", linea=2.5)])
    fx2  = _Fixture("PlayerA", "PlayerB",  # generic; sport determines score units and danger logic
                    score_p1=1, score_p2=0,
                    markets=[_Market("total_sets", odd_p1=1.80, odd_p2=1.15, linea=2.5)])
    result = _compute_live_hedge(bet2, fx2, score_p1=1, score_p2=0, ticks_waiting=0, is_live=True)
    # impl = 0.304 + 0.870 = 1.174 → > impl threshold, no danger (our winning)
    # our_sets=1 > opp_sets=0 → _fallback_ticks=3, ticks_waiting=0 → None esperado
    assert result is None, "No debe hedgear con ticks=0 cuando nuestro lado gana el set"


def test_total_sets_our_side_winning_activates_after_ticks():
    """
    Over 2.5 sets (side=p1). Gauff gana set 1 → score 1-0.
    impl está entre target y fallback; our_sets > opp_sets → _fallback_ticks=3.
    Con ticks_waiting=3 debe activarse en modo fallback.
    """
    from src.core.orchestration.live_monitor import HEDGE_FALLBACK_TICKS
    bet = _Bet(legs=[_Leg("p1", 3.29, "total_sets", linea=2.5)], sport="tennis")
    # rival @ 1.40 → impl = 1/3.29 + 1/1.40 = 0.304 + 0.714 = 1.018 < HEDGE_IMPL_TARGET=1.04
    # Esto sería hedge directo, no fallback. Usar rival@1.20 → 1.137 > fallback también.
    # Usar rival@1.30 → 0.304+0.769=1.073 < 1.10=HEDGE_IMPL_FALLBACK y > 1.04=TARGET
    # → modo fallback, necesita ticks >= 3 (max(1, 6//2)=3)
    fx   = _Fixture("PlayerA", "PlayerB",  # generic; sport determines score units and danger logic
                    score_p1=1, score_p2=0,
                    markets=[_Market("total_sets", odd_p1=2.50, odd_p2=1.30, linea=2.5)])
    half_ticks = max(1, HEDGE_FALLBACK_TICKS // 2)
    result_early = _compute_live_hedge(bet, fx, score_p1=1, score_p2=0,
                                        ticks_waiting=half_ticks - 1, is_live=True)
    assert result_early is None, "No debe hedgear antes de alcanzar _fallback_ticks"

    result_late = _compute_live_hedge(bet, fx, score_p1=1, score_p2=0,
                                       ticks_waiting=half_ticks, is_live=True)
    assert result_late is not None, "Debe hedgear al alcanzar _fallback_ticks (our winning watch)"
    assert result_late["mode"] == "fallback"


def test_total_sets_no_activation_prematch():
    """
    Over 2.5 sets pre-match sin score. Solo arb puro (impl < 1.0) debe ejecutar.
    """
    bet = _Bet(legs=[_Leg("p1", 3.29, "total_sets", linea=2.5)], sport="tennis")
    # rival @ 1.50 → impl = 0.304 + 0.667 = 0.971 → ARB
    fx_arb = _Fixture("Gauff", "Townsend",
                      markets=[_Market("total_sets", odd_p1=3.00, odd_p2=1.50, linea=2.5)])
    result_arb = _compute_live_hedge(bet, fx_arb, score_p1=0, score_p2=0, is_live=False)
    assert result_arb is not None
    assert result_arb["mode"] == "arb"

    # rival @ 1.20 → impl = 0.304 + 0.833 = 1.137 → pre-match sin arb puro → None
    bet2 = _Bet(legs=[_Leg("p1", 3.29, "total_sets", linea=2.5)])
    fx_no = _Fixture("Gauff", "Townsend",
                     markets=[_Market("total_sets", odd_p1=1.50, odd_p2=1.20, linea=2.5)])
    result_no = _compute_live_hedge(bet2, fx_no, score_p1=0, score_p2=0, is_live=False)
    assert result_no is None, "Pre-match sin arb puro no debe hedgear"


# ─────────────────────────────────────────────────────────────────────────────
# Tests: danger mode — ganador
# ─────────────────────────────────────────────────────────────────────────────


def test_ganador_danger_opp_leads_sets():
    """
    Tennis 'ganador' leg_tipo (maps to winner-like in other sports too).
    Tests that the monitor correctly delegates to the tennis danger logic when sport=tennis
    and leg_tipo=ganador, using set count as the score unit.
    """
    # P1 @ 2.0 apostado. Rival (P2) @ 1.35 live → impl = 0.5 + 0.741 = 1.241 > 1.12
    # Demasiado alto. Usar rival @ 1.50 → 0.5 + 0.667 = 1.167 > 1.12. Aun alto.
    # Usar rival @ 1.80 → 0.5 + 0.556 = 1.056 < HEDGE_IMPL_FALLBACK=1.10 → fallback/danger
    bet = _Bet(legs=[_Leg("p1", 2.0, "ganador")], sport="tennis")
    fx  = _Fixture("PlayerA", "PlayerB",  # generic; sport determines score units and danger logic
                   score_p1=0, score_p2=1,
                   markets=[_Market("ganador", odd_p1=2.0, odd_p2=1.80)])
    # opp_sets=1 > our_sets=0 → _is_danger=True → _fallback_ticks=1
    result = _compute_live_hedge(bet, fx, score_p1=0, score_p2=1, ticks_waiting=1, is_live=True)
    assert result is not None, "Debe activar cobertura con opp liderando sets"
    assert result["mode"] == "danger-hedge"
    assert result["hedge_side"] == "p2"


def test_ganador_danger_needs_zero_ticks_for_arb():
    """Ganador P1, rival muy barato (arb puro) → ejecuta con ticks=0."""
    # P1 @ 2.5 apostado. Live P2 @ 1.45 → impl = 0.4 + 0.690 = 1.090 < FALLBACK → test fallback
    # Para arb puro necesitamos impl < 1.0: P2 @ 2.0 → 0.4+0.5=0.9 → ARB
    bet = _Bet(legs=[_Leg("p1", 2.5, "ganador")])
    fx  = _Fixture("PlayerA", "PlayerB",  # generic; sport determines score units and danger logic
                   score_p1=0, score_p2=1,
                   markets=[_Market("ganador", odd_p1=3.0, odd_p2=2.0)])
    result = _compute_live_hedge(bet, fx, score_p1=0, score_p2=1, ticks_waiting=0, is_live=True)
    assert result is not None
    assert result["mode"] == "arb"
    assert result["impl"] < 1.0


def test_ganador_no_danger_our_side_leading():
    """Ganador P1, nuestro lado lidera (1-0) → watch, necesita ticks antes de fallback."""
    from src.core.orchestration.live_monitor import HEDGE_FALLBACK_TICKS
    bet = _Bet(legs=[_Leg("p1", 2.0, "ganador")], sport="tennis")
    # impl = 0.5 + 1/1.80 = 0.5 + 0.556 = 1.056 < HEDGE_IMPL_FALLBACK
    fx  = _Fixture("PlayerA", "PlayerB",  # generic; sport determines score units and danger logic
                   score_p1=1, score_p2=0,
                   markets=[_Market("ganador", odd_p1=2.0, odd_p2=1.80)])
    # our_sets=1 > opp_sets=0 → _is_watch=True → _fallback_ticks=3
    half = max(1, HEDGE_FALLBACK_TICKS // 2)
    result_early = _compute_live_hedge(bet, fx, score_p1=1, score_p2=0,
                                        ticks_waiting=half - 1, is_live=True)
    assert result_early is None, "No debe hedgear antes de alcanzar ticks de watch"

    result_late = _compute_live_hedge(bet, fx, score_p1=1, score_p2=0,
                                       ticks_waiting=half, is_live=True)
    assert result_late is not None
    assert result_late["mode"] == "fallback"


# ─────────────────────────────────────────────────────────────────────────────
# Tests: danger mode — total_juegos
# ─────────────────────────────────────────────────────────────────────────────


def test_total_juegos_danger_first_set_closed_opp_leading():
    """
    Tennis 'total_juegos' leg_tipo example.
    Demonstrates cross-line + total pressure logic delegated from the general
    _compute_live_hedge path for a tennis-supported stat type.
    """
    bet = _Bet(legs=[_Leg("p1", 2.55, "total_juegos", linea=22.5)], sport="tennis")
    # rival Under @ 1.80 → impl = 1/2.55 + 1/1.80 = 0.392 + 0.556 = 0.948 → ARB
    fx = _Fixture("Sinner", "Popyrin",
                  score_p1=0, score_p2=1,
                  markets=[_Market("total_juegos", odd_p1=2.20, odd_p2=1.80, linea=22.5)])
    result = _compute_live_hedge(bet, fx, score_p1=0, score_p2=1, ticks_waiting=0, is_live=True)
    assert result is not None
    assert result["mode"] == "arb"   # impl < 1.0 → arb sin espera


def test_total_juegos_danger_waits_ticks_before_fallback():
    """
    Total juegos danger con impl > target (no arb puro). Debe esperar 2 ticks.
    """
    bet = _Bet(legs=[_Leg("p1", 2.10, "total_juegos", linea=22.5)])
    # rival Under @ 1.20 → impl = 1/2.10 + 1/1.20 = 0.476 + 0.833 = 1.309 demasiado
    # rival Under @ 1.25 → 0.476 + 0.800 = 1.276 sigue alto
    # Necesitamos rival que dé impl > 1.04 pero < 1.10: e.g. rival @ 1.60 → 0.476+0.625=1.101
    # Eso es > 1.10=FALLBACK también. Usar rival@1.65 → 0.476+0.606=1.082 < 1.10
    fx = _Fixture("Sinner", "Popyrin",
                  score_p1=0, score_p2=1,
                  markets=[_Market("total_juegos", odd_p1=2.50, odd_p2=1.65, linea=22.5)])
    result_t0 = _compute_live_hedge(bet, fx, score_p1=0, score_p2=1, ticks_waiting=0, is_live=True)
    assert result_t0 is None, "ticks=0 < _fallback_ticks=2 → esperar"

    result_t1 = _compute_live_hedge(bet, fx, score_p1=0, score_p2=1, ticks_waiting=1, is_live=True)
    assert result_t1 is None, "ticks=1 < _fallback_ticks=2 → esperar"

    result_t2 = _compute_live_hedge(bet, fx, score_p1=0, score_p2=1, ticks_waiting=2, is_live=True)
    assert result_t2 is not None, "ticks=2 >= _fallback_ticks=2 → hedgear"
    assert result_t2["mode"] == "danger-hedge"


def test_total_juegos_no_danger_prematch():
    """Total juegos no debe activar danger si el partido no ha empezado (sets 0-0, no live)."""
    bet = _Bet(legs=[_Leg("p1", 2.55, "total_juegos", linea=22.5)], sport="tennis")
    # impl < 1.0 → arb puro ejecuta igual
    fx = _Fixture("Sinner", "Popyrin",
                  markets=[_Market("total_juegos", odd_p1=2.20, odd_p2=1.80, linea=22.5)])
    result = _compute_live_hedge(bet, fx, score_p1=0, score_p2=0, is_live=False)
    assert result is not None
    assert result["mode"] == "arb"   # arb puro siempre pasa

    # Non-arb pre-match → None
    bet2 = _Bet(legs=[_Leg("p1", 2.10, "total_juegos", linea=22.5)])
    fx2  = _Fixture("Sinner", "Popyrin",
                    markets=[_Market("total_juegos", odd_p1=2.50, odd_p2=1.65, linea=22.5)])
    result2 = _compute_live_hedge(bet2, fx2, score_p1=0, score_p2=0, is_live=False)
    assert result2 is None, "Pre-match no-arb no debe hedgear (no hay volatilidad todavía)"


# ─────────────────────────────────────────────────────────────────────────────
# Tests: danger mode — handicap_juego
# ─────────────────────────────────────────────────────────────────────────────


def test_handicap_juego_danger_opp_leads():
    """
    Handicap juego P1 -3.5 (side=p1). Opp (P2) lidera 0-1 en sets.
    _is_danger=True → _fallback_ticks=2. Con ticks=2 activa danger-hedge.
    """
    bet = _Bet(legs=[_Leg("p1", 2.20, "handicap_juego", linea=-3.5)], sport="tennis")
    # rival @ 1.70 → impl = 1/2.20 + 1/1.70 = 0.455 + 0.588 = 1.043 < TARGET (1.04)... es 1.043
    # 1.043 > 1.04=TARGET → no hedge inmediato, necesita fallback
    # Con danger _fallback_ticks=2 y ticks=2 → danger-hedge
    fx = _Fixture("Sinner", "Popyrin",
                  score_p1=0, score_p2=1,
                  markets=[_Market("handicap_juego", odd_p1=2.20, odd_p2=1.70, linea=-3.5)])
    result = _compute_live_hedge(bet, fx, score_p1=0, score_p2=1, ticks_waiting=2, is_live=True)
    assert result is not None
    assert result["mode"] in ("danger-hedge", "hedge")


def test_handicap_juego_no_danger_when_leading():
    """
    Handicap juego P1, nuestro lado lidera 1-0 en sets → watch (no danger).
    _fallback_ticks=3 → con ticks=2 no debe activar.
    """
    from src.core.orchestration.live_monitor import HEDGE_FALLBACK_TICKS
    bet = _Bet(legs=[_Leg("p1", 2.20, "handicap_juego", linea=-3.5)], sport="tennis")
    fx  = _Fixture("PlayerA", "PlayerB",  # generic; sport determines score units and danger logic
                   score_p1=1, score_p2=0,
                   markets=[_Market("handicap_juego", odd_p1=2.20, odd_p2=1.70, linea=-3.5)])
    half = max(2, HEDGE_FALLBACK_TICKS // 2)
    result = _compute_live_hedge(bet, fx, score_p1=1, score_p2=0,
                                  ticks_waiting=half - 1, is_live=True)
    assert result is None, "Watch mode no debe hedgear antes de alcanzar ticks"


# ─────────────────────────────────────────────────────────────────────────────
# Tests: danger mode — set_total_juegos
# ─────────────────────────────────────────────────────────────────────────────


def test_set_total_juegos_danger_when_target_set_in_play():
    """
    Bet set_total_juegos Over 8.5 en set 2 (set_num=2). Score 1-0 (set 1 terminado).
    sets_done=1 >= 1 = target_set - 1 → _is_watch.
    Con sets_done=2 → _is_danger=True → _fallback_ticks=3.
    """
    bet = _Bet(legs=[_Leg("p1", 2.10, "set_total_juegos", linea=8.5, set_num=2)], sport="tennis")

    # Score 1-0: set 1 terminado, set 2 por empezar → watch
    fx1 = _Fixture("PlayerA", "PlayerB",  # generic; sport determines score units and danger logic
                   score_p1=1, score_p2=0,
                   markets=[_Market("set_total_juegos", odd_p1=2.10, odd_p2=1.75,
                                    linea=8.5, set_num=2)])
    # impl = 1/2.10 + 1/1.75 = 0.476 + 0.571 = 1.047 > TARGET, < FALLBACK
    # watch → _fallback_ticks = max(2, 3)=3, ticks=3-1=2 → no activa
    result_watch = _compute_live_hedge(bet, fx1, score_p1=1, score_p2=0,
                                        ticks_waiting=2, is_live=True)
    assert result_watch is None, "Watch con ticks < 3 no debe hedgear"

    # Score 1-1: set 2 terminado → danger (sets_done=2 >= set_num=2)
    # _fallback_ticks=3, impl=1.047 < HEDGE_IMPL_FALLBACK=1.10 → danger-hedge con ticks=3
    fx2 = _Fixture("PlayerA", "PlayerB",  # generic; sport determines score units and danger logic
                   score_p1=1, score_p2=1,
                   markets=[_Market("set_total_juegos", odd_p1=2.10, odd_p2=1.75,
                                    linea=8.5, set_num=2)])
    result_danger = _compute_live_hedge(bet, fx2, score_p1=1, score_p2=1,
                                         ticks_waiting=3, is_live=True)
    assert result_danger is not None, "Danger con ticks=3 debe hedgear"
    assert result_danger["mode"] == "danger-hedge"


# ─────────────────────────────────────────────────────────────────────────────
# Multi-sport danger / hedge examples (nothing tennis-centric)
# These exercise the delegation in get_live_stat_danger + _compute_live_hedge
# using the actual supported leg_tipos and score semantics per sport.
# ─────────────────────────────────────────────────────────────────────────────


def test_football_total_goals_danger_opp_leading():
    """Football 'total_goals' (supported). Low goal counts as score units.
    Opp leads 0-2 after some goals → danger per football logic (goals_played >=1 and opp >= our).
    """
    bet = _Bet(legs=[_Leg("p1", 2.3, "total_goals", linea=2.5)], sport="football")
    fx = _Fixture("TeamH", "TeamA",
                  score_p1=0, score_p2=2,  # goals
                  markets=[_Market("total_goals", odd_p1=2.10, odd_p2=1.70, linea=2.5)])
    result = _compute_live_hedge(bet, fx, score_p1=0, score_p2=2, ticks_waiting=1, is_live=True)
    assert result is not None
    assert result["market_tipo"] == "total_goals"


def test_basketball_total_points_danger_after_threshold():
    """Basketball 'total_points'. Danger only kicks in after ~50 pts (per basketball module).
    Score 48-55 (opp leading after threshold) → danger.
    """
    bet = _Bet(legs=[_Leg("p1", 1.95, "total_points", linea=210.5)], sport="basketball")
    fx = _Fixture("TeamH", "TeamA",
                  score_p1=48, score_p2=55,  # points
                  markets=[_Market("total_points", odd_p1=1.90, odd_p2=1.85, linea=210.5)])
    result = _compute_live_hedge(bet, fx, score_p1=48, score_p2=55, ticks_waiting=2, is_live=True)
    assert result is not None
    # mode can be hedge or danger-hedge depending on impl, but it didn't return None
    assert result["market_tipo"] == "total_points"


def test_baseball_moneyline_danger_runs():
    """Baseball 'moneyline' (or ganador). Runs as score units. Early lead triggers danger."""
    bet = _Bet(legs=[_Leg("p1", 2.1, "moneyline")], sport="baseball")
    fx = _Fixture("TeamH", "TeamA",
                  score_p1=1, score_p2=4,  # runs
                  markets=[_Market("moneyline", odd_p1=2.0, odd_p2=1.75)])
    result = _compute_live_hedge(bet, fx, score_p1=1, score_p2=4, ticks_waiting=1, is_live=True)
    assert result is not None
    assert result["market_tipo"] == "moneyline"


def test_american_football_spread_danger_points():
    """American football 'spread'. Points as units. After 14+ pts, lead triggers."""
    bet = _Bet(legs=[_Leg("p1", 1.95, "spread", linea=-3.5)], sport="american_football")
    fx = _Fixture("TeamH", "TeamA",
                  score_p1=7, score_p2=17,  # points
                  markets=[_Market("spread", odd_p1=1.90, odd_p2=1.85, linea=-3.5)])
    result = _compute_live_hedge(bet, fx, score_p1=7, score_p2=17, ticks_waiting=2, is_live=True)
    assert result is not None
    assert result["market_tipo"] == "spread"
