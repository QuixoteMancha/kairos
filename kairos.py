#!/usr/bin/env python3
"""
KAIROS — Arbitrage Monitor & Virtual Wallet Simulator

FILOSOFÍA DE ARQUITECTURA (reestructuración en curso):

El **core / motor** (KairosEngine) debe funcionar de forma **automática y autónoma**:
  - Detección continua de oportunidades (scans, cross-book, multi-sport).
  - Auto-bets con rotación, jitter gaussiano, win-rate feedback por libro.
  - Live monitoring + hedging de stat bets.
  - Settlement automático, rebalance de wallets, historial, etc.
  - Todo esto vive en src/core/orchestration/ + core/math + core/sports/.

La **web app** (Flask + Vue) es **principalmente visualización**:
  - Muestra balance, equity, P&L, arbs detectados, historial de apuestas,
    partidos, etc.
  - Permite acciones manuales solo para depósitos y rebalanceos de dinero. Todo lo demás (scans, apuestas, settlement, live-hedge) es 100% autónomo.
  - No es el "cerebro", es la interfaz de observación y control ligero.

El sistema completo (motor + web) se inicia con un solo comando principal.
Otros comandos son utilitarios (simulación, scraping manual, inspección CLI).

En Docker (modelo actual y deseado):
  El contenedor arranca todo automáticamente:
      python kairos.py run          # o "dashboard" por compatibilidad
  El motor corre solo en background.
  La web sirve en :8765 para ver los datos.

Comandos principales:
  run / dashboard   → Arranca el motor autónomo completo + web server (recomendado)
  stop              → Detener

Comandos utilitarios (no son el "programa principal"):
  simulate          → Simulación con wallet virtual (testing/estrategia)
  scan              → Escaneo manual one-shot
  status / history / pending / resolve
  scrape / players  → Pipelines de recolección de datos (pueden ser automatizados)

Ejemplos:
  python kairos.py run
  python kairos.py run --interval 30 --auto-bet
  python kairos.py simulate --balance 5000 --auto 10
  python kairos.py scan
"""

import argparse
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.core.logging_config import configure_logging
configure_logging()

from src.core.math.simulation import SimulationEngine, VirtualWallet

# ============================================================================
# HELPERS DE PRESENTACIÓN
# ============================================================================

def _print_history(wallet: VirtualWallet, n: int):
    bets = wallet.bets[-n:]
    if not bets:
        print("  Sin historial de apuestas.")
        return

    icons = {'won': '✓', 'lost': '✗', 'pending': '⏳'}
    tipo_colors = {'inside': 'INSIDE', 'cross': 'CROSS ', 'stat': 'STAT  '}

    print(f"\n  {'─'*72}")
    print(f"  {'ID':8s}  {'Fecha':16s}  {'Partido':28s}  {'Tipo':6s}  {'ROI':>7}  {'Apostado':>10}  {'P&L':>8}")
    print(f"  {'─'*72}")
    for bet in reversed(bets):
        icon    = icons.get(bet.status, '?')
        ts      = bet.timestamp[:16].replace('T', ' ')
        tipo    = tipo_colors.get(bet.arb_tipo, bet.arb_tipo[:6])
        nombre  = bet.fixture_name[:28]
        pnl_str = f"{bet.pnl:+.2f}" if bet.status != 'pending' else "pending"
        print(
            f"  {icon} {bet.bet_id:7s}  {ts}  {nombre:<28}  {tipo}  "
            f"{bet.roi*100:>6.2f}%  ${bet.total_staked:>9.2f}  {pnl_str:>8}"
        )
    print(f"  {'─'*72}")
    resolved = [b for b in bets if b.status != 'pending']
    if resolved:
        total_pnl = sum(b.pnl for b in resolved)
        total_stk = sum(b.total_staked for b in bets)
        print(
            f"  {'':8s}  {'':16s}  {'TOTALES':28s}  {'':6s}  {'':>7}  "
            f"${total_stk:>9.2f}  {total_pnl:>+8.2f}"
        )
        print(f"  {'─'*72}")


def _print_pending(wallet: VirtualWallet):
    pending = wallet.pending_bets
    if not pending:
        print("  Sin apuestas pendientes.")
        return
    print(f"\n  Apuestas pendientes ({len(pending)}):")
    for bet in pending:
        print(f"  ⏳ #{bet.bet_id}  [{bet.arb_tipo}]  {bet.fixture_name}  ROI={bet.roi*100:.2f}%")
        print(f"         apostado=${bet.total_staked:.2f}  retorno_esperado=${bet.guaranteed_return:.2f}")


