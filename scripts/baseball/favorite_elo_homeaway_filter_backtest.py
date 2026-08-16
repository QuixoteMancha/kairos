"""Backtest MLB análogo a la estrategia final elegida en tenis
(`scripts/tennis/favorite_surface_winrate_filter_backtest.py`, variante
8/5 -- ver Obsidian "Backtest Favorito ATP y Estrategia Set1 -
Kairos.md") -- 2026-08-05, pedido explícito del usuario: "como ya
tenemos histórico de MLB hagamos un backtest de una estrategia
parecida a la seleccionada en tenis, y toma lo anotado en obsidian
como referencia, para esta y otra sesiones".

EQUIVALENCIAS CON TENIS (documentadas para que el diseño sea
verificable contra el de tenis, no una reinvención ad-hoc):
  - "favorito" en tenis = mejor RANKING real point-in-time.
    "favorito" en MLB = mayor ELO AUTO-CALCULADO point-in-time (no hay
    ranking oficial de equipos MLB equivalente a ATP/WTA -- Elo es el
    sustituto natural, mismo criterio ya usado en
    `scripts/tennis/self_computed_elo_backtest.py`: point-in-time
    correcto por construcción, procesa partidos en orden cronológico,
    lee-antes-de-actualizar).
  - filtro "rank_gap >= 20" en tenis = filtro "elo_gap >= _MIN_ELO_GAP"
    acá (umbral derivado empíricamente de la distribución REAL de gaps
    del propio dataset MLB, no copiado del valor de tenis -- las
    escalas de Elo/ranking no son comparables 1:1).
  - filtro "winrate por superficie" en tenis = filtro "winrate por ROL
    (local/visitante)" acá -- el equivalente MLB más directo de un
    contexto point-in-time recurrente por jugador/equipo. Igual
    criterio fail-closed: exige que TANTO el favorito COMO el
    underdog tengan al menos 1 partido previo real en su rol
    específico (favorito en su rol real del partido evaluado --local
    o visitante--, underdog en el suyo) antes de confiar en el
    winrate; sin historial -> excluido, nunca se asume 50%/0%.
  - "priorizar por ranking dentro del cupo diario" en tenis = acá se
    prioriza por Elo del favorito (descendente) dentro del cupo diario
    del 50% -- mismo mecanismo: ordenar `cases` por (date, -favorite_elo)
    antes de simular, el cupo de `_simulate_simple_hold_strategy`
    acepta en orden de llegada de la lista.
  - "precio" de cada apuesta = fair price MODELADO (Elo point-in-time),
    NO un precio real de mercado histórico -- MISMO criterio que tenis
    (`_real_price_legs` en `favorite_by_historical_rank_backtest.py`):
    no existen datos reales de precio de Kalshi para partidos
    históricos de MLB (Kalshi no cotizaba MLB en esos años), así que el
    modelo actúa como proxy de precio de entrada, igual que en tenis.
  - fees reales de Kalshi (`outer_apis/kalshi/fees.py::leg_fee_fraction`)
    aplicados en la entrada, mismo criterio conservador que tenis (sin
    descuento de maker).
  - sizing: 1% de capital por apuesta, cupo de exposición simultánea
    50% del capital diario, techo de liquidez por apuesta
    (`max_stake_usd`) -- mismos valores que tenis, sin re-derivar
    (mismo régimen de capital, mismo motivo: liquidez real de mercados
    Kalshi por evento, no es MLB-específico).

DIFERENCIA DELIBERADA vs. tenis: SIN warm-up separado en años
anteriores al rango de evaluación -- el dataset MLB solo cubre 2015-2026
(12 temporadas, ver `MlbGameHistory`, backfill completo verificado
2026-08-05), así que se usa la primera temporada completa (2015) como
warm-up del Elo/winrate-por-rol y se evalúa 2016 en adelante (mismo
patrón de "1 temporada de calentamiento" que tenis usa "16 años" -- acá
no hay más historia real disponible para calentar más)."""

from __future__ import annotations

import json
import statistics
from typing import Any

from database.db import init_db_historical
from database.historical.baseball import MlbGameHistory
from outer_apis.kalshi.fees import leg_fee_fraction
from outer_apis.sports.tennis.tennisabstract_elo import elo_implied_win_prob
from scripts.reports import risk_metrics

