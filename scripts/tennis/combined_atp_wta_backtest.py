"""Backtest COMBINADO ATP+WTA compartiendo un único pool de capital --
2026-08-05, pedido explícito del usuario: "Ahora una corrida combinada
atp + wta con la estrategia seleccionada para atp + la estrategia 1
para wta".

ESTRATEGIAS APLICADAS (por circuito, sobre su propio pool filtrado ya
comiteado):
  - ATP: variante 8 ("8_exit_double_down_leg_if_favorite_loses_set2"),
    documentada como "la elegida como mejor" en
    `favorite_by_historical_rank_backtest.py` -- pre-match + refuerzo
    si el favorito gana el set 1, pero si LUEGO pierde el set 2 se
    retira SOLO la pata de refuerzo (revaluada a `price_at_1_1`).
  - WTA: variante 1 ("1_simple_bet_favorite") -- apostar al favorito
    pre-partido y sostener hasta el resultado final, sin refuerzo/retiro.

POOLS DE ORIGEN (sin cambios, mismos filtros ya comiteados):
  - ATP: `favorite_surface_winrate_filter_backtest.py::_build_surface_winrate_tracker`,
    filtrado por gap>=20 + winrate real por superficie (torneo 250+ ya
    aplicado por defecto vía `TOUR_LEVELS_250_PLUS`).
  - WTA: `wta_favorite_surface_winrate_filter_backtest.py::collect_wta_cases`
    + `_build_surface_winrate_tracker`, filtrado por gap>=30 + winrate
    real por superficie + torneo 250+.

CAPITAL COMPARTIDO: un solo bankroll simulado para AMBOS circuitos --
mismo cupo de exposición simultánea diaria (50% del capital, pedido
original ya vigente en ambos backtests individuales) y mismo tamaño de
apuesta (1% del capital, pedido original), pero el cupo diario ahora
se comparte entre TODOS los partidos de esa fecha sin importar el
circuito (más realista: un bankroll real de Kairos operaría ambos
circuitos a la vez, no dos bankrolls separados). Prioridad dentro del
día: mejor ranking primero (mismo criterio ya usado en cada backtest
individual), sin distinguir circuito -- es solo un desempate de cola
para el cupo, no una comparación de fuerza entre tours."""

from __future__ import annotations

import json
from typing import Any

from outer_apis.kalshi.fees import leg_fee_fraction
from scripts.tennis.favorite_by_historical_rank_backtest import (
    _real_price_legs, _price_at_1_1, _cagr_pct, _annualized_sharpe, _annualized_sortino,
    _calmar_ratio, _cvar_95_pct, _max_drawdown_pct,
)
from scripts.tennis.favorite_surface_winrate_filter_backtest import (
    _build_surface_winrate_tracker as _atp_surface_tracker, _MIN_RANK_GAP as _ATP_MIN_RANK_GAP,
)
from scripts.tennis.wta_favorite_surface_winrate_filter_backtest import (
    collect_wta_cases, _build_surface_winrate_tracker as _wta_surface_tracker, _MIN_RANK_GAP as _WTA_MIN_RANK_GAP,
)


