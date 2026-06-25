"""
Tests para el bug de settlement de stat bets pre-partido.

Problema original:
  - Stat bets colocadas el día ANTES del partido usan bet.timestamp[:10] como
    fecha de búsqueda en Sofascore. El partido es al día siguiente → Sofascore
    devuelve None → sp1/sp2 = None → _compute_bet_actual_return devuelve None →
    la bet queda pendiente forever.

Fixes aplicados:
  1. Sofascore: intenta también +1 y +2 días cuando la búsqueda inicial falla.
  2. DB fallback: si sp1 sigue siendo None, lee resultado directo de match.resultado.

Estos tests validan:
  - _compute_bet_actual_return: ganador con sp1/sp2 conocidos → resultado correcto.
  - _compute_bet_actual_return: ganador con sp1=None → None (no puede resolver).
  - _compute_bet_actual_return: total_juegos con datos completos → correcto.
  - La función no lanza excepciones con inputs incompletos.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

# The entire module exercised the old private _compute_bet_actual_return
# (and related helpers) that lived in src.web.dashboard / auto_bets before
# the core modularization. That symbol no longer exists.
# Real logic is now in:
#   - src.core.math.arbitrage.detectors.tennis._outcome_wins
#   - src.core.math.simulation.VirtualWallet.resolve_bet
#
# We keep the tests (and the adapter) as regression documentation for the
# many score → win/lose/return cases (Sinner/Popyrin bug, period_scores derivation, etc.).
#
# All tests now pass against the current _outcome_wins + adapter (no xfail markers).
# Improvements made to support this:
#   - _outcome_wins now derives total_games/games_p1/p2 from period_scores when missing.
#   - Early None guards for sp1/sp2 to avoid crashes on incomplete data and return None cleanly.
#   - Adapter rounds monetary returns to 2 decimals to avoid float artifacts.
# The real autonomous settlement (resolve_bet + settlement_rules + third-source) uses the same core.


# ── Stubs de modelos ──────────────────────────────────────────────────────────

@dataclass
class _Leg:
    side: str
    odd: float
    stake: float
    tipo: str
    linea: float | None = None
    set_num: int | None = None
    player_side: str | None = None
    market_label: str = ""


@dataclass
class _Bet:
    legs: list
    total_staked: float = 10.0
    arb_tipo: str = "stat"
    guaranteed_return: float = 0.0


# ── Helper: construir objeto compatible con _compute_bet_actual_return ────────

def _make_bet(tipo: str, side: str, odd: float, stake: float,
              linea: float | None = None, player_side: str | None = None) -> _Bet:
    leg = _Leg(side=side, odd=odd, stake=stake, tipo=tipo,
               linea=linea, player_side=player_side)
    return _Bet(legs=[leg], total_staked=stake)


# ── Usar la lógica real de resolución (movida a detectors/tennis durante refactor) ──
from src.core.math.arbitrage.detectors.tennis import _outcome_wins

# Adaptador mínimo: convierte un _Leg del test en algo que _outcome_wins entiende.
# El código real hace `if t == BetMarket.TIPO_GANADOR` etc, por lo que Fake debe
# exponer las constantes como atributos de clase (los valores son los strings
# que se guardan en la columna 'tipo' del modelo).
class _FakeMarket:
    # Valores típicos que el modelo BetMarket usaba para .tipo
    TIPO_GANADOR = 'ganador'
    TIPO_SET_GANADOR = 'set_ganador'
    TIPO_HANDICAP_SET = 'handicap_set'
    TIPO_HANDICAP_JUEGO = 'handicap_juego'
    TIPO_TOTAL_JUEGOS = 'total_juegos'
    TIPO_TOTAL_SETS = 'total_sets'
    TIPO_SET_TOTAL_JUEGOS = 'set_total_juegos'
    TIPO_ACES = 'aces'
    TIPO_DOBLES_FALTAS = 'dobles_faltas'

    def __init__(self, leg):
        self.tipo = leg.tipo
        self.linea = leg.linea
        self.set_num = leg.set_num
        self.player_side = leg.player_side

def _compute(bet, sp1, sp2, total_games=None, games_p1=None, games_p2=None,
             period_scores=None):
    """
    Re-implementación ligera del viejo _compute_bet_actual_return usando la
    función canónica _outcome_wins (ahora en core).
    Retorna el retorno real si la pata gana, 0 si pierde, None si no determinístico.
    """
    # Nota: BetMarket.TIPO_* (usadas por _outcome_wins) son ahora atributos
    # reales del modelo (ver domain.py) -- el workaround de monkeypatch que
    # vivía aquí ya no hace falta.
    total_return = 0.0
    for leg in (bet.legs or []):
        m = _FakeMarket(leg)
        side = leg.side
        won = _outcome_wins(m, side, sp1, sp2,
                            total_games=total_games,
                            games_p1=games_p1, games_p2=games_p2,
                            period_scores=period_scores)
        if won is True:
            total_return += leg.stake * leg.odd
        elif won is False:
            # perdió esta pata → no suma
            pass
        else:
            # no determinístico (None) → la función vieja retornaba None en muchos casos
            return None
    # Round to cents to match historical expectations and avoid float noise
    # (e.g. 5.26 * 2.22 produces 11.6772... which the old helper presented as 11.68).
    if total_return > 0:
        return round(total_return, 2)
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Tests: ganador bet — el caso clave del bug Sinner/Popyrin
# ─────────────────────────────────────────────────────────────────────────────

def test_ganador_p2_lost_when_p1_wins():
    """
    Bet sobre P2 (Popyrin @ 15.0) — Sinner ganó 2-0 → sp1=2, sp2=0.
    La apuesta pierde → retorno = 0.0 < stake → won=False.
    """
    bet = _make_bet("ganador", side="p2", odd=15.0, stake=2.80)
    ret = _compute(bet, sp1=2, sp2=0)
    # P2 perdió → no cobra nada
    assert ret == 0.0


def test_ganador_p1_won_returns_correct_amount():
    """
    Bet sobre P1 (Navone @ 2.22) — Navone ganó 2-1 → sp1=2, sp2=1.
    Retorno = stake * odd.
    """
    bet = _make_bet("ganador", side="p1", odd=2.22, stake=5.26)
    ret = _compute(bet, sp1=2, sp2=1)
    assert ret == round(5.26 * 2.22, 2)


def test_ganador_p2_won_returns_correct_amount():
    """
    Bet sobre P2 (Ruud @ 6.0) — Ruud ganó 2-0 → sp1=0, sp2=2.
    Retorno = stake * odd.
    """
    bet = _make_bet("ganador", side="p2", odd=6.0, stake=4.79)
    ret = _compute(bet, sp1=0, sp2=2)
    assert ret == round(4.79 * 6.0, 2)


def test_ganador_sp1_none_returns_none():
    """
    BUG ORIGINAL: sin sp1/sp2 (Sofascore no encontró el partido) → None.
    El fix de DB fallback garantiza que este caso no persiste.
    Esta prueba valida que _compute_bet_actual_return reconoce correctamente
    la falta de datos y devuelve None (sin crashear).
    """
    bet = _make_bet("ganador", side="p2", odd=15.0, stake=2.80)
    ret = _compute(bet, sp1=None, sp2=None)
    assert ret is None


# ─────────────────────────────────────────────────────────────────────────────
# Tests: total_juegos — el otro tipo de stat bet afectado
# ─────────────────────────────────────────────────────────────────────────────

def test_total_juegos_over_lost_when_total_below_line():
    """
    Bet Over 17.5 @ 1.66 — partido terminó 6-2 6-0 = 14 juegos → Under gana.
    total=14 < 17.5 → p1 (Over) pierde → retorno 0.0.
    """
    bet = _make_bet("total_juegos", side="p1", odd=1.66, stake=5.0, linea=17.5)
    ret = _compute(bet, sp1=2, sp2=0, total_games=14,
                   period_scores=[[6, 2], [6, 0]])
    assert ret == 0.0


def test_total_juegos_over_won_when_total_above_line():
    """
    Bet Over 17.5 @ 1.66 — partido terminó 7-5 6-3 = 21 juegos → Over gana.
    """
    bet = _make_bet("total_juegos", side="p1", odd=1.66, stake=5.0, linea=17.5)
    ret = _compute(bet, sp1=2, sp2=0, total_games=21,
                   period_scores=[[7, 5], [6, 3]])
    assert ret == round(5.0 * 1.66, 2)


def test_total_juegos_no_total_returns_none_for_stat():
    """
    Sin total_games ni period_scores → _compute devuelve None para stat bets.
    El auto-settle debe saltar este bet y esperar datos reales.
    """
    bet = _make_bet("total_juegos", side="p1", odd=2.03, stake=3.0, linea=18.5)
    ret = _compute(bet, sp1=2, sp2=0, total_games=None)
    assert ret is None


# ─────────────────────────────────────────────────────────────────────────────
# Tests: period_scores derivan total_games automáticamente
# ─────────────────────────────────────────────────────────────────────────────

def test_total_games_derived_from_period_scores():
    """
    Si total_games=None pero period_scores=[[6,2],[6,0]] → total=14.
    Bet Over 18.5 → 14 < 18.5 → p1 pierde → 0.0.
    """
    bet = _make_bet("total_juegos", side="p1", odd=2.03, stake=3.0, linea=18.5)
    ret = _compute(bet, sp1=2, sp2=0, total_games=None,
                   period_scores=[[6, 2], [6, 0]])
    assert ret == 0.0


def test_total_games_derived_from_period_scores_over_wins():
    """
    period_scores=[[6,4],[7,5]] → total=22 > Over 19.5 → p1 gana.
    """
    bet = _make_bet("total_juegos", side="p1", odd=2.55, stake=4.0, linea=19.5)
    ret = _compute(bet, sp1=2, sp2=0, total_games=None,
                   period_scores=[[6, 4], [7, 5]])
    assert ret == round(4.0 * 2.55, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: total_sets — bug Kalinina/Parry (Pinnacle mislabel total_juegos→total_sets)
# ─────────────────────────────────────────────────────────────────────────────

def test_total_sets_over_lost_when_match_2sets():
    """
    Bet Over 2.5 sets @ 2.64 — partido terminó 2-0 (2 sets) → Under gana.
    n_sets=2 < 2.5 → p1 (Over) pierde → retorno 0.0.
    Bug original: con tipo=total_juegos + linea=2.5 → total_games (ej 14) > 2.5 → siempre WON (INCORRECTO).
    """
    bet = _make_bet("total_sets", side="p1", odd=2.64, stake=7.48, linea=2.5)
    ret = _compute(bet, sp1=2, sp2=0, total_games=14,
                   period_scores=[[6, 2], [6, 3]])
    assert ret == 0.0


def test_total_sets_over_won_when_match_3sets():
    """
    Bet Over 2.5 sets @ 2.64 — partido terminó 2-1 (3 sets) → Over gana.
    n_sets=3 > 2.5 → p1 gana → retorno = stake * odd.
    """
    bet = _make_bet("total_sets", side="p1", odd=2.64, stake=7.48, linea=2.5)
    ret = _compute(bet, sp1=2, sp2=1, total_games=26,
                   period_scores=[[6, 4], [3, 6], [7, 5]])
    assert ret == round(7.48 * 2.64, 2)


def test_total_sets_under_won_when_match_2sets():
    """
    Bet Under 2.5 sets (side='p2') — partido 2-0 → n_sets=2 < 2.5 → Under gana.
    """
    bet = _make_bet("total_sets", side="p2", odd=1.45, stake=5.0, linea=2.5)
    ret = _compute(bet, sp1=2, sp2=0, total_games=14,
                   period_scores=[[6, 2], [6, 3]])
    assert ret == round(5.0 * 1.45, 2)


def test_total_sets_uses_sp1_sp2_fallback_without_period_scores():
    """
    Sin period_scores: usa sp1+sp2 como n_sets.
    Partido 2-1 (3 sets), Over 2.5 → n_sets=3 > 2.5 → gana.
    """
    bet = _make_bet("total_sets", side="p1", odd=2.64, stake=7.48, linea=2.5)
    ret = _compute(bet, sp1=2, sp2=1, total_games=26)
    assert ret == round(7.48 * 2.64, 2)


def test_total_sets_none_when_no_score_data():
    """
    Sin sp1/sp2 ni period_scores → no evaluable → None.
    """
    bet = _make_bet("total_sets", side="p1", odd=2.64, stake=7.48, linea=2.5)
    ret = _compute(bet, sp1=None, sp2=None, total_games=None)
    assert ret is None
