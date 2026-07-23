"""
Reporte de consola para el sistema de paper trading de LimitArb (ver
strategies/paper_trading.py, database/accounting/{papertrade,paperaccount}.py).

IMPORTANTE: correr siempre como módulo (-m), no como ruta de script --
`python3 scripts/x.py` deja `/app/scripts` en sys.path en vez de `/app`,
y el import de `database`/`strategies` (paquetes top-level) falla (mismo
bug ya encontrado y corregido en docker-compose.yml para los workers,
ver memoria kairos-4-workers-deployed-prod-tree-corruption-found-fixed).

Uso local (contra la DB de dev, desde la raíz del repo):
    python3 -m scripts.paper_trading_stats

Uso en producción (contra la DB real, dentro de cualquiera de los
contenedores kairos_* -- todos comparten el mismo volumen de contabilidad):
    docker exec kairos_limitarb_monitor_worker python3 -m scripts.paper_trading_stats

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


def main():
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

    print("=" * 72)
    print("KAIROS -- PAPER TRADING REPORT (100% dinero simulado, nunca real)")
    print("=" * 72)
    print()
    print(f"Bankroll actual:        {_fmt_usd(account.bankroll_usd)}  (inicial: {_fmt_usd(starting_bankroll)}, "
          f"retorno neto: {_fmt_pct(net_return_pct)})")
    print(f"Capital comprometido:   {_fmt_usd(committed_capital)}  ({len(open_trades)} posiciones abiertas)")
    print(f"Equity disponible:      {_fmt_usd(available_equity)}")
    print(f"Kelly calibration_factor: {account.kelly_calibration_factor:.4f}"
          f"  (última recalibración: {account.last_recalibrated_at.isoformat() if account.last_recalibrated_at else 'nunca'})")
    print()

    print("-" * 72)
    print(f"TRADES: {len(all_trades)} totales | {len(open_trades)} abiertas | {len(closed_trades)} cerradas")
    print("-" * 72)

    if classifiable:
        wins = [t for t in classifiable if t.realized_pnl_usd > 0]
        losses = [t for t in classifiable if t.realized_pnl_usd <= 0]
        win_rate = len(wins) / len(classifiable)
        avg_win = sum(t.realized_pnl_usd for t in wins) / len(wins) if wins else None
        avg_loss = sum(t.realized_pnl_usd for t in losses) / len(losses) if losses else None
        avg_pnl = total_realized_pnl / len(classifiable) if classifiable else None

        print(f"Clasificables (excl. market_gone_unresolved/None): {len(classifiable)}")
        print(f"  Win rate real:       {win_rate*100:.1f}%  ({len(wins)}W / {len(losses)}L)")
        print(f"  PnL promedio/trade:  {_fmt_usd(avg_pnl)}")
        print(f"  Ganancia promedio:   {_fmt_usd(avg_win)}")
        print(f"  Pérdida promedio:    {_fmt_usd(avg_loss)}")
        print(f"  PnL total realizado: {_fmt_usd(total_realized_pnl)}")
    else:
        print("Todavía no hay trades cerradas clasificables -- sin estadísticas de win rate.")

    print()
    print("-" * 72)
    print("DESGLOSE POR MOTIVO DE CIERRE (close_reason)")
    print("-" * 72)
    reason_counts = Counter(t.close_reason for t in closed_trades)
    for reason, count in sorted(reason_counts.items(), key=lambda kv: -kv[1]):
        subset = [t for t in closed_trades if t.close_reason == reason and t.realized_pnl_usd is not None]
        avg = sum(t.realized_pnl_usd for t in subset) / len(subset) if subset else None
        print(f"  {reason or '(sin motivo)':<28} n={count:<4} pnl_promedio={_fmt_usd(avg)}")

    print()
    print("-" * 72)
    print("DESGLOSE POR CASO (favorite_overvalued / favorite_undervalued)")
    print("-" * 72)
    for case in ('favorite_overvalued', 'favorite_undervalued'):
        subset = [t for t in classifiable if t.case == case]
        if not subset:
            print(f"  {case:<24} sin datos clasificables todavía")
            continue
        wr = sum(1 for t in subset if t.realized_pnl_usd > 0) / len(subset)
        avg = sum(t.realized_pnl_usd for t in subset) / len(subset)
        print(f"  {case:<24} n={len(subset):<4} win_rate={wr*100:.1f}%  pnl_promedio={_fmt_usd(avg)}")

    print()
    print("-" * 72)
    print(f"ÚLTIMAS 10 TRADES (más reciente primero)")
    print("-" * 72)
    recent = sorted(all_trades, key=lambda t: t.opened_at, reverse=True)[:10]
    if not recent:
        print("  (sin trades todavía)")
    for t in recent:
        status_str = t.status if t.status == 'open' else f"closed:{t.close_reason}"
        print(f"  [{t.opened_at.strftime('%Y-%m-%d %H:%M:%S')}] {t.market_id:<28} {t.case:<22} "
              f"size={_fmt_usd(t.position_size_usd):<10} kelly_f={t.kelly_fraction_used:.4f} "
              f"p_proxy={t.p_success_proxy_at_open:.3f} {status_str:<26} pnl={_fmt_usd(t.realized_pnl_usd)}")

    print()
    print("=" * 72)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error generando el reporte: {e}", file=sys.stderr)
        sys.exit(1)