def _build_combined_cases() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Arma el pool combinado: casos ATP (gap>=20+superficie) tageados
    `strategy="8"`, casos WTA (gap>=30+superficie) tageados
    `strategy="1"` -- cada uno con SU precio real (motor calibrado por
    `tour`), listos para simulación conjunta."""
    atp_all, atp_meta = _atp_surface_tracker()
    atp_filtered = [
        c for c in atp_all
        if c["rank_gap"] >= _ATP_MIN_RANK_GAP and c["favorite_surface_winrate"] > c["underdog_surface_winrate"]
    ]

    wta_raw, wta_meta1 = collect_wta_cases()
    wta_all, wta_meta2 = _wta_surface_tracker(wta_raw)
    wta_filtered = [
        c for c in wta_all
        if c["rank_gap"] >= _WTA_MIN_RANK_GAP and c["favorite_surface_winrate"] > c["underdog_surface_winrate"]
    ]

    combined: list[dict[str, Any]] = []
    skipped_atp_invalid_best_of = 0
    for c in atp_filtered:
        best_of = c.get("best_of") or 3
        if best_of not in (3, 5):
            skipped_atp_invalid_best_of += 1
            continue
        price_pre, price_win, price_loss = _real_price_legs(
            c["favorite_rank"], c["underdog_rank"], best_of, c.get("surface"), tour="ATP",
        )
        price_1_1 = _price_at_1_1(c["favorite_rank"], c["underdog_rank"], best_of, c.get("surface"), tour="ATP")
        combined.append({
            **c, "tour": "ATP", "strategy": "8", "best_of": best_of,
            "price_pre_match": price_pre, "price_after_set1_win": price_win,
            "price_after_set1_loss": price_loss, "price_at_1_1": price_1_1,
        })

    for c in wta_filtered:
        price_pre, _, _ = _real_price_legs(c["favorite_rank"], c["underdog_rank"], 3, c.get("surface"), tour="WTA")
        combined.append({
            **c, "tour": "WTA", "strategy": "1", "best_of": 3,
            "price_pre_match": price_pre,
        })

    return combined, {
        "atp_filtered_cases_n": len(atp_filtered), "wta_filtered_cases_n": len(wta_filtered),
        "skipped_atp_invalid_best_of": skipped_atp_invalid_best_of,
        **{f"atp_{k}": v for k, v in atp_meta.items()},
        **{f"wta_{k}": v for k, v in wta_meta1.items()}, **{f"wta_{k}": v for k, v in wta_meta2.items()},
    }


def _measure_trading_days_per_year(cases: list[dict[str, Any]], default: float = 60.0) -> float:
    dates = sorted({c["date"] for c in cases})
    if len(dates) < 2:
        return default
    from datetime import date as _date
    first, last = _date.fromisoformat(dates[0]), _date.fromisoformat(dates[-1])
    years = (last - first).days / 365.25
    return len(dates) / years if years > 0 else default


def _entry_fee_frac(price: float) -> float:
    fee_per_contract = leg_fee_fraction(price) or 0.0
    return fee_per_contract / price if price > 0 else 0.0


def _net_hold_pct(price: float, hit: bool) -> float:
    raw = (1 - price) / price if hit else -1.0
    return raw - _entry_fee_frac(price)


def _net_reprice_pct(entry_price: float, exit_price: float) -> float:
    raw = (exit_price / entry_price) - 1.0
    return raw - _entry_fee_frac(entry_price) - _entry_fee_frac(exit_price)


def simulate_combined(
    cases: list[dict[str, Any]], initial_capital: float = 50.0, stake_pct: float = 0.01,
    max_simultaneous_pct: float = 0.5, max_stake_usd: float | None = 60_000.0,
) -> dict[str, Any]:
    """Un único bankroll compartido -- cada caso aplica la lógica de SU
    `strategy` tag ("1"=simple, "8"=refuerzo con retiro condicional al
    set 2, ver docstring del módulo), pero todos compiten por el MISMO
    cupo diario de exposición (50% del capital compartido)."""
    by_date: dict[str, list[dict[str, Any]]] = {}
    for c in cases:
        by_date.setdefault(c["date"], []).append(c)

    capital = initial_capital
    busted = False
    trade_returns: list[float] = []
    resolve_hits = resolve_n = 0
    equity_curve: list[float] = [initial_capital]
    skipped_cap = 0
    by_tour_trades = {"ATP": 0, "WTA": 0}

    def _sized_stake(cap_start: float) -> float:
        stake = cap_start * stake_pct
        return min(stake, max_stake_usd) if max_stake_usd is not None else stake

    for date in sorted(by_date.keys()):
        if busted:
            equity_curve.append(capital)
            continue
        cap_start = capital
        max_exposure = cap_start * max_simultaneous_pct
        running_exposure = 0.0
        day_pnl = 0.0

        # mismo criterio de prioridad ya usado en cada backtest
        # individual: mejor ranking (número más bajo) primero, sin
        # distinguir circuito -- solo desempate de cola para el cupo.
        for c in sorted(by_date[date], key=lambda x: x["favorite_rank"]):
            hit = c["hit"]
            by_tour_trades[c["tour"]] += 1

            if c["strategy"] == "1":
                stake = _sized_stake(cap_start)
                if running_exposure + stake > max_exposure:
                    skipped_cap += 1
                    continue
                running_exposure += stake
                pct = _net_hold_pct(c["price_pre_match"], hit)
                day_pnl += stake * pct
                trade_returns.append(pct)
                resolve_n += 1
                resolve_hits += 1 if hit else 0
                continue

            # strategy == "8" (ATP, la elegida)
            s1 = c["set1_favorite_win"]
            p_pre, p_win, p_loss = c["price_pre_match"], c["price_after_set1_win"], c["price_after_set1_loss"]
            p_1_1 = c.get("price_at_1_1")
            s2 = c.get("set2_favorite_win")

            stake = _sized_stake(cap_start)
            if running_exposure + stake > max_exposure:
                skipped_cap += 1
                continue
            running_exposure += stake
            if s1 is False:
                pct = _net_reprice_pct(p_pre, p_loss)
                day_pnl += stake * pct
                trade_returns.append(pct)  # reprecio, no cuenta para winrate
            else:
                pct = _net_hold_pct(p_pre, hit)
                day_pnl += stake * pct
                trade_returns.append(pct)
                resolve_n += 1
                resolve_hits += 1 if hit else 0
                if s1 is True:
                    add_stake = _sized_stake(cap_start)
                    if running_exposure + add_stake <= max_exposure:
                        running_exposure += add_stake
                        if s2 is False and p_1_1 is not None:
                            pct2 = (p_1_1 / p_win) - 1.0
                            day_pnl += add_stake * pct2
                            trade_returns.append(pct2)
                        else:
                            pct2 = _net_hold_pct(p_win, hit)
                            day_pnl += add_stake * pct2
                            trade_returns.append(pct2)
                            resolve_n += 1
                            resolve_hits += 1 if hit else 0
                    else:
                        skipped_cap += 1

        capital += day_pnl
        if capital <= 0:
            capital = 0.0
            busted = True
        equity_curve.append(capital)

    trading_days_per_year = _measure_trading_days_per_year(cases)
    max_dd = _max_drawdown_pct(equity_curve)
    cagr = _cagr_pct(equity_curve, initial_capital, trading_days_per_year=trading_days_per_year)

    return {
        "n_cases": len(cases), "n_trades": resolve_n, "skipped_daily_cap": skipped_cap,
        "n_trades_by_tour": by_tour_trades,
        "hit_rate": round(resolve_hits / resolve_n, 4) if resolve_n else None,
        "final_capital": round(capital, 2), "busted": busted,
        "trading_days_per_year_measured": round(trading_days_per_year, 2),
        "max_drawdown_pct": max_dd, "cagr_pct": cagr,
        "annualized_sharpe": _annualized_sharpe(equity_curve, initial_capital, trading_days_per_year=trading_days_per_year),
        "annualized_sortino": _annualized_sortino(equity_curve, initial_capital, trading_days_per_year=trading_days_per_year),
        "calmar_ratio": _calmar_ratio(cagr, max_dd),
        "cvar_95_pct": _cvar_95_pct(trade_returns),
    }


def run_backtest(initial_capital: float = 50.0, max_stake_usd: float | None = 60_000.0) -> dict[str, Any]:
    combined_cases, meta = _build_combined_cases()
    combined_prioritized = sorted(combined_cases, key=lambda c: (c["date"], c["favorite_rank"]))
    result = simulate_combined(combined_prioritized, initial_capital=initial_capital, max_stake_usd=max_stake_usd)
    return {
        **meta, "combined": result,
        "caveat": (
            "Un solo bankroll compartido entre ATP (variante 8, la elegida) "
            "y WTA (variante 1, simple) -- mismo cupo diario de exposición "
            "(50%) y mismo stake (1%) que cada backtest individual, pero "
            "ahora compitiendo por el MISMO capital y el MISMO cupo (más "
            "realista que 2 bankrolls separados). Prioridad diaria: mejor "
            "ranking primero, sin distinguir circuito."
        ),
    }


if __name__ == "__main__":
    print(json.dumps(run_backtest(), indent=2, ensure_ascii=False))