# ============================================================================
# CMD: SCAN
# ============================================================================

def cmd_scan(min_roi: float = -1.0):
    from src.core.math.arbitrage import scan_match_arb
    from src.scrapers.stake_scraper import StakeScraper

    print(f"\n  SCAN — cuotas en vivo de Stake\n  {'─'*58}")
    with StakeScraper() as s:
        fixtures = s.get_tennis_fixtures()
    print(f"  {len(fixtures)} fixtures | {sum(len(f.markets) for f in fixtures)} mercados\n")

    found_arbs = []
    for fx in fixtures:
        if not fx.markets:
            continue
        opps = scan_match_arb(fx.markets, min_roi=min_roi, verbose=False, include_stat=True)
        for o in opps:
            found_arbs.append((fx, o))

    if found_arbs:
        seen = set()
        unique = []
        for fx, o in sorted(found_arbs, key=lambda x: -x[1].roi):
            k = (fx.fixture_id, o.descripcion)
            if k not in seen:
                seen.add(k)
                unique.append((fx, o))
        print(f"  ✓ {len(unique)} arb(s) únicos  ROI >= {min_roi*100:.1f}%:\n")
        for fx, o in unique:
            tipo_label = {'inside': 'INSIDE', 'cross': 'CROSS ', 'stat': 'STAT  '}.get(o.tipo, o.tipo)
            print(f"  [{tipo_label}] ROI={o.roi*100:.2f}%  {fx.p1_name} vs {fx.p2_name}")
            print(f"         {o.descripcion}")
            for leg in o.legs:
                m = leg['market']
                side_str = 'P1/Over ' if leg['side'] == 'p1' else 'P2/Under'
                print(f"         {m.label():36s}  {side_str}  @{leg['odd']:.2f}")
            print()
    else:
        print(f"  ✗ Sin arbs activos con ROI >= {min_roi*100:.1f}%\n")

    print(f"\n  TOP 10 mercados con menor sobreround:\n  {'─'*58}")
    rows = []
    for fx in fixtures:
        for m in fx.markets:
            or_ = (1/m.odd_p1 + 1/m.odd_p2) - 1
            rows.append((fx, m, or_))
    rows.sort(key=lambda x: x[2])
    for fx, m, or_ in rows[:10]:
        sign = "+" if or_ > 0 else ""
        label = getattr(m, 'market_name', None) or m.tipo
        if m.linea is not None:
            label += f"({m.linea:+.1f})"
        print(
            f"  {sign}{or_*100:.3f}%  {fx.p1_name[:20]:<20} vs {fx.p2_name[:20]:<20}"
            f"  {label:<32}  P1@{m.odd_p1:.2f}  P2@{m.odd_p2:.2f}"
        )
    print()


# ============================================================================
# CMD: DASHBOARD
# ============================================================================

def cmd_dashboard(args):
    from src.web.dashboard import run_dashboard
    run_dashboard(
        host=args.host,
        port=args.port,
        scan_interval=args.interval,
        auto_bet=args.auto_bet,
        bet_pct=args.bet_pct,
        reload=args.reload,
    )


# ============================================================================
# CMD: SIMULATE
# ============================================================================

def cmd_simulate(args):
    if args.reset:
        wallet = VirtualWallet(initial_balance=args.balance)
        wallet.save()
        print(f"\n  ✓ Wallet reseteada con balance inicial ${args.balance:,.2f}")
    else:
        wallet = VirtualWallet.load_or_create(initial_balance=args.balance)
        print("\n  ✓ Wallet cargada (SQLite)")

    engine = SimulationEngine(
        wallet=wallet,
        max_bet_pct=args.bet_pct,
        min_roi=args.min_roi,
        include_stat=args.include_stat,
    )

    if args.auto > 0:
        engine.run_continuous(rounds=args.auto, delay_sec=args.delay, mode='auto')
    elif args.rounds > 1:
        engine.run_continuous(rounds=args.rounds, delay_sec=args.delay, mode='interactive')
    else:
        engine.run_round(mode='interactive', verbose=True)


