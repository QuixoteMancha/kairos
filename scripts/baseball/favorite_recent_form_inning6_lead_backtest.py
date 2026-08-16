"""Backtest MLB: equivalente a la variante 2 de tenis ("entrar SOLO
cuando el favorito ya ganó el set 1", sin exposición pre-partido) --
2026-08-05, pedido explícito del usuario: "el equipo que tiene mejores
estadísticas recientes lleva la ventaja a la séptima entrada de al
menos 3 puntos".

EQUIVALENCIAS CON TENIS (variante 2 de
`scripts/tennis/favorite_by_historical_rank_backtest.py::_build_strategy_comparison`):
  - "favorito gana el set 1" = "el equipo de mejor FORMA RECIENTE
    (winrate point-in-time de sus últimos N partidos, no el Elo de
    toda la temporada como en `favorite_elo_homeaway_filter_backtest.py`
    -- pedido explícito: "estadísticas recientes") llega a la 7ma
    entrada con ventaja >= 3 carreras".
  - SIN posición pre-partido: a diferencia de la estrategia de Elo (que
    apuesta pre-partido), acá NO hay capital expuesto mientras se
    juegan las primeras 6 entradas -- se entra recién al checkpoint,
    igual que la variante 2 de tenis (que entra recién si el favorito
    ya ganó el set 1, sin apuesta previa).
  - "precio" = a diferencia del backtest de Elo (que usa el fair price
    del propio motor), acá el precio es una tabla de frecuencias
    REAL point-in-time: P(el equipo que va arriba por X carreras en el
    checkpoint termina ganando), construida incrementalmente sobre el
    HISTÓRICO REAL de partidos previos (no restringida a los casos que
    califican como "favorito de forma reciente arriba" -- se usa TODO
    el checkpoint real, para no sesgar la muestra de calibración). Esto
    es honesto point-in-time por construcción: la tabla de frecuencias
    para el partido evaluado en la fecha X solo usa checkpoints de
    partidos ANTERIORES a X.

CHECKPOINT: cumulativo de carreras a través de la 6ta entrada
INCLUSIVE ("llega a la séptima entrada" = ya completó la 6ta) --
reusa `_cumulative_runs_through_inning` de `mlb_comeback_analysis.py`
(mismo criterio fail-closed: None si el partido no llegó a jugar esa
entrada completa, ej. terminó antes por lluvia/formato acortado).

DB-FIRST: NO re-fetchea linescores -- `MlbGameHistory.innings_json`
YA los tiene guardados desde el backfill original
(`scripts/baseball/mlb_local_backfill.py::backfill_year`, mismo campo
que usa `mlb_comeback_analysis.py` para el checkpoint F5), en el MISMO
formato ({"num","home_runs","away_runs"} por entrada) que espera
`_cumulative_runs_through_inning`."""

from __future__ import annotations

import json
from typing import Any

from database.db import init_db_historical
from database.historical.baseball import MlbGameHistory
from outer_apis.kalshi.fees import leg_fee_fraction
from scripts.baseball.mlb_comeback_analysis import _cumulative_runs_through_inning
from scripts.reports import risk_metrics

_INNING_6_CHECKPOINT = 6  # cumulativo a través de la 6ta entrada -- "llega a la séptima"
_MIN_LEAD_MARGIN = 3  # pedido explícito del usuario
_RECENT_FORM_WINDOW = 10  # últimos N partidos reales del equipo, point-in-time
_MIN_RECENT_FORM_GAMES = 5  # muestra mínima real antes de confiar en el winrate reciente (fail-closed)
_MIN_BUCKET_SAMPLES = 30  # muestra mínima real en el bucket de margen antes de confiar en el precio (fail-closed)
_MAX_MARGIN_BUCKET = 7  # márgenes >=7 colapsan en un solo bucket (muestra real escasa más allá de esto)


def collect_games_with_innings_from_db() -> list[dict[str, Any]]:
    """DB-first, NUNCA toca la red -- lee `MlbGameHistory` completo,
    incluido `innings_json` ya persistido, ordenado cronológicamente.
    Fail-closed: descarta filas sin resultado final o sin linescore
    real (partido sin `innings_json`, ej. postemporada acortada o
    fallo de fetch en el backfill original -- nunca se fabrica)."""
    init_db_historical()
    rows = list(MlbGameHistory.select().order_by(MlbGameHistory.date))
    result = []
    for r in rows:
        if r.home_runs_final is None or r.away_runs_final is None or not r.innings_json:
            continue
        try:
            innings = json.loads(r.innings_json)
        except (ValueError, TypeError):
            continue
        result.append({
            "date": r.date, "home_team": r.home_team, "away_team": r.away_team,
            "home_won": r.home_won, "innings": innings,
        })
    return result


def _margin_bucket(margin: int) -> int:
    return min(margin, _MAX_MARGIN_BUCKET)


