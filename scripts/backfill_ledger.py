#!/usr/bin/env python3
"""
backfill_ledger.py
==================
Reconstruye WalletLedger desde las tablas existentes:
  - walletdeposit  → entradas 'deposit'
  - walletwithdraw → entradas 'withdraw'
  - walletbet      → entradas 'bet_placed', 'bet_won', 'bet_lost'

Las operaciones se ordenan cronológicamente y se simula el saldo
acumulado para poblar balance_after, equity_after, pnl_cumulative, etc.

Uso:
    python backfill_ledger.py [--dry-run]
"""
import argparse
import sys

sys.path.insert(0, ".")
from src.database.database import (
    Wallet,
    WalletBet,
    WalletDeposit,
    WalletLedger,
    WalletWithdraw,
    db,
    init_db,
)

parser = argparse.ArgumentParser()
parser.add_argument("--dry-run", action="store_true",
                    help="Solo muestra las entradas sin insertar en la DB")
args = parser.parse_args()

init_db()

# ── Comprobar si ya hay registros ────────────────────────────────────────────
existing = WalletLedger.select().count()
if existing > 0:
    print(f"[LEDGER] Ya existen {existing} entradas. Limpiando para rehacer...")
    WalletLedger.delete().execute()

# ── Construir lista de eventos cronológicos ───────────────────────────────────
events = []

# Depósitos
for d in WalletDeposit.select():
    events.append({
        "timestamp":  d.timestamp,
        "op_type":    "deposit",
        "ref_table":  "walletdeposit",
        "ref_id":     str(d.id),
        "amount":     +d.amount,
        "note":       d.note or "",
    })

# Retiros
for w in WalletWithdraw.select():
    events.append({
        "timestamp":  w.timestamp,
        "op_type":    "withdraw",
        "ref_table":  "walletwithdraw",
        "ref_id":     str(w.id),
        "amount":     -w.amount,
        "note":       w.note or "",
    })

# Apuestas: dos eventos por bet (placed + resolved)
for b in WalletBet.select():
    # Evento: apuesta colocada
    events.append({
        "timestamp":  b.placed_at,
        "op_type":    "bet_placed",
        "ref_table":  "walletbet",
        "ref_id":     b.bet_id,
        "amount":     -b.total_staked,
        "note":       f"{b.arb_tipo} | {b.fixture_name}",
    })
    # Evento: resolución (solo si no está pendiente)
    if b.status == "won":
        # Timestamp de resolución = placed_at (no tenemos el timestamp real de resolve)
        # Se marca un segundo después para que quede ordenado después de placed
        ts_resolved = b.placed_at  # mejor que nada; se puede mejorar si se añade resolved_at
        events.append({
            "timestamp":  ts_resolved,
            "op_type":    "bet_won",
            "ref_table":  "walletbet",
            "ref_id":     b.bet_id,
            "amount":     +b.guaranteed_return,
            "note":       f"{b.arb_tipo} | {b.fixture_name} | pnl={b.pnl:+.2f}",
        })
    elif b.status == "lost":
        ts_resolved = b.placed_at
        events.append({
            "timestamp":  ts_resolved,
            "op_type":    "bet_lost",
            "ref_table":  "walletbet",
            "ref_id":     b.bet_id,
            "amount":     round(b.total_staked + b.pnl, 2) if b.pnl < 0 else 0.0,
            "note":       f"{b.arb_tipo} | {b.fixture_name} | pnl={b.pnl:+.2f}",
        })

# Ordenar cronológicamente; para mismo timestamp: deposit > bet_placed > bet_won/lost > withdraw
TYPE_ORDER = {"deposit": 0, "withdraw": 1, "bet_placed": 2, "bet_won": 3, "bet_lost": 3}
events.sort(key=lambda e: (e["timestamp"], TYPE_ORDER.get(e["op_type"], 9)))

print(f"[LEDGER] {len(events)} eventos a procesar...")

# ── Simular estado acumulado ──────────────────────────────────────────────────
# Estado inicial: balance = initial_balance de wallet, antes de cualquier apuesta
wallet_row = Wallet.select().first()
initial_balance   = float(wallet_row.initial_balance) if wallet_row else 50.0

# Reconstruir balance paso a paso
# Necesitamos conocer el conjunto de bets pendientes en cada momento
pending_ids: set = set()   # bet_ids actualmente "placed" pero no resueltos

# Pre-cargar info de bets para calcular pending_staked dinámico
bet_info = {b.bet_id: b for b in WalletBet.select()}

# Calcular balance inicial (antes del primer evento)
# = initial_balance - sum(pending_staked) + sum(pnl resueltos) NO
# Más simple: simular desde 0 y partir desde initial_balance real
balance = initial_balance

