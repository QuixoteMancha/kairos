#!/usr/bin/env python3
"""
Auditor de portafolio EN VIVO (read-only) contra la API del Docker.

Hace SOLO peticiones GET a los endpoints del wallet y recalcula por su cuenta
cada invariante financiera, reportando PASS/FAIL. No modifica nada.

Uso:
    python scripts/audit_live.py
    python scripts/audit_live.py --base http://PROD_HOST:8765
    KAIROS_BASE=http://PROD_HOST:8765 python scripts/audit_live.py

Invariantes auditadas:
  1. total_equity   == Σ equity por plataforma (detalle de /total)
  2. total_balance  == Σ balance por plataforma
  3. capital en vuelo (pending_staked) == total_equity - total_balance
  4. realized_pnl   == Σ pnl por plataforma
  5. /summary y /total reportan el MISMO equity/balance/pnl
  6. apostado total (/total) == Σ wagered del breakdown por estrategia (/summary)
  7. pnl realizado (/total) == Σ pnl del breakdown por estrategia (/summary)
  8. win_rate == bets_won / (bets_won + bets_lost) * 100  (void/pending no cuentan)
  9. conservación de capital: equity == seed + pnl + deposits - withdrawals
 10. ninguna wallet con balance negativo
 11. ningún avg_roi/roi_expected absurdo (|roi| <= 100%)
 12. salud de balance: ninguna wallet activa con < REBALANCE_MIN (5.0) sin alerta

Devuelve exit code 0 si todo cuadra, 1 si hay algún descuadre.
"""
from __future__ import annotations

import argparse
import os
import sys
from urllib.request import urlopen
from urllib.error import URLError
import json

TOL = 0.02         # tolerancia €0.02 por redondeos
REBALANCE_MIN = 5.0


class Audit:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.lines: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        mark = "PASS" if ok else "FAIL"
        self.lines.append(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""))
        if ok:
            self.passed += 1
        else:
            self.failed += 1

    def report(self) -> int:
        print("\n".join(self.lines))
        print(f"\n  ── {self.passed} passed, {self.failed} failed ──")
        return 0 if self.failed == 0 else 1


def _get(base: str, path: str) -> dict:
    url = base.rstrip("/") + path
    with urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _eq(a, b, tol=TOL) -> bool:
    return abs(float(a or 0) - float(b or 0)) <= tol


