"""
Reporte de consola sobre verificación retrospectiva de fill de la pata
límite (ver database/accounting/fillverification.py,
strategies/fill_verification.py, workers/fill_verification_worker.py).

Uso local (contra la DB de dev, desde la raíz del repo):
    python3 -m scripts.limit_fill_report

Uso en producción:
    docker exec kairos_fill_verification_worker python3 -m scripts.limit_fill_report

Solo lectura -- no escribe nada, no toca dinero real (no existe en este
proyecto), no requiere red (lee solo la DB local).
"""

import sys
from collections import Counter

from database.db import init_db, db
from database.accounting.fillverification import FillVerification
from database.accounting.limitarbsignal import LimitArbSignal


def _pct(n, total):
    return f"{(n / total * 100):.1f}%" if total else "N/A"


def main():
    init_db()
    with db.connection_context():
        verifications = list(FillVerification.select())
        signals_by_id = {s.id: s for s in LimitArbSignal.select()}

    total = len(verifications)

    print("=" * 72)
    print("KAIROS -- LIMIT-LEG FILL VERIFICATION REPORT (análisis retrospectivo, solo lectura)")
    print("=" * 72)
    print()
    print(f"Total de señales verificadas: {total}")

    if total == 0:
        print()
        print("Todavía no hay filas de FillVerification -- sin estadísticas.")
        print("=" * 72)
        return

    failed = [v for v in verifications if not v.limit_filled_verified]
    filled_ok = total - len(failed)

    print(f"  limit_filled_verified=True:  {filled_ok}  ({_pct(filled_ok, total)})")
    print(f"  limit_filled_verified=False: {len(failed)}  ({_pct(len(failed), total)})")

    print()
    print("-" * 72)
    print("DE LAS FALLIDAS: desglose por exit_reason real (LimitArbSignal)")
    print("-" * 72)
    if failed:
        exit_reasons = Counter()
        for v in failed:
            sig = signals_by_id.get(v.signal_id)
            exit_reasons[sig.exit_reason if sig and sig.exit_reason else '(desconocido)'] += 1
        for reason, count in sorted(exit_reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {reason:<20} n={count:<4} {_pct(count, len(failed))}")
    else:
        print("  (sin señales fallidas)")

    print()
    print("-" * 72)
    print("DE LAS FALLIDAS: desglose por fail_reason (verificación real)")
    print("-" * 72)
    if failed:
        fail_reasons = Counter()
        for v in failed:
            fail_reasons[v.fail_reason if v.fail_reason else 'no_computable'] += 1
        for reason, count in sorted(fail_reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {reason:<20} n={count:<4} {_pct(count, len(failed))}")
    else:
        print("  (sin señales fallidas)")

    print()
    print("=" * 72)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error generando el reporte: {e}", file=sys.stderr)
        sys.exit(1)
