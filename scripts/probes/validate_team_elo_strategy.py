#!/usr/bin/env python3
"""
validate_team_elo_strategy.py — ¿el modelo ELO→Dixon-Coles tiene señal real?

Verifica empíricamente si se pueden armar estrategias sport-specific en base a
correlaciones estadísticas, usando datos históricos de football-data.co.uk
(resultados FT + odds de CIERRE point-in-time de Pinnacle/Bet365).

Para cada partido:
  1. Resuelve ratings ClubElo de local/visita (TeamRating, ya poblado).
  2. Calcula prob del modelo (Dixon-Coles ajustado por ELO).
  3. Compara contra la prob implícita sin-vig del cierre.
  4. Mide: calibración, correlación edge↔profit, ROI de la estrategia value-bet.

NOTA: usa el ELO ACTUAL (ClubElo no expone snapshots por fecha gratis), así que
es una aproximación — úsese sólo con temporadas recientes. Es un sanity-check de
señal, no un backtest point-in-time exacto.

Uso:
    python scripts/probes/validate_team_elo_strategy.py [--seasons 2425,2324] [--edge 0.04]
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.core.sports.football import MatchProbs  # noqa: E402
from src.database.database import init_db  # noqa: E402

_BASE = "https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
# Ligas top con cobertura ClubElo fuerte
_DIVS = ["E0", "SP1", "I1", "D1", "F1", "E1", "SP2"]
_HEADERS = {"User-Agent": "kairos/1.0 (+strategy-validation)"}


def _devig3(oh: float, od: float, oa: float) -> tuple[float, float, float]:
    ih, id_, ia = 1 / oh, 1 / od, 1 / oa
    s = ih + id_ + ia
    return ih / s, id_ / s, ia / s


def _get_odds(row: dict) -> tuple[float, float, float] | None:
    """Closing odds: Pinnacle (PSCH/D/A) → Bet365 close (B365C*) → B365 abiertas."""
    for h, d, a in (("PSCH", "PSCD", "PSCA"), ("B365CH", "B365CD", "B365CA"),
                    ("B365H", "B365D", "B365A")):
        try:
            oh, od, oa = float(row[h]), float(row[d]), float(row[a])
            if oh > 1 and od > 1 and oa > 1:
                return oh, od, oa
        except (KeyError, ValueError, TypeError):
            continue
    return None


def _fetch_div(season: str, div: str) -> list[dict]:
    url = _BASE.format(season=season, div=div)
    try:
        r = requests.get(url, headers=_HEADERS, timeout=30)
        if r.status_code != 200:
            return []
        return list(csv.DictReader(io.StringIO(r.text)))
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2425,2324")
    ap.add_argument("--edge", type=float, default=0.04, help="umbral de value-bet")
    args = ap.parse_args()

    init_db()
    from src.core.orchestration.scanning import _team_elo_lookup

    seasons = [s.strip() for s in args.seasons.split(",") if s.strip()]
    matches = []
    for season in seasons:
        for div in _DIVS:
            for row in _fetch_div(season, div):
                ht, at, ftr = row.get("HomeTeam"), row.get("AwayTeam"), row.get("FTR")
                if not ht or not at or ftr not in ("H", "D", "A"):
                    continue
                odds = _get_odds(row)
                if not odds:
                    continue
                matches.append((ht, at, ftr, odds))

    print(f"Partidos con resultado+odds: {len(matches)}")
    if not matches:
        print("Sin datos. ¿Conexión a football-data.co.uk?")
        return 1

    # ── Métricas ──
    n_modeled = 0
    cal_bins = {i: [0, 0] for i in range(10)}   # bin prob → [aciertos, total] (home win)
    edge_profit: list[tuple[float, float]] = []  # (edge, profit_unitario) sobre value bets
    bet_n = bet_win = 0
    bet_pnl = 0.0
    sides = {"H": 0, "D": 0, "A": 0}

    for ht, at, ftr, (oh, od, oa) in matches:
        h_elo, a_elo = _team_elo_lookup(ht, at, "football")
        if h_elo is None or a_elo is None:
            continue
        n_modeled += 1
        mp = MatchProbs.from_teams(ht, at, home_elo=h_elo, away_elo=a_elo)
        m = {"H": mp.p_home, "D": mp.p_draw, "A": mp.p_away}
        imp = dict(zip("HDA", _devig3(oh, od, oa)))
        odd = {"H": oh, "D": od, "A": oa}

        # Calibración sobre prob de local
        b = min(9, int(mp.p_home * 10))
        cal_bins[b][1] += 1
        if ftr == "H":
            cal_bins[b][0] += 1

        # Estrategia value-bet: apostar el lado con mayor model_edge si supera umbral
        best_side, best_edge = None, args.edge
        for s in "HDA":
            e = m[s] - imp[s]
            if e > best_edge:
                best_side, best_edge = s, e
        if best_side:
            bet_n += 1
            sides[best_side] += 1
            won = (ftr == best_side)
            profit = (odd[best_side] - 1.0) if won else -1.0
            bet_pnl += profit
            bet_win += int(won)
            edge_profit.append((best_edge, profit))

    print(f"Partidos modelados (ambos equipos en ClubElo): {n_modeled}")
    print()

    # Calibración
    print("── Calibración prob_home (modelo) vs win-rate real ──")
    print(f"{'bin':>8} {'modelo':>8} {'real':>8} {'n':>6}")
    for i in range(10):
        wins, tot = cal_bins[i]
        if tot < 10:
            continue
        print(f"{i/10:.1f}-{(i+1)/10:.1f} {(i+0.5)/10:>8.2f} {wins/tot:>8.2f} {tot:>6}")
    print()

    # Correlación edge ↔ profit (Pearson)
    if len(edge_profit) >= 20:
        import statistics as st
        es = [e for e, _ in edge_profit]
        ps = [p for _, p in edge_profit]
        me, mp_ = st.mean(es), st.mean(ps)
        cov = sum((e - me) * (p - mp_) for e, p in edge_profit) / len(edge_profit)
        sde = st.pstdev(es) or 1e-9
        sdp = st.pstdev(ps) or 1e-9
        corr = cov / (sde * sdp)
        print(f"Correlación edge↔profit (Pearson): {corr:+.3f}")

    # ROI de la estrategia
    print()
    print(f"── Estrategia value-bet (edge > {args.edge:.0%}) ──")
    print(f"Apuestas: {bet_n}  |  sides H/D/A: {sides['H']}/{sides['D']}/{sides['A']}")
    if bet_n:
        print(f"Hit-rate: {bet_win/bet_n:.1%}")
        print(f"PnL: {bet_pnl:+.2f} u  |  ROI: {bet_pnl/bet_n:+.1%}")
        verdict = "SEÑAL POSITIVA ✅" if bet_pnl > 0 else "sin edge (ruido) ❌"
        print(f"Veredicto: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