# ============================================================================
# CMD: STATUS / HISTORY / PENDING / RESOLVE
# ============================================================================

def cmd_status():
    print(VirtualWallet.load_or_create().summary())


def cmd_history(n: int):
    _print_history(VirtualWallet.load_or_create(), n)


def cmd_pending():
    _print_pending(VirtualWallet.load_or_create())


# ============================================================================
# CMD: (scrape legacy TennisStats removed)
# ============================================================================

def cmd_stop(port: int = 8765):
    """Mata el proceso del dashboard leyendo kairos.pid, o vía /api/shutdown, o pkill."""
    pid_file = Path(__file__).parent / "kairos.pid"

    # 1. PID file (método principal — disponible desde la nueva versión)
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            import time as _t; _t.sleep(1)
            # Si sigue vivo, forzar SIGKILL
            try:
                os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            pid_file.unlink(missing_ok=True)
            print(f"  ✓ Kairos detenido (PID {pid})")
            return
        except ProcessLookupError:
            print("  ! Proceso no encontrado — limpiando PID file")
            pid_file.unlink(missing_ok=True)
        except Exception as e:
            print(f"  ! Error al matar proceso por PID: {e}")

    # 2. Endpoint HTTP /api/shutdown
    try:
        import urllib.request
        req = urllib.request.Request(
            f"http://localhost:{port}/api/shutdown",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=b"{}",
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            print(f"  ✓ Kairos detenido vía HTTP: {r.read().decode()}")
            return
    except Exception:
        pass

    # 3. Fallback: pkill con SIGKILL — buscar por nombre del script
    import subprocess
    patterns = [
        "kairos.py dashboard",
        "uvicorn.*kairos",
    ]
    for pattern in patterns:
        result = subprocess.run(["pkill", "-9", "-f", pattern], capture_output=True)
        if result.returncode == 0:
            print(f"  ✓ Kairos detenido (pkill -9 '{pattern}')")
            return
    # También matar por puerto como último recurso
    try:
        import subprocess as _sp
        pids = _sp.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True).stdout.strip()
        if pids:
            for p in pids.splitlines():
                _sp.run(["kill", "-9", p])
            print(f"  ✓ Proceso(s) en puerto {port} terminados")
            return
    except Exception:
        pass
    print("  ✗ No se encontró un proceso de Kairos corriendo")


# ============================================================================
# CMD: PLAYERS (Scraping de perfiles de jugadores)
# ============================================================================

def cmd_players(nombres=None, days_stale: int = 1, superficies=None, no_history: bool = False, delay: float = 2.0):
    """
    Scraping de perfiles y stats de jugadores (multi-sport, via Sofascore/Flashscore + models).
    Actualiza PlayerStats, ELO por superficie, RankingHistory.
    """
    import asyncio

    from src.core.orchestration.main import run_player_stats_pipeline_async
    result = asyncio.run(
        run_player_stats_pipeline_async(
            nombres=nombres or None,
            days_stale=days_stale,
            superficies=superficies or None,
        )
    )
    print(f"\n[PLAYERS] Completado — OK={result['ok']}  FAIL={result['fail']}  SKIP={result.get('skipped', 0)}")


