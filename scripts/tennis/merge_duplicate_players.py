"""Fusión de `Player` duplicados por orden de nombre invertido
(2026-08-10, pedido explícito del usuario tras correr
`fix_player_canonical_name_te_order.py --dry-run` contra producción y
encontrar 143 colisiones reales: la misma persona existe como 2 filas
`Player` distintas en orden opuesto, ej. "Karin Knapp" (id=15594) y
"Knapp Karin" (id=14459) -- fragmentación real de identidad heredada
de la migración retroactiva de Fase 1 (`migrate_players_to_canonical_
entity.py`), NO un problema de orden de nombre en sí.

Reusa `find_reorder_candidates()` de `fix_player_canonical_name_te_
order.py`: cada candidato que colisiona con un `Player` YA existente
bajo el nombre correcto ("Nombre Apellido") es, por construcción, el
mismo caso -- el candidato es la fila TE-nativa sin normalizar (`dup`,
sobrevive únicamente por el bug de clustering), el que ya tiene el
nombre correcto es la fila real a conservar (`survivor`).

Fusión (atómica por par, vía `db_historical.atomic()`):
  1. Repuntar TODAS las filas de `MatchHistory` que referencian `dup`
     (como `player_a_id` o `player_b_id`) hacia `survivor.id`.
  2. Enriquecer `survivor` con los campos de bio de `dup` que estén
     NULL en `survivor` (misma política de `_resolve_player_id`:
     nunca sobreescribe un valor ya confirmado).
  3. Si `survivor` y `dup` tienen el MISMO campo de bio con valores
     DISTINTOS y no-nulos (conflicto real, ej. 2 birthdates distintos)
     -- fail-closed, el par completo se salta y se reporta para
     revisión manual, NO se fusiona a ciegas.
  4. Borrar la fila `dup` (ya sin ninguna fila de `MatchHistory`
     apuntándole).

Uso:
    python3 -m scripts.tennis.merge_duplicate_players --dry-run
    python3 -m scripts.tennis.merge_duplicate_players --apply
"""

from __future__ import annotations

import argparse

from database.db import db_historical
from database.historical.tennis import MatchHistory, Player
from scripts.tennis.fix_player_canonical_name_te_order import (
    find_reorder_candidates,
)

_BIO_FIELDS = ("slug", "tour", "birthdate", "height_cm", "weight_kg", "plays")


def find_duplicate_pairs() -> list[tuple[Player, Player]]:
    """Devuelve [(dup, survivor), ...] -- `dup` es la fila TE-nativa sin
    normalizar que colisiona con `survivor`, que ya tiene el nombre
    correcto. Reusa exactamente la misma lógica fail-closed de
    `find_reorder_candidates` (fuente única de verdad de qué es un
    nombre crudo sin normalizar vs. uno ya correcto)."""
    candidates = find_reorder_candidates()
    pairs: list[tuple[Player, Player]] = []
    for dup, proposed in candidates:
        survivor = Player.get_or_none(Player.canonical_name == proposed)
        if survivor is None or survivor.id == dup.id:
            continue
        pairs.append((dup, survivor))
    return pairs


def _bio_conflict(dup: Player, survivor: Player) -> str | None:
    for field in _BIO_FIELDS:
        dup_val = getattr(dup, field)
        survivor_val = getattr(survivor, field)
        if dup_val is not None and survivor_val is not None and dup_val != survivor_val:
            return field
    return None


def _merge_one(dup: Player, survivor: Player, apply: bool) -> dict:
    conflict_field = _bio_conflict(dup, survivor)
    if conflict_field is not None:
        return {
            "dup_id": dup.id, "dup_name": dup.canonical_name,
            "survivor_id": survivor.id, "survivor_name": survivor.canonical_name,
            "status": "conflict", "conflict_field": conflict_field,
        }

    matches_repointed = MatchHistory.select().where(
        (MatchHistory.player_a == dup) | (MatchHistory.player_b == dup)
    ).count()

    if apply:
        with db_historical.atomic():
            MatchHistory.update(player_a=survivor).where(
                MatchHistory.player_a == dup
            ).execute()
            MatchHistory.update(player_b=survivor).where(
                MatchHistory.player_b == dup
            ).execute()
            for field in _BIO_FIELDS:
                if getattr(survivor, field) is None and getattr(dup, field) is not None:
                    setattr(survivor, field, getattr(dup, field))
            survivor.save()
            dup.delete_instance()

    return {
        "dup_id": dup.id, "dup_name": dup.canonical_name,
        "survivor_id": survivor.id, "survivor_name": survivor.canonical_name,
        "status": "merged", "matches_repointed": matches_repointed,
    }


def run(apply: bool) -> dict:
    pairs = find_duplicate_pairs()
    merged = []
    conflicts = []
    for dup, survivor in pairs:
        result = _merge_one(dup, survivor, apply=apply)
        if result["status"] == "conflict":
            conflicts.append(result)
        else:
            merged.append(result)
    return {
        "pairs_found": len(pairs),
        "merged": merged,
        "conflicts_skipped": conflicts,
        "mode": "apply" if apply else "dry-run",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    result = run(apply=args.apply)
    print(f"[merge_duplicate_players] mode={result['mode']}")
    print(f"pairs_found={result['pairs_found']}")
    for row in result["merged"]:
        verb = "MERGED" if args.apply else "WOULD MERGE"
        print(f"  {verb}: dup_id={row['dup_id']} '{row['dup_name']}' -> "
              f"survivor_id={row['survivor_id']} '{row['survivor_name']}' "
              f"(matches_repointed={row['matches_repointed']})")
    if result["conflicts_skipped"]:
        print(f"CONFLICTS SKIPPED (manual review needed): "
              f"{len(result['conflicts_skipped'])}")
        for c in result["conflicts_skipped"]:
            print(f"  dup_id={c['dup_id']} '{c['dup_name']}' vs "
                  f"survivor_id={c['survivor_id']} '{c['survivor_name']}' "
                  f"conflict on field '{c['conflict_field']}'")


if __name__ == "__main__":
    main()
