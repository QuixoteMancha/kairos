#!/usr/bin/env python3
"""
check_wallet_history.py
=======================
Verifica la consistencia del balance del wallet virtual comparando
los registros de depósitos, retiros y apuestas contra el balance actual.

Uso:
    python check_wallet_history.py [--verbose] [--csv historial.csv]
"""
import argparse
import sys

# ---------------------------------------------------------------------------
# Bootstrap: asegurar que src/ esté en el path antes de importar modelos
# ---------------------------------------------------------------------------
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from src.database.database import WalletBet, WalletDeposit, WalletWithdraw, init_db

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(description="Verifica consistencia del wallet.")
parser.add_argument("--verbose", "-v", action="store_true", help="Muestra cada apuesta")
parser.add_argument("--csv", metavar="FILE", help="Exporta historial de apuestas a CSV")
args = parser.parse_args()

# ---------------------------------------------------------------------------
# Init DB (solo lectura, but models need connection)
# ---------------------------------------------------------------------------
init_db()

# ---------------------------------------------------------------------------
# 1. Depósitos
# ---------------------------------------------------------------------------
deposits = list(WalletDeposit.select().order_by(WalletDeposit.timestamp))
total_deposits = sum(d.amount for d in deposits)

# ---------------------------------------------------------------------------
# 2. Retiros
# ---------------------------------------------------------------------------
withdrawals = list(WalletWithdraw.select().order_by(WalletWithdraw.timestamp))
total_withdrawals = sum(w.amount for w in withdrawals)

# ---------------------------------------------------------------------------
# 3. Apuestas
# ---------------------------------------------------------------------------
all_bets = list(WalletBet.select().order_by(WalletBet.placed_at))
pending_bets  = [b for b in all_bets if b.status == 'pending']
resolved_bets = [b for b in all_bets if b.status in ('won', 'lost')]
won_bets      = [b for b in all_bets if b.status == 'won']
lost_bets     = [b for b in all_bets if b.status == 'lost']

realized_pnl   = sum(b.pnl for b in resolved_bets)
pending_staked = sum(b.total_staked for b in pending_bets)
total_wagered  = sum(b.total_staked for b in all_bets)

# ---------------------------------------------------------------------------
# 4. Balance esperado
#    free_balance_expected = deposits - withdrawals + realized_pnl - pending_staked
# ---------------------------------------------------------------------------
expected_free = total_deposits - total_withdrawals + realized_pnl - pending_staked

# ---------------------------------------------------------------------------
# 5. Balance real (tabla wallet en DB)
# ---------------------------------------------------------------------------
from src.database.database import Wallet as _WalletRow

_wallet_row   = _WalletRow.select().first()
real_balance  = _wallet_row.balance  if _wallet_row else 0.0
real_deposits = _wallet_row.deposits if _wallet_row else 0.0   # acumulado en wallet.deposits
real_equity   = real_balance + pending_staked   # equity total estimado

# ---------------------------------------------------------------------------
# 6. Discrepancia
# ---------------------------------------------------------------------------
discrepancy = real_balance - expected_free
ok = abs(discrepancy) <= 0.02   # tolerancia de 2 céntimos

# ---------------------------------------------------------------------------
# 7. Impresión
# ---------------------------------------------------------------------------
SEP = "─" * 60
print(SEP)
print("  KAIROS — HISTORIAL Y CONSISTENCIA DEL WALLET")
print(SEP)
print(f"  Depósitos ({len(deposits):>3} reg.)          : €{total_deposits:>10.2f}")
dep_match = "✓" if abs(real_deposits - total_deposits) <= 0.01 else f"✗ wallet.deposits={real_deposits:.2f}"
print(f"  wallet.deposits (acumulado)     : €{real_deposits:>10.2f}  {dep_match}")
print(f"  Retiros   ({len(withdrawals):>3} reg.)          : €{total_withdrawals:>10.2f}  (−)")
print(f"  PnL realizado                   : €{realized_pnl:>+10.2f}")
print(f"  Apostado pendiente              : €{pending_staked:>10.2f}  (−)")
print(SEP)
print(f"  Balance esperado                : €{expected_free:>10.2f}")
print(f"  Balance real  (DB wallet)       : €{real_balance:>10.2f}")
diff_sign = "✓" if ok else "✗"
print(f"  Discrepancia                    : €{discrepancy:>+10.4f}  {diff_sign}")
print(SEP)
print(f"  Equity total estimado           : €{real_equity:>10.2f}")
print("  Apuestas → ganadas / perdidas / pendientes")
print(f"    {len(won_bets)} won  {len(lost_bets)} lost  {len(pending_bets)} pending  ({len(all_bets)} total)")
win_rate = (len(won_bets) / len(resolved_bets) * 100) if resolved_bets else 0.0
print(f"  Win rate (resueltas)            : {win_rate:>6.1f}%")
print(f"  Total apostado (all-time)       : €{total_wagered:>10.2f}")
print(SEP)

if not ok:
    print("\n  [!] DISCREPANCIA > 0.02€ — revisar registros")
    print()

# ---------------------------------------------------------------------------
# 8. Historial detallado de depósitos
# ---------------------------------------------------------------------------
if deposits:
    print("\n  DEPÓSITOS")
    print(f"  {'#':>3}  {'Fecha':^22}  {'Monto':>8}  Nota")
    for i, d in enumerate(deposits, 1):
        print(f"  {i:>3}  {d.timestamp:<22}  €{d.amount:>7.2f}  {d.note}")
    print()

# ---------------------------------------------------------------------------
# 9. Historial detallado de retiros
# ---------------------------------------------------------------------------
if withdrawals:
    print("\n  RETIROS")
    print(f"  {'#':>3}  {'Fecha':^22}  {'Monto':>8}  Nota")
    for i, w in enumerate(withdrawals, 1):
        print(f"  {i:>3}  {w.timestamp:<22}  €{w.amount:>7.2f}  {w.note}")
    print()

# ---------------------------------------------------------------------------
# 10. Verbose: tabla de apuestas
# ---------------------------------------------------------------------------
if args.verbose:
    print("\n  APUESTAS (detalle)")
    print(f"  {'Fecha':^22}  {'Tipo':^22}  {'Staked':>7}  {'PnL':>7}  {'Estado':^8}  Fixture")
    for b in all_bets:
        pnl_str = f"{b.pnl:>+7.2f}" if b.status in ('won','lost') else "       "
        st = b.status.upper()[:8]
        print(f"  {b.placed_at[:22]:<22}  {b.arb_tipo:<22}  €{b.total_staked:>6.2f}  {pnl_str}  {st:<8}  {b.fixture_name[:35]}")
    print()

# ---------------------------------------------------------------------------
# 11. CSV export
# ---------------------------------------------------------------------------
if args.csv:
    import csv
    path = Path(args.csv)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["placed_at","fixture_name","arb_tipo","total_staked","guaranteed_return","roi","pnl","status","combo_key"])
        for b in all_bets:
            writer.writerow([b.placed_at, b.fixture_name, b.arb_tipo,
                             b.total_staked, b.guaranteed_return,
                             b.roi, b.pnl, b.status, b.combo_key])
    print(f"  CSV exportado → {path.resolve()}")

print()
sys.exit(0 if ok else 1)
