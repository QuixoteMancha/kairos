"""Reparación del incidente real de `merge_duplicate_players.py`
(2026-08-10): un bug de pares simétricos no deduplicados causó que
`Player.id` de AMBOS lados de ~25-30 pares fueran borrados durante un
`--apply` en producción, dejando `MatchHistory.player_a_id`/
`player_b_id` apuntando a filas `Player` que ya no existen (FK
huérfana). Verificado en vivo: 17,676 filas con `player_a_id` huérfano,
5,992 con `player_b_id` huérfano. CERO pérdida de datos real -- los
campos de texto (`player_a_name`/`player_b_name`) nunca se tocaron.

Este script es el paso 1 de la reparación: anula (a NULL) SOLO las
columnas FK que apuntan a un `Player` inexistente, dejando todo lo
demás de la fila intacto. Paso 2 (no incluido acá, ya existe y está
probado): volver a correr `migrate_players_to_canonical_entity.
run_migration()`, que solo rellena FKs `NULL` -- reconstruye el
linkeo usando el texto crudo intacto, sin arriesgar los datos ya
correctos del resto de la tabla.

Uso:
    python3 -m scripts.tennis.null_out_orphaned_player_fk --dry-run
    python3 -m scripts.tennis.null_out_orphaned_player_fk --apply
"""

from __future__ import annotations

import argparse

from database.db import db_historical
from database.historical.tennis import MatchHistory, Player


def find_orphaned_fk_row_ids() -> dict[str, list[int]]:
    """Devuelve {"player_a": [match_history.id, ...], "player_b": [...]}
    -- IDs de fila cuya FK apunta a un `Player` que no existe. 1 sola
    lectura por lado (patrón de batching ya aprendido de los bugs de
    performance anteriores en este mismo módulo de scripts)."""
    existing_player_ids = {p.id for p in Player.select(Player.id)}

    orphan_a: list[int] = []
    orphan_b: list[int] = []
    for row in MatchHistory.select(
        MatchHistory.id, MatchHistory.player_a_id, MatchHistory.player_b_id
    ).tuples():
        row_id, a_id, b_id = row
        if a_id is not None and a_id not in existing_player_ids:
            orphan_a.append(row_id)
        if b_id is not None and b_id not in existing_player_ids:
            orphan_b.append(row_id)
    return {"player_a": orphan_a, "player_b": orphan_b}


def run(apply: bool) -> dict:
    orphans = find_orphaned_fk_row_ids()
    _BATCH = 500  # límite real de variables por query en SQLite
    nulled_a = nulled_b = 0
    if apply:
        with db_historical.connection_context():
            ids = orphans["player_a"]
            for i in range(0, len(ids), _BATCH):
                nulled_a += (
                    MatchHistory.update(player_a_id=None)
                    .where(MatchHistory.id.in_(ids[i:i + _BATCH]))
                    .execute()
                )
            ids = orphans["player_b"]
            for i in range(0, len(ids), _BATCH):
                nulled_b += (
                    MatchHistory.update(player_b_id=None)
                    .where(MatchHistory.id.in_(ids[i:i + _BATCH]))
                    .execute()
                )
    return {
        "mode": "apply" if apply else "dry-run",
        "player_a_orphans_found": len(orphans["player_a"]),
        "player_b_orphans_found": len(orphans["player_b"]),
        "player_a_nulled": nulled_a,
        "player_b_nulled": nulled_b,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    result = run(apply=args.apply)
    print(f"[null_out_orphaned_player_fk] {result}")


if __name__ == "__main__":
    main()