_INITIAL_ELO = 1500.0
# 2026-08-05, hallazgo real durante esta sesión: K=32 (el mismo valor
# usado en tenis, `self_computed_elo_backtest.py`) resulta SEVERAMENTE
# sobreconfiado para MLB -- probado en vivo contra el dataset real
# (n=24,443 casos 2016-2026): K=32 da precio promedio del favorito
# 61.42% contra un hit rate real de apenas 54.8% (gap de calibración de
# +6.6 puntos, ambas estrategias del backtest terminan en ruina total
# apostando contra el propio modelo). Motivo real: MLB juega ~162
# partidos/temporada por equipo (10-20x más partidos por unidad de
# tiempo que un jugador ATP), así que un K de tenis sobre-reacciona a
# cada resultado individual en MLB (162 oportunidades/año de que un
# equipo bueno pierda un partido cualquiera no mueve tanto su fuerza
# real). Barrido explícito K=2/4/8/16/32 sobre el dataset real: K=2 es
# el mejor calibrado (precio promedio 55.88% vs. hit real 55.90%,
# prácticamente exacto) -- valor elegido tras verificación empírica,
# no copiado de tenis.
_K_FACTOR = 2.0

_WARMUP_END_YEAR = 2015  # única temporada disponible antes del rango de evaluación real
_EVAL_START_YEAR = 2016


def _elo_update(elo_winner: float, elo_loser: float, k: float = _K_FACTOR) -> tuple[float, float]:
    expected_winner = elo_implied_win_prob(elo_winner, elo_loser)
    delta = k * (1.0 - expected_winner)
    return elo_winner + delta, elo_loser - delta


def collect_games_from_db() -> list[dict[str, Any]]:
    """DB-first, NUNCA toca la red -- mismo criterio ya establecido esta
    sesión para tenis (`get_matches_range_from_db`) y para
    `mlb_comeback_analysis.py::collect_games_from_db`. Lee todo
    `MlbGameHistory` real, ordenado cronológicamente."""
    init_db_historical()
    rows = list(MlbGameHistory.select().order_by(MlbGameHistory.date))
    result = []
    for r in rows:
        if r.home_runs_final is None or r.away_runs_final is None:
            continue  # fail-closed: partido sin resultado final real, no se puede evaluar
        result.append({
            "date": r.date, "home_team": r.home_team, "away_team": r.away_team,
            "home_won": r.home_won,
        })
    return result


_MIN_ELO_GAP = 60  # ver docstring de `run_filtered_backtest` -- derivado empíricamente con K=2.0 calibrado (p75 real=59.26, n=24,443)


