#!/usr/bin/env python3
"""
clean_orphan_bets.py
--------------------
Limpia apuestas huérfanas de la DB de Kairos.

Criterios de huérfana:
  1. cross_book_arb / cross_book_intra / cross_book_total_arb:
     combo_key compartido con menos de 2 bets en total.
  2. cross_book_hedge / cross_book_intra_hedge / live-hedge / live-hedge-xbook:
     source_bet_id vacío o no existe en DB.

Para cada bet huérfana con status='pending':
  - Se devuelve el stake al balance del wallet correspondiente.
  - Se registra un apunte en el ledger.
  - Se elimina la bet (y sus legs) de la DB.

Las bets huérfanas ya resueltas (won/lost/void) sólo se eliminan si se pasa --include-resolved.

Uso:
    python scripts/clean_orphan_bets.py [--dry-run] [--include-resolved]

Opciones:
    --dry-run           Muestra lo que se haría sin ejecutar cambios.
    --include-resolved  También elimina bets huérfanas ya resueltas.
"""
import sys
import os
import argparse
from datetime import datetime

# ── Path setup ───────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..'))

from src.database.database import (
    db, init_db, WalletBet, WalletLedger, Wallet,
)

# ── Tipos de bet que forman pares (órfana si combo_key tiene < 2 bets) ───────
_PAIR_TYPES = {
    'cross_book_arb',
    'cross_book_intra',
    'cross_book_total_arb',
    'cross_book_intra_hedge',  # puede ser solitaria si el arb original falló
}

# ── Tipos de hedge que requieren source_bet_id válido ────────────────────────
_HEDGE_TYPES = {
    'cross_book_hedge',
    'live-hedge',
    'live-hedge-xbook',
}


def _write_ledger(platform: str, amount: float, bet_id: str, note: str, dry_run: bool):
    if dry_run:
        return
    try:
        WalletLedger.create(
            platform=platform,
            timestamp=datetime.now().isoformat(),
            op_type='orphan_cleanup',
            amount=amount,
            ref_table='walletbet',
            ref_id=bet_id,
            balance_after=None,
            note=note,
        )
    except Exception as e:
        print(f"  [LEDGER] Error al escribir: {e}")


def find_orphans(include_resolved: bool = False) -> list[dict]:
    """Retorna lista de bets huérfanas con metadatos."""
    orphans = []

    all_bets = list(WalletBet.select())
    # Índice rápido por combo_key y bet_id
    combo_counts: dict[str, int] = {}
    bet_ids: set[str] = {b.bet_id for b in all_bets}

    for b in all_bets:
        if b.combo_key and b.arb_tipo in _PAIR_TYPES:
            combo_counts[b.combo_key] = combo_counts.get(b.combo_key, 0) + 1

    for bet in all_bets:
        if not include_resolved and bet.status not in ('pending', ''):
            continue

        reason = None

        if bet.arb_tipo in _PAIR_TYPES and bet.combo_key:
            if combo_counts.get(bet.combo_key, 0) < 2:
                reason = f"combo_key '{bet.combo_key}' solitario (falta pareja)"

        elif bet.arb_tipo in _HEDGE_TYPES:
            src = getattr(bet, 'source_bet_id', '') or ''
            if not src or src not in bet_ids:
                reason = f"source_bet_id '{src}' no existe en DB"

        if reason:
            orphans.append({
                'bet_id':    bet.bet_id,
                'platform':  bet.platform,
                'status':    bet.status,
                'fixture':   bet.fixture_name,
                'arb_tipo':  bet.arb_tipo,
                'stake':     bet.total_staked,
                'placed_at': bet.placed_at,
                'combo_key': getattr(bet, 'combo_key', '') or '',
                'reason':    reason,
            })

    return orphans


def clean_orphans(orphans: list[dict], dry_run: bool = False) -> int:
    """Elimina las bets huérfanas, ajusta balances y escribe ledger."""
    cleaned = 0

    for o in orphans:
        bet_id   = o['bet_id']
        platform = o['platform']
        stake    = o['stake']
        status   = o['status']

        prefix = "[DRY-RUN] " if dry_run else ""
        print(
            f"{prefix}→ {bet_id[:8]} | {platform:10s} | {o['arb_tipo']:30s} | "
            f"status={status:7s} | stake={stake:.2f} | {o['fixture'][:40]}"
        )
        print(f"   razón: {o['reason']}")

        if status == 'pending' and stake > 0:
            # Devolver stake al balance del wallet
            wallet_row = Wallet.get_or_none(Wallet.platform == platform)
            if wallet_row:
                new_balance = round(wallet_row.balance + stake, 2)
                print(f"   {prefix}Wallet {platform}: {wallet_row.balance:.2f} → {new_balance:.2f} (+{stake:.2f})")
                if not dry_run:
                    wallet_row.balance = new_balance
                    wallet_row.save()
                _write_ledger(
                    platform=platform,
                    amount=stake,
                    bet_id=bet_id,
                    note=f"orphan_cleanup | stake devuelto | {o['arb_tipo']} | {o['fixture'][:60]}",
                    dry_run=dry_run,
                )

        if not dry_run:
            # Borrar legs primero (FK)
            db.execute_sql("DELETE FROM walletbet_leg WHERE bet_id = ?", (bet_id,))
            WalletBet.delete().where(WalletBet.bet_id == bet_id).execute()

        cleaned += 1

    return cleaned


def main():
    parser = argparse.ArgumentParser(description="Limpia apuestas huérfanas de Kairos DB")
    parser.add_argument('--dry-run',          action='store_true', help='Solo mostrar, sin cambios')
    parser.add_argument('--include-resolved', action='store_true', help='Incluir bets ya resueltas (won/lost/void)')
    args = parser.parse_args()

    init_db()

    print("=" * 70)
    print(f"  Kairos — Limpieza de apuestas huérfanas  [{datetime.now():%Y-%m-%d %H:%M}]")
    if args.dry_run:
        print("  MODO DRY-RUN: no se realizarán cambios")
    print("=" * 70)

    orphans = find_orphans(include_resolved=args.include_resolved)

    if not orphans:
        print("\n✓ No se encontraron apuestas huérfanas.")
        return

    print(f"\nEncontradas: {len(orphans)} apuesta(s) huérfana(s)\n")

    cleaned = clean_orphans(orphans, dry_run=args.dry_run)

    print()
    if args.dry_run:
        print(f"[DRY-RUN] Se eliminarían {cleaned} apuesta(s) huérfana(s).")
        print("Ejecuta sin --dry-run para aplicar los cambios.")
    else:
        print(f"✓ {cleaned} apuesta(s) huérfana(s) eliminadas.")
        print("Reinicia el contenedor para que el wallet en memoria quede sincronizado:")
        print("  docker restart kairos")


if __name__ == '__main__':
    main()