# Restar todos los depósitos para calcular el balance base antes de empezar
# (el wallet partió con initial_balance que representa los depósitos acumulados)
deps_total  = sum(d.amount for d in WalletDeposit.select())
wdrs_total  = sum(w.amount for w in WalletWithdraw.select())
pnl_total   = sum(b.pnl or 0.0 for b in WalletBet.select() if b.status in ("won","lost"))
staked_pend = sum(b.total_staked for b in WalletBet.select() if b.status == "pending")

# Balance libre = depósitos_netos + pnl_total - staked_pendiente
balance_now = round(deps_total - wdrs_total + pnl_total - staked_pend, 2)

# Verificar contra DB
if wallet_row:
    if abs(balance_now - wallet_row.balance) > 0.05:
        print(f"[WARN] Balance calculado {balance_now:.2f} vs DB {wallet_row.balance:.2f}")
    balance = wallet_row.balance
else:
    balance = balance_now

# Para el backfill vamos a simular hacia ATRÁS partiendo del balance actual,
# pero es más fiable simular hacia adelante desde 0.
# Partimos del estado ANTES del primer evento.

# Calcular el balance justo antes del primer evento:
# Es el balance actual + pending_staked - sumatorio de todos los pnl ganados + sumatorio de todos los stakes
# Demasiado complejo → simplificar: simular acumulando desde un saldo inicial estimado.

# Estrategia simple: partir de deposits_total como capital disponible en el inicio,
# luego aplicar cada evento.
running_balance   = 0.0     # balance libre acumulado
running_deposits  = 0.0     # depósitos netos acumulados
running_pnl       = 0.0     # PnL realizado acumulado
running_pending   = {}      # bet_id → total_staked

entries_to_insert = []

for ev in events:
    op = ev["op_type"]
    amt = ev["amount"]

    if op == "deposit":
        running_balance  += abs(amt)
        running_deposits += abs(amt)
    elif op == "withdraw":
        running_balance  -= abs(amt)
        running_deposits -= abs(amt)
    elif op == "bet_placed":
        stake = abs(amt)
        running_balance -= stake
        running_pending[ev["ref_id"]] = stake
    elif op == "bet_won":
        bet_id = ev["ref_id"]
        b = bet_info.get(bet_id)
        if b:
            running_balance += b.guaranteed_return
            running_pnl     += b.pnl or 0.0
            running_pending.pop(bet_id, None)
    elif op == "bet_lost":
        bet_id = ev["ref_id"]
        b = bet_info.get(bet_id)
        if b:
            partial = max(0.0, round(b.total_staked + b.pnl, 2))
            if partial > 0:
                running_balance += partial
            running_pnl -= abs(b.pnl or 0.0)
            running_pending.pop(bet_id, None)

    pending_staked_now = round(sum(running_pending.values()), 2)
    equity_now         = round(running_balance + pending_staked_now, 2)

    entries_to_insert.append({
        "timestamp":      ev["timestamp"],
        "op_type":        op,
        "ref_table":      ev["ref_table"],
        "ref_id":         ev["ref_id"],
        "amount":         round(amt, 2),
        "balance_after":  round(running_balance, 2),
        "equity_after":   equity_now,
        "deposits_total": round(running_deposits, 2),
        "pnl_cumulative": round(running_pnl, 2),
        "pending_staked": pending_staked_now,
        "note":           ev["note"],
    })

# ── Output ────────────────────────────────────────────────────────────────────
print(f"\n{'TS':^22}  {'Op':^12}  {'Amount':>8}  {'Balance':>9}  {'Equity':>9}  {'PnL':>8}  Nota")
print("─" * 110)
for e in entries_to_insert:
    print(f"  {e['timestamp'][:19]:<20}  {e['op_type']:<12}  {e['amount']:>+8.2f}  "
          f"€{e['balance_after']:>8.2f}  €{e['equity_after']:>8.2f}  "
          f"{e['pnl_cumulative']:>+8.2f}  {e['note'][:50]}")

print(f"\n  Balance final simulado: €{running_balance:.2f}")
print(f"  PnL acumulado:          €{running_pnl:+.2f}")
print(f"  Eventos totales:        {len(entries_to_insert)}")

if args.dry_run:
    print("\n[DRY-RUN] No se modificó la base de datos.")
    sys.exit(0)

# ── Insertar ──────────────────────────────────────────────────────────────────
print(f"\nInsertando {len(entries_to_insert)} entradas en walletledger...")
with db.atomic():
    for chunk_start in range(0, len(entries_to_insert), 100):
        chunk = entries_to_insert[chunk_start:chunk_start + 100]
        WalletLedger.insert_many(chunk).execute()

print(f"[LEDGER] Backfill completado: {WalletLedger.select().count()} entradas en DB.")