def _build_recent_form_lead_tracker(games: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Recorre el histórico real una sola vez en orden cronológico,
    manteniendo (1) forma reciente point-in-time por equipo (últimos
    `_RECENT_FORM_WINDOW` partidos) y (2) una tabla de frecuencias
    point-in-time de "equipo arriba por X carreras en el checkpoint ->
    ¿ganó el partido?" -- arma `cases` solo para los partidos donde el
    equipo de MEJOR forma reciente es efectivamente el que va arriba
    por >= `_MIN_LEAD_MARGIN` en el checkpoint."""
    recent: dict[str, list[bool]] = {}  # team -> [won, won, ...] cronológico
    # bucket de margen -> [wins_del_equipo_que_iba_arriba, total]
    bucket_record: dict[int, list[int]] = {}

    cases: list[dict[str, Any]] = []
    skipped_no_checkpoint = skipped_tied_at_checkpoint = 0
    skipped_no_recent_form_history = skipped_lead_not_by_favorite = 0
    skipped_margin_below_threshold = skipped_no_bucket_samples = 0

    for g in games:
        home, away, home_won = g["home_team"], g["away_team"], g["home_won"]

        checkpoint = _cumulative_runs_through_inning({"innings": g["innings"]}, _INNING_6_CHECKPOINT)
        if checkpoint is None:
            skipped_no_checkpoint += 1
        else:
            home_r6, away_r6 = checkpoint
            margin = abs(home_r6 - away_r6)
            if margin == 0:
                skipped_tied_at_checkpoint += 1
            else:
                leading_is_home = home_r6 > away_r6
                leading_team = home if leading_is_home else away
                bucket = _margin_bucket(margin)

                home_form = recent.get(home, [])
                away_form = recent.get(away, [])
                if len(home_form) < _MIN_RECENT_FORM_GAMES or len(away_form) < _MIN_RECENT_FORM_GAMES:
                    skipped_no_recent_form_history += 1
                else:
                    home_wr = sum(home_form[-_RECENT_FORM_WINDOW:]) / len(home_form[-_RECENT_FORM_WINDOW:])
                    away_wr = sum(away_form[-_RECENT_FORM_WINDOW:]) / len(away_form[-_RECENT_FORM_WINDOW:])
                    favorite_by_form = home if home_wr > away_wr else (away if away_wr > home_wr else None)

                    if favorite_by_form is None or favorite_by_form != leading_team:
                        skipped_lead_not_by_favorite += 1
                    elif margin < _MIN_LEAD_MARGIN:
                        skipped_margin_below_threshold += 1
                    else:
                        rec = bucket_record.get(bucket)
                        n = rec[1] if rec else 0
                        if n < _MIN_BUCKET_SAMPLES:
                            skipped_no_bucket_samples += 1
                        else:
                            price = rec[0] / n  # P(equipo que iba arriba por este margen termina ganando), point-in-time
                            hit = (leading_team == home) == home_won
                            cases.append({
                                "date": g["date"], "favorite_team": leading_team,
                                "margin": margin, "margin_bucket": bucket,
                                "favorite_recent_winrate": round(home_wr if leading_is_home else away_wr, 4),
                                "underdog_recent_winrate": round(away_wr if leading_is_home else home_wr, 4),
                                "price": round(price, 4), "bucket_samples_used": n, "hit": hit,
                            })

                # actualizar la tabla de frecuencias DESPUÉS de usarla -- point-in-time correcto
                rec = bucket_record.setdefault(bucket, [0, 0])
                rec[1] += 1
                if (leading_team == home) == home_won:
                    rec[0] += 1

        # actualizar forma reciente DESPUÉS de usarla -- point-in-time correcto
        recent.setdefault(home, []).append(bool(home_won))
        recent.setdefault(away, []).append(not home_won)

    return cases, {
        "skipped_no_checkpoint": skipped_no_checkpoint,
        "skipped_tied_at_checkpoint": skipped_tied_at_checkpoint,
        "skipped_no_recent_form_history": skipped_no_recent_form_history,
        "skipped_lead_not_by_favorite": skipped_lead_not_by_favorite,
        "skipped_margin_below_threshold": skipped_margin_below_threshold,
        "skipped_no_bucket_samples": skipped_no_bucket_samples,
    }


def _entry_fee_frac(price: float) -> float:
    fee_per_contract = leg_fee_fraction(price) or 0.0
    return fee_per_contract / price if price > 0 else 0.0


def _net_hold_pct(price: float, hit: bool) -> float:
    raw = (1 - price) / price if hit else -1.0
    return raw - _entry_fee_frac(price)


def _measure_trading_days_per_year(cases: list[dict[str, Any]], default: float = 178.0) -> float:
    dates = sorted({c["date"] for c in cases})
    if len(dates) < 2:
        return default
    from datetime import date as _date
    first, last = _date.fromisoformat(dates[0]), _date.fromisoformat(dates[-1])
    years = (last - first).days / 365.25
    return len(dates) / years if years > 0 else default


def _simulate_entry_at_checkpoint(
    cases: list[dict[str, Any]], initial_capital: float, stake_pct: float = 0.01,
    max_simultaneous_pct: float = 0.5, max_stake_usd: float | None = 60_000.0,
) -> dict[str, Any]:
    """Sin exposición pre-partido: cada caso se entra recién al
    checkpoint (equivalente a la variante 2 de tenis), precio real
    calculado por la tabla de frecuencias point-in-time (`price` de
    cada caso), fee de Kalshi solo en la entrada, mismo cupo diario
    50% y priorización que el resto de esta sesión."""
    by_date: dict[str, list[dict[str, Any]]] = {}
    for c in cases:
        by_date.setdefault(c["date"], []).append(c)

    capital = initial_capital
    busted = False
    trade_returns: list[float] = []
    resolve_hits = resolve_n = 0
    equity_curve: list[float] = [initial_capital]
    skipped_cap = 0

    for date in sorted(by_date.keys()):
        if busted:
            equity_curve.append(capital)
            continue
        cap_start = capital
        max_exposure = cap_start * max_simultaneous_pct
        running_exposure = 0.0
        day_pnl = 0.0

        for c in by_date[date]:
            stake = cap_start * stake_pct
            if max_stake_usd is not None:
                stake = min(stake, max_stake_usd)
            if running_exposure + stake > max_exposure:
                skipped_cap += 1
                continue
            running_exposure += stake
            pct = _net_hold_pct(c["price"], c["hit"])
            day_pnl += stake * pct
            trade_returns.append(pct)
            resolve_n += 1
            resolve_hits += 1 if c["hit"] else 0

        capital += day_pnl
        if capital <= 0:
            capital = 0.0
            busted = True
        equity_curve.append(capital)

    trading_days_per_year = _measure_trading_days_per_year(cases)
    max_dd = risk_metrics.max_drawdown_pct(equity_curve)
    cagr = risk_metrics.cagr_pct(equity_curve, initial_capital, trading_days_per_year)

    return {
        "n_cases": len(cases), "n_trades": resolve_n, "skipped_daily_cap": skipped_cap,
        "hit_rate": round(resolve_hits / resolve_n, 4) if resolve_n else None,
        "final_capital": round(capital, 2), "busted": busted,
        "trading_days_per_year_measured": round(trading_days_per_year, 2),
        "max_drawdown_pct": max_dd, "cagr_pct": cagr,
        "annualized_sharpe": risk_metrics.annualized_sharpe(equity_curve, initial_capital, trading_days_per_year),
        "annualized_sortino": risk_metrics.annualized_sortino(equity_curve, initial_capital, trading_days_per_year),
        "calmar_ratio": risk_metrics.calmar_ratio(cagr, max_dd),
        "cvar_95_pct": risk_metrics.cvar_95_pct(trade_returns),
    }


def run_backtest(initial_capital: float = 50.0, max_stake_usd: float | None = 60_000.0) -> dict[str, Any]:
    games = collect_games_with_innings_from_db()
    cases, meta = _build_recent_form_lead_tracker(games)

    # priorización dentro del cupo diario -- margen más grande primero
    # (mayor confianza), luego mayor brecha de forma reciente.
    prioritized = sorted(
        cases, key=lambda c: (c["date"], -c["margin"], -(c["favorite_recent_winrate"] - c["underdog_recent_winrate"])),
    )
    result = _simulate_entry_at_checkpoint(prioritized, initial_capital, max_stake_usd=max_stake_usd)

    avg_price = round(sum(c["price"] for c in cases) / len(cases), 4) if cases else None
    avg_hit = round(sum(1 for c in cases if c["hit"]) / len(cases), 4) if cases else None

    return {
        "min_lead_margin_used": _MIN_LEAD_MARGIN, "checkpoint_inning": _INNING_6_CHECKPOINT,
        "recent_form_window": _RECENT_FORM_WINDOW, "min_recent_form_games": _MIN_RECENT_FORM_GAMES,
        "min_bucket_samples": _MIN_BUCKET_SAMPLES,
        "n_qualifying_cases": len(cases), **meta,
        "avg_model_price": avg_price, "avg_actual_hit_rate": avg_hit,
        "result": result,
        "caveat": (
            "Precio = tabla de frecuencias REAL point-in-time (P(equipo "
            "que va arriba por X carreras en la 6ta entrada termina "
            "ganando), sobre TODO el histórico real de checkpoints "
            "anteriores, no solo los casos que califican como favorito "
            "de forma reciente -- evita sesgar la calibración con la "
            "misma muestra que se está evaluando). Sin exposición "
            "pre-partido (entra recién al checkpoint, igual que la "
            "variante 2 de tenis)."
        ),
    }


if __name__ == "__main__":
    print(json.dumps(run_backtest(), indent=2, ensure_ascii=False))
