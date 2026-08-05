"""
Reporte de consola para el sistema de paper trading de LimitArb (ver
strategies/paper_trading.py, database/accounting/{papertrade,paperaccount}.py).

IMPORTANTE: correr siempre como módulo (-m), no como ruta de script --
`python3 scripts/x.py` deja `/app/scripts` en sys.path en vez de `/app`,
y el import de `database`/`strategies` (paquetes top-level) falla (mismo
bug ya encontrado y corregido en docker-compose.yml para los workers,
ver memoria kairos-4-workers-deployed-prod-tree-corruption-found-fixed).

Uso local (contra la DB de dev, desde la raíz del repo):
    python3 -m scripts.reports.paper_trading_stats

Uso en producción (contra la DB real, dentro de cualquiera de los
contenedores kairos_* -- todos comparten el mismo volumen de contabilidad):
    docker exec kairos_limitarb_monitor_worker python3 -m scripts.reports.paper_trading_stats

Solo lectura -- no escribe nada, no toca dinero real (no existe en este
proyecto), no requiere red.
"""

import sys
from collections import Counter

from database.db import init_db, db
from database.accounting.paperaccount import PaperAccount
from database.accounting.papertrade import PaperTrade


def _fmt_usd(x):
    return f"${x:,.2f}" if x is not None else "N/A"


def _fmt_pct(x):
    return f"{x*100:+.2f}%" if x is not None else "N/A"


def compute_report() -> dict:
    """Hace todo el cálculo del reporte de paper trading y devuelve un
    dict puro (sin imprimir nada) -- reusado tanto por main() como por
    api/server.py (GET /api/stats/paper-trading). Mismas cifras que se
    imprimen hoy, ninguna nueva."""
    init_db()
    with db.connection_context():
        account, _ = PaperAccount.get_or_create(id=1, defaults={'bankroll_usd': 200.0})
        all_trades = list(PaperTrade.select())

    open_trades = [t for t in all_trades if t.status == 'open']
    closed_trades = [t for t in all_trades if t.status == 'closed']
    # Clasificables = tienen realized_pnl_usd real (no None, no market_gone_unresolved
    # que se cierra en breakeven asumido, no un resultado medido).
    classifiable = [t for t in closed_trades if t.realized_pnl_usd is not None
                     and t.close_reason != 'market_gone_unresolved']

    committed_capital = sum(t.position_size_usd for t in open_trades)
    available_equity = account.bankroll_usd - committed_capital

    total_realized_pnl = sum(t.realized_pnl_usd for t in closed_trades if t.realized_pnl_usd is not None)
    starting_bankroll = 200.0
    net_return_pct = (account.bankroll_usd - starting_bankroll) / starting_bankroll if starting_bankroll else None

    if classifiable:
        wins = [t for t in classifiable if t.realized_pnl_usd > 0]
        losses = [t for t in classifiable if t.realized_pnl_usd <= 0]
        win_rate = len(wins) / len(classifiable)
        avg_win = sum(t.realized_pnl_usd for t in wins) / len(wins) if wins else None
        avg_loss = sum(t.realized_pnl_usd for t in losses) / len(losses) if losses else None
        avg_pnl = total_realized_pnl / len(classifiable) if classifiable else None
        classification = {
            'n_classifiable': len(classifiable),
            'win_rate': win_rate,
            'n_wins': len(wins),
            'n_losses': len(losses),
            'avg_pnl_usd': avg_pnl,
            'avg_win_usd': avg_win,
            'avg_loss_usd': avg_loss,
            'total_realized_pnl_usd': total_realized_pnl,
        }
    else:
        classification = None

    close_reason_breakdown = []
    reason_counts = Counter(t.close_reason for t in closed_trades)
    for reason, count in sorted(reason_counts.items(), key=lambda kv: -kv[1]):
        subset = [t for t in closed_trades if t.close_reason == reason and t.realized_pnl_usd is not None]
        avg = sum(t.realized_pnl_usd for t in subset) / len(subset) if subset else None
        close_reason_breakdown.append({'close_reason': reason, 'n': count, 'avg_pnl_usd': avg})

    case_breakdown = []
    for case in ('favorite_overvalued', 'favorite_undervalued'):
        subset = [t for t in classifiable if t.case == case]
        if not subset:
            case_breakdown.append({'case': case, 'n': 0, 'win_rate': None, 'avg_pnl_usd': None})
            continue
        wr = sum(1 for t in subset if t.realized_pnl_usd > 0) / len(subset)
        avg = sum(t.realized_pnl_usd for t in subset) / len(subset)
        case_breakdown.append({'case': case, 'n': len(subset), 'win_rate': wr, 'avg_pnl_usd': avg})

    recent = sorted(all_trades, key=lambda t: t.opened_at, reverse=True)[:10]
    recent_trades = []
    for t in recent:
        status_str = t.status if t.status == 'open' else f"closed:{t.close_reason}"
        recent_trades.append({
            'id': t.id,
            'opened_at': t.opened_at.isoformat(),
            'market_id': t.market_id,
            'case': t.case,
            'position_size_usd': t.position_size_usd,
            'kelly_fraction_used': t.kelly_fraction_used,
            'p_success_proxy_at_open': t.p_success_proxy_at_open,
            'status': status_str,
            'realized_pnl_usd': t.realized_pnl_usd,
        })

    return {
        'bankroll_usd': account.bankroll_usd,
        'starting_bankroll_usd': starting_bankroll,
        'net_return_pct': net_return_pct,
        'committed_capital_usd': committed_capital,
        'n_open_positions': len(open_trades),
        'available_equity_usd': available_equity,
        'kelly_calibration_factor': account.kelly_calibration_factor,
        'last_recalibrated_at': account.last_recalibrated_at.isoformat() if account.last_recalibrated_at else None,
        'n_total_trades': len(all_trades),
        'n_open_trades': len(open_trades),
        'n_closed_trades': len(closed_trades),
        'classification': classification,
        'close_reason_breakdown': close_reason_breakdown,
        'case_breakdown': case_breakdown,
        'recent_trades': recent_trades,
    }