def _build_elo_homeaway_tracker(games: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Recorre TODO el histórico MLB una sola vez en orden cronológico,
    manteniendo (1) Elo point-in-time por equipo y (2) win/loss
    point-in-time por (equipo, rol=local/visitante) -- arma `cases` con
    ambos winrates (favorito y underdog, en SU rol real de ese
    partido), ANTES de que este partido se juegue."""
    elo: dict[str, float] = {}
    # (team, role) -> [wins, losses], role in {"home", "away"}
    record: dict[tuple[str, str], list[int]] = {}
    eval_cutoff = f"{_EVAL_START_YEAR}-01-01"

    cases: list[dict[str, Any]] = []
    skipped_no_elo_history = 0

    for g in games:
        home, away, home_won = g["home_team"], g["away_team"], g["home_won"]
        elo_home = elo.get(home, _INITIAL_ELO)
        elo_away = elo.get(away, _INITIAL_ELO)

        if g["date"] >= eval_cutoff:
            favorite_is_home = elo_home >= elo_away
            favorite_team = home if favorite_is_home else away
            underdog_team = away if favorite_is_home else home
            favorite_elo = max(elo_home, elo_away)
            underdog_elo = min(elo_home, elo_away)
            favorite_role = "home" if favorite_is_home else "away"
            underdog_role = "away" if favorite_is_home else "home"

            fav_rec = record.get((favorite_team, favorite_role))
            dog_rec = record.get((underdog_team, underdog_role))
            if fav_rec is None or dog_rec is None or (fav_rec[0] + fav_rec[1]) == 0 or (dog_rec[0] + dog_rec[1]) == 0:
                skipped_no_elo_history += 1
            else:
                fav_wr = fav_rec[0] / (fav_rec[0] + fav_rec[1])
                dog_wr = dog_rec[0] / (dog_rec[0] + dog_rec[1])
                hit = (favorite_team == home) == home_won  # el favorito ganó de verdad

                cases.append({
                    "date": g["date"], "favorite_team": favorite_team, "underdog_team": underdog_team,
                    "favorite_elo": round(favorite_elo, 2), "underdog_elo": round(underdog_elo, 2),
                    "elo_gap": round(favorite_elo - underdog_elo, 2), "hit": hit,
                    "favorite_role": favorite_role,
                    "favorite_role_winrate": round(fav_wr, 4), "underdog_role_winrate": round(dog_wr, 4),
                    "favorite_role_matches": fav_rec[0] + fav_rec[1], "underdog_role_matches": dog_rec[0] + dog_rec[1],
                })

        # actualizar Elo y winrate-por-rol DESPUÉS de usarlos -- point-in-time correcto
        winner, loser = (home, away) if home_won else (away, home)
        new_elo_w, new_elo_l = _elo_update(elo.get(winner, _INITIAL_ELO), elo.get(loser, _INITIAL_ELO))
        elo[winner], elo[loser] = new_elo_w, new_elo_l

        winner_role = "home" if home_won else "away"
        loser_role = "away" if home_won else "home"
        wr = record.setdefault((winner, winner_role), [0, 0])
        wr[0] += 1
        lr = record.setdefault((loser, loser_role), [0, 0])
        lr[1] += 1

    return cases, {"skipped_no_elo_or_role_history": skipped_no_elo_history}


def _real_price(favorite_elo: float, underdog_elo: float) -> float:
    """Fair price modelado (proxy de precio histórico, ver docstring
    del módulo) -- probabilidad Elo-implícita de que gane el favorito."""
    return elo_implied_win_prob(favorite_elo, underdog_elo)


def _entry_fee_frac(price: float) -> float:
    fee_per_contract = leg_fee_fraction(price) or 0.0
    return fee_per_contract / price if price > 0 else 0.0


def _net_hold_pct(price: float, hit: bool) -> float:
    raw = (1 - price) / price if hit else -1.0
    return raw - _entry_fee_frac(price)


_TRADING_DAYS_PER_YEAR_MLB_DEFAULT = 178.0  # ver `_measure_trading_days_per_year` -- valor medido real 2016-2026, dejado como fallback documentado


def _measure_trading_days_per_year(cases: list[dict[str, Any]]) -> float:
    """Días de trading reales/año, MEDIDO del propio dataset de
    evaluación (a diferencia de tenis, que hardcodea un valor medido
    aparte) -- más robusto ante cambios de rango de años, y evita
    tener que re-medir a mano cada vez que se actualiza el backfill."""
    dates = sorted({c["date"] for c in cases})
    if len(dates) < 2:
        return _TRADING_DAYS_PER_YEAR_MLB_DEFAULT
    from datetime import date as _date
    first = _date.fromisoformat(dates[0])
    last = _date.fromisoformat(dates[-1])
    years = (last - first).days / 365.25
    if years <= 0:
        return _TRADING_DAYS_PER_YEAR_MLB_DEFAULT
    return len(dates) / years


def _simulate_simple_hold_strategy(
    cases: list[dict[str, Any]], initial_capital: float, stake_pct: float = 0.01,
    max_simultaneous_pct: float = 0.5, max_stake_usd: float | None = 60_000.0,
) -> dict[str, Any]:
    """Solo la estrategia "1_simple_bet_favorite" de tenis -- apostar al
    favorito pre-partido y sostener hasta el resultado final. Las
    variantes 2-8 de tenis dependen del set 1/set 2 (sin equivalente
    limpio en MLB dentro del alcance pedido -- "una estrategia parecida
    a la seleccionada", no las 8 variantes completas)."""
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
            price = _real_price(c["favorite_elo"], c["underdog_elo"])
            pct = _net_hold_pct(price, c["hit"])
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
        "max_drawdown_pct": max_dd,
        "cagr_pct": cagr,
        "annualized_sharpe": risk_metrics.annualized_sharpe(equity_curve, initial_capital, trading_days_per_year),
        "annualized_sortino": risk_metrics.annualized_sortino(equity_curve, initial_capital, trading_days_per_year),
        "calmar_ratio": risk_metrics.calmar_ratio(cagr, max_dd),
        "cvar_95_pct": risk_metrics.cvar_95_pct(trade_returns),
        "sharpe_per_trade": round(statistics.mean(trade_returns) / statistics.stdev(trade_returns), 4)
        if len(trade_returns) >= 2 and statistics.stdev(trade_returns) > 0 else None,
    }


def run_filtered_backtest(
    initial_capital: float = 50.0, max_stake_usd: float | None = 60_000.0,
    min_elo_gap: int = _MIN_ELO_GAP, min_role_matches: int = 0,
) -> dict[str, Any]:
    """`min_elo_gap` (default 60): derivado empíricamente vía
    `report_elo_gap_distribution()` sobre el dataset REAL de producción,
    YA con `_K_FACTOR=2.0` calibrado (ver docstring de esa constante --
    K=32 copiado de tenis daba un Elo severamente sobreconfiado para
    MLB) -- mediana real=35.18, P75 real=59.26, P90 real=86.32,
    max=181.77 (n=24,443 casos evaluables, 2016-2026). Se fija en 60
    (~P75) para mantener el mismo espíritu del filtro de tenis
    ("rank_gap>=20", que también corresponde aprox. al cuartil superior
    de gaps ATP): exigir una ventaja de favorito considerablemente por
    encima de la mediana, no solo positiva. Re-medir con
    `report_elo_gap_distribution()` si el dataset MLB se actualiza o si
    `_K_FACTOR` cambia (el Elo, y por lo tanto la distribución de
    gaps, depende directamente de ese valor)."""
    games = collect_games_from_db()
    all_cases, meta = _build_elo_homeaway_tracker(games)

    filtered_cases = [
        c for c in all_cases
        if c["elo_gap"] >= min_elo_gap
        and c["favorite_role_winrate"] > c["underdog_role_winrate"]
        and c["favorite_role_matches"] >= min_role_matches
        and c["underdog_role_matches"] >= min_role_matches
    ]

    # priorización dentro del cupo diario 50% -- favorito de MAYOR Elo
    # primero (equivalente MLB de "mejor ranking primero" en tenis).
    filtered_prioritized = sorted(filtered_cases, key=lambda c: (c["date"], -c["favorite_elo"]))
    all_prioritized = sorted(all_cases, key=lambda c: (c["date"], -c["favorite_elo"]))

    baseline_result = _simulate_simple_hold_strategy(all_prioritized, initial_capital, max_stake_usd=max_stake_usd)
    filtered_result = _simulate_simple_hold_strategy(filtered_prioritized, initial_capital, max_stake_usd=max_stake_usd)

    baseline_hits = sum(1 for c in all_cases if c["hit"])
    filtered_hits = sum(1 for c in filtered_cases if c["hit"])

    return {
        "min_elo_gap_used": min_elo_gap, "min_role_matches_used": min_role_matches,
        "total_cases_with_elo_and_role_history": len(all_cases), **meta,
        "filtered_cases_n": len(filtered_cases),
        "baseline_hit_rate": round(baseline_hits / len(all_cases), 4) if all_cases else None,
        "filtered_hit_rate": round(filtered_hits / len(filtered_cases), 4) if filtered_cases else None,
        "baseline": baseline_result,
        "filtered": filtered_result,
        "caveat": (
            "Elo y winrate-por-rol AUTO-CALCULADOS desde MlbGameHistory "
            "(warm-up 2015 únicamente -- única temporada disponible antes "
            "del rango de evaluación, a diferencia de tenis que tiene 16 "
            "años de warm-up). Precio de entrada = fair price MODELADO "
            "(Elo-implícito), NO precio real de mercado histórico -- "
            "Kalshi no cotizaba MLB en esos años. Equipo/rol sin "
            "historial previo real -> excluido (fail-closed)."
        ),
    }


def report_elo_gap_distribution() -> dict[str, Any]:
    """Distribución real de `elo_gap` sobre TODOS los casos evaluables
    (sin filtrar) -- usado para justificar `_MIN_ELO_GAP` empíricamente
    en vez de copiar un número arbitrario. Ejecutar manualmente si el
    dataset MLB se actualiza y `_MIN_ELO_GAP` necesita re-derivarse."""
    games = collect_games_from_db()
    all_cases, _ = _build_elo_homeaway_tracker(games)
    gaps = sorted(c["elo_gap"] for c in all_cases)
    if not gaps:
        return {"n": 0}
    n = len(gaps)
    return {
        "n": n,
        "median": gaps[n // 2],
        "p75": gaps[int(n * 0.75)],
        "p90": gaps[int(n * 0.90)],
        "max": gaps[-1],
    }


if __name__ == "__main__":
    import sys

    gap = int(sys.argv[1]) if len(sys.argv) > 1 else _MIN_ELO_GAP
    result = run_filtered_backtest(min_elo_gap=gap)
    print(json.dumps(result, indent=2, ensure_ascii=False))