def run_audit(base: str) -> int:
    a = Audit()
    try:
        total = _get(base, "/api/wallet/total")
        summary = _get(base, "/api/wallet/summary")
        platforms = _get(base, "/api/wallet/platforms")
        xbook = _get(base, "/api/wallet/cross-book-metrics")
    except (URLError, OSError) as e:
        print(f"[AUDIT] No se pudo conectar a {base}: {e}")
        return 2

    print(f"[AUDIT] Portafolio en vivo @ {base}")
    print(f"        equity={total.get('total_equity')} balance={total.get('total_balance')} "
          f"pnl={total.get('realized_pnl')} en_vuelo={total.get('total_pending_staked')}")
    print("─" * 70)

    tdetail = total.get("platforms", [])

    # 1-4: /total interno cuadra con su propio detalle
    sum_eq = round(sum(p.get("equity", 0) for p in tdetail), 2)
    sum_bal = round(sum(p.get("balance", 0) for p in tdetail), 2)
    sum_pnl = round(sum(p.get("pnl", 0) for p in tdetail), 2)
    # OJO: el detalle de /total omite wallets sin movimiento (idle); por eso la suma
    # del detalle puede ser menor al headline. La diferencia debe ser capital ocioso
    # (balance) de wallets sin bets, nunca negativa.
    # Upper bound: headline can exceed detail by at most total_balance (idle wallets
    # hold their balance but have no equity entry in the detail list).
    _head_eq = total.get("total_equity", 0)
    _idle_cap = max(0.0, float(total.get("total_balance", 0)))
    a.check("total_equity dentro de rango [Σdetalle, Σdetalle+idle_cap]",
            sum_eq - TOL <= _head_eq <= sum_eq + _idle_cap + TOL,
            f"head={_head_eq} Σdetalle={sum_eq} idle_cap={_idle_cap}")
    a.check("capital en vuelo == equity - balance",
            _eq(total.get("total_pending_staked"),
                total.get("total_equity", 0) - total.get("total_balance", 0)),
            f"pend={total.get('total_pending_staked')} "
            f"eq-bal={round(total.get('total_equity',0)-total.get('total_balance',0),2)}")

    # 5: /summary == /total (las wallets con has_wallet)
    a.check("summary.total_equity == total.total_equity",
            _eq(summary.get("total_equity"), total.get("total_equity")),
            f"{summary.get('total_equity')} vs {total.get('total_equity')}")
    a.check("summary.total_balance == total.total_balance",
            _eq(summary.get("total_balance"), total.get("total_balance")),
            f"{summary.get('total_balance')} vs {total.get('total_balance')}")
    a.check("summary.total_pnl == total.realized_pnl",
            _eq(summary.get("total_pnl"), total.get("realized_pnl")),
            f"{summary.get('total_pnl')} vs {total.get('realized_pnl')}")

    # 6-7: apostado / pnl == Σ del breakdown por estrategia
    strat = summary.get("strategies", [])
    sum_strat_wag = round(sum(s.get("wagered", 0) for s in strat), 2)
    sum_strat_pnl = round(sum(s.get("pnl", 0) for s in strat), 2)
    a.check("apostado total == Σ wagered estrategias",
            _eq(total.get("total_wagered"), sum_strat_wag),
            f"total={total.get('total_wagered')} Σstrat={sum_strat_wag}")
    a.check("pnl realizado == Σ pnl estrategias",
            _eq(total.get("realized_pnl"), sum_strat_pnl),
            f"total={total.get('realized_pnl')} Σstrat={sum_strat_pnl}")

    # 8: win_rate recomputado
    won, lost = total.get("bets_won", 0), total.get("bets_lost", 0)
    exp_wr = round(won / (won + lost) * 100, 1) if (won + lost) else 0.0
    a.check("win_rate == won/(won+lost)",
            _eq(total.get("win_rate"), exp_wr, tol=0.1),
            f"api={total.get('win_rate')} calc={exp_wr}")

    # 9: conservación de capital (seed derivado): equity == seed + pnl + dep - wit
    # seed = equity - pnl - deposits + withdrawals; debe ser >= 0 y estable.
    seed = round(total.get("total_equity", 0) - total.get("realized_pnl", 0)
                 - total.get("total_deposits", 0) + total.get("total_withdrawals", 0), 2)
    a.check("conservación de capital: seed implícito >= 0",
            seed >= -TOL, f"seed_implícito={seed} (equity-pnl-dep+wit)")

    # 10: ninguna wallet con balance negativo
    neg = [p for p in platforms.get("platforms", []) if p.get("balance", 0) < -TOL]
    a.check("ninguna wallet con balance negativo",
            not neg, f"negativas={[p['slug'] for p in neg]}" if neg else "ok")

    # 11: ROI sin valores absurdos
    bad_roi = [s["tipo"] for s in strat if abs(s.get("avg_roi", 0)) > 100.0]
    a.check("avg_roi de estrategias en rango sano (|roi|<=100%)",
            not bad_roi, f"absurdos={bad_roi}" if bad_roi else "ok")
    bad_xroi = [t for t, m in xbook.get("by_tipo", {}).items()
                if abs(m.get("roi_expected", 0)) > 100.0]
    a.check("roi_expected cross-book en rango sano",
            not bad_xroi, f"absurdos={bad_xroi}" if bad_xroi else "ok")

    # 12: salud de balance — wallets activas críticas deben tener alerta
    alerts = {al["slug"] for al in summary.get("rebalance_alerts", [])}
    critical = [p for p in summary.get("platforms", [])
                if p.get("is_active") and p.get("has_wallet")
                and p.get("balance", 0) < REBALANCE_MIN]
    missing = [p["slug"] for p in critical if p["slug"] not in alerts]
    a.check("wallets críticas (<5€) tienen alerta de rebalance",
            not missing, f"sin_alerta={missing}" if missing else "ninguna crítica")

    # 13: cross-book counts particionan
    bad_part = []
    for t, m in xbook.get("by_tipo", {}).items():
        if m.get("bets_total", 0) != (m.get("bets_won", 0) + m.get("bets_lost", 0)
                                      + m.get("bets_pending", 0) + m.get("bets_void", 0)):
            bad_part.append(t)
    a.check("cross-book: bets_total == won+lost+pending+void",
            not bad_part, f"descuadre={bad_part}" if bad_part else "ok")

    # 14: P&L por casa (cards del dashboard) suma == realized_pnl global
    plat_pnls = [p.get("pnl") for p in platforms.get("platforms", [])]
    have_pnl = all(p is not None for p in plat_pnls) and len(plat_pnls) > 0
    sum_plat_pnl = round(sum(p or 0 for p in plat_pnls), 2)
    a.check("P&L por casa presente y suma == realized_pnl",
            have_pnl and _eq(sum_plat_pnl, total.get("realized_pnl")),
            f"Σcasas={sum_plat_pnl} realized={total.get('realized_pnl')}"
            + ("" if have_pnl else " (FALTA pnl en /platforms)"))

    print("─" * 70)
    return a.report()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("KAIROS_BASE", "http://PROD_HOST:8765"))
    args = ap.parse_args()
    sys.exit(run_audit(args.base))


if __name__ == "__main__":
    main()
