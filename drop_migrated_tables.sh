#!/bin/bash
# Borra las tablas ya migradas/redundantes en la mini-pc -- 2026-08-09,
# pedido explícito del usuario. Backups YA hechos (verificados con
# conteo de filas antes de este script):
#   tennis_match_history_backup_20260809        (438,419 filas)
#   tml_database_match_backup_20260809           (197,998 filas)
#   charted_match_backup_20260809                (7,744 filas)
#   charted_match_stats_backup_20260809          (7,730 filas)
#   tennis_surface_match_history_backup_20260809 (2,383 filas)
#
# NO borra tennis_match_history_unvalidated -- la sigue usando
# scripts/tennis/validate_unresolved_via_live_fetch.py (corriendo en
# background en este momento).
#
# Correr en la mini-pc: bash drop_migrated_tables.sh

set -euo pipefail

TABLES=(
  tennis_match_history
  tml_database_match
  charted_match
  charted_match_stats
  tennis_surface_match_history
)

for t in "${TABLES[@]}"; do
  echo "Borrando $t ..."
  docker exec kairos python3 -c "
from database.db import db_historical
db_historical.connect(reuse_if_open=True)
db_historical.execute_sql('DROP TABLE $t')
print('  OK: $t borrada')
"
done

echo
echo "Verificación final:"
docker exec kairos python3 -c "
from database.db import db_historical
db_historical.connect(reuse_if_open=True)
remaining = [r[0] for r in db_historical.execute_sql(\"SELECT name FROM sqlite_master WHERE type='table' AND name IN ('tennis_match_history','tml_database_match','charted_match','charted_match_stats','tennis_surface_match_history','tennis_match_history_unvalidated')\").fetchall()]
print('tablas que quedan de esta lista (debería ser solo tennis_match_history_unvalidated):', remaining)
"