# ============================================================================
# ARGUMENT PARSER
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='kairos',
        description='⚡ Kairos — Motor autónomo de arbitraje + visualización web',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    sub = parser.add_subparsers(dest='cmd', metavar='COMANDO')

    # ── run (principal) / dashboard (compatibilidad) ────────────────────────
    # "run" es el comando recomendado: arranca el motor autónomo completo
    # (scans automáticos, auto-bets, live hedge, settlement, rebalance...)
    # + el servidor web para visualización.
    def _add_run_args(parser):
        parser.add_argument('--host', type=str, default='0.0.0.0')
        parser.add_argument('--port', type=int, default=8765)
        parser.add_argument('--interval', type=int, default=60,
            help='Segundos entre scans automáticos (default: 60)')
        parser.add_argument('--auto-bet', action='store_true', default=True,
            help='Colocar apuestas automáticamente cuando haya arbs (default: ON)')
        parser.add_argument('--bet-pct', type=float, default=0.05,
            help='Fracción del balance a apostar (default: 0.05 = 5%%)')
        parser.add_argument('--reload', action='store_true',
            help='Hot-reload automático al modificar código fuente')

    p_run = sub.add_parser('run', help='Arranca el motor autónomo completo + web (recomendado)')
    _add_run_args(p_run)

    p_dash = sub.add_parser('dashboard', help='[DEPRECATED] Alias de "run" (usa "run" en su lugar)')
    _add_run_args(p_dash)

    # ── simulate ───────────────────────────────────────────────────────────
    p_sim = sub.add_parser('simulate', help='Rondas de simulación con wallet virtual')
    p_sim.add_argument('--balance', type=float, default=1000.0,
        help='Balance inicial para wallet nueva (default: 1000)')
    p_sim.add_argument('--reset', action='store_true', help='Resetear la wallet')
    p_sim.add_argument('--auto', type=int, metavar='N', default=0,
        help='N rondas automáticas sin confirmación manual')
    p_sim.add_argument('--rounds', type=int, default=1,
        help='Rondas en modo interactivo (default: 1)')
    p_sim.add_argument('--delay', type=int, default=60,
        help='Segundos entre rondas (default: 60)')
    p_sim.add_argument('--bet-pct', type=float, default=0.05,
        help='%% del balance a apostar por arb (default: 0.05 = 5%%)')
    p_sim.add_argument('--min-roi', type=float, default=0.001,
        help='ROI mínimo (default: 0.001 = 0.1%%)')
    p_sim.add_argument('--include-stat', action='store_true',
        help='Incluir arbs estadísticos (pendientes de resolución manual)')

    # ── scan ───────────────────────────────────────────────────────────────
    p_scan = sub.add_parser('scan', help='Escanear mercados Stake (sin apostar)')
    p_scan.add_argument('--min-roi', type=float, default=-1.0,
        help='ROI mínimo a mostrar (default: -1.0 = todos)')

    # ── status ─────────────────────────────────────────────────────────────
    sub.add_parser('status', help='Estado del wallet virtual')

    # ── history ────────────────────────────────────────────────────────────
    p_hist = sub.add_parser('history', help='Historial de apuestas')
    p_hist.add_argument('n', type=int, nargs='?', default=20,
        help='Número de apuestas a mostrar (default: 20)')

    # ── pending ────────────────────────────────────────────────────────────
    sub.add_parser('pending', help='Apuestas pendientes de resolver')

    # ── stop ───────────────────────────────────────────────────────────────
    p_stop = sub.add_parser('stop', help='Detener el dashboard en ejecución')
    p_stop.add_argument('--port', type=int, default=8765,
        help='Puerto del dashboard (default: 8765, para fallback HTTP)')

    # ── players ────────────────────────────────────────────────────────────
    p_players = sub.add_parser(
        'players',
        help='Scrapear perfiles de jugadores (serve, aces, form, ranking history)'
    )
    p_players.add_argument(
        'nombres', nargs='*',
        help='Nombres explícitos. Sin argumentos = todos los jugadores en la DB.'
    )
    p_players.add_argument(
        '--days-stale', type=int, default=1,
        help='Saltear jugadores actualizados hace menos de N días (default: 1)'
    )
    p_players.add_argument(
        '--no-history', action='store_true',
        help='No scrapear historial de ranking (más rápido)'
    )
    p_players.add_argument(
        '--superficies', nargs='+',
        metavar='SURF',
        choices=['all', 'hard', 'clay', 'grass'],
        help='Superficies a scrapear (default: all hard clay grass)'
    )
    p_players.add_argument(
        '--delay', type=float, default=2.0,
        help='Segundos entre requests (default: 2.0)'
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.cmd is None:
        parser.print_help()
        return

    if args.cmd in ('run', 'dashboard'):
        cmd_dashboard(args)
    elif args.cmd == 'simulate':  cmd_simulate(args)
    elif args.cmd == 'scan':      cmd_scan(min_roi=args.min_roi)
    elif args.cmd == 'status':    cmd_status()
    elif args.cmd == 'history':   cmd_history(args.n)
    elif args.cmd == 'pending':   cmd_pending()
    elif args.cmd == 'stop':      cmd_stop(port=args.port)
    elif args.cmd == 'players':   cmd_players(
        nombres=args.nombres or None,
        days_stale=args.days_stale,
        superficies=args.superficies or None,
        no_history=args.no_history,
        delay=args.delay,
    )


if __name__ == '__main__':
    main()


# Hola esto es una prueba! lol