def main():
    r = compute_report()

    print("=" * 72)
    print("KAIROS -- PAPER TRADING REPORT (100% dinero simulado, nunca real)")
    print("=" * 72)
    print()
    print(f"Bankroll actual:        {_fmt_usd(r['bankroll_usd'])}  (inicial: {_fmt_usd(r['starting_bankroll_usd'])}, "
          f"retorno neto: {_fmt_pct(r['net_return_pct'])})")
    print(f"Capital comprometido:   {_fmt_usd(r['committed_capital_usd'])}  ({r['n_open_positions']} posiciones abiertas)")
    print(f"Equity disponible:      {_fmt_usd(r['available_equity_usd'])}")
    print(f"Kelly calibration_factor: {r['kelly_calibration_factor']:.4f}"
          f"  (última recalibración: {r['last_recalibrated_at'] or 'nunca'})")
    print()

    print("-" * 72)
    print(f"TRADES: {r['n_total_trades']} totales | {r['n_open_trades']} abiertas | {r['n_closed_trades']} cerradas")
    print("-" * 72)

    c = r['classification']
    if c:
        print(f"Clasificables (excl. market_gone_unresolved/None): {c['n_classifiable']}")
        print(f"  Win rate real:       {c['win_rate']*100:.1f}%  ({c['n_wins']}W / {c['n_losses']}L)")
        print(f"  PnL promedio/trade:  {_fmt_usd(c['avg_pnl_usd'])}")
        print(f"  Ganancia promedio:   {_fmt_usd(c['avg_win_usd'])}")
        print(f"  Pérdida promedio:    {_fmt_usd(c['avg_loss_usd'])}")
        print(f"  PnL total realizado: {_fmt_usd(c['total_realized_pnl_usd'])}")
    else:
        print("Todavía no hay trades cerradas clasificables -- sin estadísticas de win rate.")

    print()
    print("-" * 72)
    print("DESGLOSE POR MOTIVO DE CIERRE (close_reason)")
    print("-" * 72)
    for row in r['close_reason_breakdown']:
        print(f"  {row['close_reason'] or '(sin motivo)':<28} n={row['n']:<4} pnl_promedio={_fmt_usd(row['avg_pnl_usd'])}")

    print()
    print("-" * 72)
    print("DESGLOSE POR CASO (favorite_overvalued / favorite_undervalued)")
    print("-" * 72)
    for row in r['case_breakdown']:
        if row['n'] == 0:
            print(f"  {row['case']:<24} sin datos clasificables todavía")
            continue
        print(f"  {row['case']:<24} n={row['n']:<4} win_rate={row['win_rate']*100:.1f}%  pnl_promedio={_fmt_usd(row['avg_pnl_usd'])}")

    print()
    print("-" * 72)
    print(f"ÚLTIMAS 10 TRADES (más reciente primero)")
    print("-" * 72)
    recent = r['recent_trades']
    if not recent:
        print("  (sin trades todavía)")
    for t in recent:
        opened_dt = t['opened_at'].replace('T', ' ')[:19]
        print(f"  [{opened_dt}] {t['market_id']:<28} {t['case']:<22} "
              f"size={_fmt_usd(t['position_size_usd']):<10} kelly_f={t['kelly_fraction_used']:.4f} "
              f"p_proxy={t['p_success_proxy_at_open']:.3f} {t['status']:<26} pnl={_fmt_usd(t['realized_pnl_usd'])}")

    print()
    print("=" * 72)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error generando el reporte: {e}", file=sys.stderr)
        sys.exit(1)
