"""Fix retroactivo de orden nombre/apellido en `Player.canonical_name`
(2026-08-10, pedido explícito del usuario tras encontrar "Vanta Valentin"
y "Mike Lorenzo" con pinta de corrupción durante la auditoría post-
migración de la entidad canónica `Player`).

Investigación real hecha antes de escribir este script (NO asumida):
- "Mike Lorenzo": jugador ATP real, confirmado vía ATP Tour
  (atptour.com/en/players/mike-lorenzo/), activo 1970-72 -- coincide
  exacto con los torneos/años que tenemos en `MatchHistory`
  (source="sackmann", que YA entrega "Nombre Apellido" nativo). NO está
  mal ordenado, es un falso positivo de la auditoría visual -- este
  script lo deja intacto porque su única fuente es sackmann.
- "Vanta Valentin": jugador ATP real, Valentin Vanta (rumano), confirmado
  vía ATP Tour + slug real de TennisExplorer ("vanta") y birthdate
  1997-09-28 que YA coincide con el que teníamos guardado. Su única
  fuente es tennisexplorer y el `canonical_name` quedó literal en
  formato TE-nativo ("Apellido Nombre") -- fila migrada ANTES del fix
  de `get_match_detail` (commit 465e897) que normaliza en la fuente.
  Corresponde reordenar a "Valentin Vanta".

Alcance del script (generalización segura, no solo estos 2 casos):
un `Player` es candidato a reordenar SOLO SI:
  1. TODOS los partidos de `MatchHistory` que lo referencian (como
     player_a o player_b) tienen `source == "tennisexplorer"` (nunca
     "sackmann", que ya llega en "Nombre Apellido" real y NO debe
     tocarse -- ahí está el riesgo de romper un nombre ya correcto,
     como pasó con la falsa alarma de Mike Lorenzo).
  2. `canonical_name` coincide LITERALMENTE con el texto crudo
     (`player_a_name`/`player_b_name`) visto en esas filas -- prueba
     de que esa fila nunca pasó por `_te_native_to_nombre_apellido`
     (dato pre-fix), no un nombre ya normalizado a mano.
  3. `canonical_name` tiene 2+ tokens (fail-closed: nombres de 1 solo
     token no tienen apellido que reordenar).

Si el reorden produce un `canonical_name` que YA existe en otro
`Player` (colisión real, posible duplicado de identidad), el caso se
reporta para revisión manual y NO se aplica automáticamente --
fail-closed, nunca se fusionan entidades sin supervisión humana.

Uso:
    python3 -m scripts.tennis.fix_player_canonical_name_te_order --dry-run
    python3 -m scripts.tennis.fix_player_canonical_name_te_order --apply
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from database.historical.tennis import MatchHistory, Player
from outer_apis.scrapers.tennis.tennisexplorer_player_scraper import (
    _te_native_to_nombre_apellido,
)


def _collect_sources_and_raw_names_by_player_id() -> tuple[
    dict[int, set[str]], dict[int, set[str]]
]:
    """1 sola lectura completa de la tabla (patrón ya aprendido en
    `migrate_players_to_canonical_entity.py` tras el bug real de
    performance de esa migración: nunca N+1 queries por player sobre
    ~619K filas)."""
    sources_by_pid: dict[int, set[str]] = defaultdict(set)
    names_by_pid: dict[int, set[str]] = defaultdict(set)
    query = MatchHistory.select(
        MatchHistory.source,
        MatchHistory.player_a_id,
        MatchHistory.player_a_name,
        MatchHistory.player_b_id,
        MatchHistory.player_b_name,
    ).tuples()
    for source, a_id, a_name, b_id, b_name in query:
        if a_id is not None:
            sources_by_pid[a_id].add(source)
            names_by_pid[a_id].add(a_name)
        if b_id is not None:
            sources_by_pid[b_id].add(source)
            names_by_pid[b_id].add(b_name)
    return sources_by_pid, names_by_pid


def _is_abbreviated_or_malformed_raw_name(name: str) -> bool:
    """Fail-closed contra 2 patrones reales encontrados corriendo este
    script en --dry-run contra producción (2026-08-10, 16,871
    candidatos crudos, la mayoría basura antes de este filtro):

    1. Nombre ABREVIADO estilo TE "Apellido Inicial." (ej. "Del
       Federico A.", "Gonzalez Huerta I.") -- el ÚLTIMO token es una
       inicial de 1 letra + punto, NO un nombre de pila completo.
       `_te_native_to_nombre_apellido` fue diseñada para nombres
       COMPLETOS; aplicada acá produce basura tipo "A. Del Federico".
       74% (12,539/16,871) de los candidatos crudos eran de este tipo.

    2. Sufijo parentético de desambiguación por año de nacimiento (ej.
       "Moreno Alejandro (1990)", patrón ya documentado en memoria del
       proyecto -- "(birth year) homonym suffix sometimes appended").
       Reordenar ciegamente rompe el paréntesis (ej. "(1990) Moreno
       Alejandro"). 35 candidatos de este tipo encontrados.

    Ambos se saltan: mejor dejar el nombre como está que producir un
    nombre peor."""
    tokens = name.strip().split(" ")
    if not tokens:
        return True
    last = tokens[-1]
    if len(last) <= 2 and last.endswith("."):
        return True
    if "(" in name or ")" in name:
        return True
    return False


def find_reorder_candidates() -> list[tuple[Player, str]]:
    """Devuelve [(player, nombre_propuesto), ...] -- SOLO candidatos que
    pasan las condiciones fail-closed del docstring del módulo."""
    sources_by_pid, names_by_pid = _collect_sources_and_raw_names_by_player_id()
    candidates: list[tuple[Player, str]] = []
    for player in Player.select():
        sources = sources_by_pid.get(player.id)
        if not sources or sources != {"tennisexplorer"}:
            continue
        raw_names = names_by_pid.get(player.id, set())
        if player.canonical_name not in raw_names:
            continue
        if len(player.canonical_name.strip().split(" ")) < 2:
            continue
        if _is_abbreviated_or_malformed_raw_name(player.canonical_name):
            continue
        proposed = _te_native_to_nombre_apellido(player.canonical_name)
        if proposed == player.canonical_name:
            continue
        candidates.append((player, proposed))
    return candidates


def run(apply: bool) -> dict:
    candidates = find_reorder_candidates()
    applied = []
    collisions = []
    for player, proposed in candidates:
        existing = Player.get_or_none(Player.canonical_name == proposed)
        if existing is not None and existing.id != player.id:
            collisions.append(
                {
                    "player_id": player.id,
                    "current": player.canonical_name,
                    "proposed": proposed,
                    "collides_with_player_id": existing.id,
                }
            )
            continue
        applied.append(
            {"player_id": player.id, "from": player.canonical_name, "to": proposed}
        )
        if apply:
            player.canonical_name = proposed
            player.save()
    return {
        "candidates_found": len(candidates),
        "applied": applied,
        "collisions_skipped": collisions,
        "mode": "apply" if apply else "dry-run",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    result = run(apply=args.apply)
    print(f"[fix_player_canonical_name_te_order] mode={result['mode']}")
    print(f"candidates_found={result['candidates_found']}")
    for row in result["applied"]:
        print(f"  {'APPLIED' if args.apply else 'WOULD APPLY'}: "
              f"player_id={row['player_id']} '{row['from']}' -> '{row['to']}'")
    if result["collisions_skipped"]:
        print(f"COLLISIONS SKIPPED (manual review needed): "
              f"{len(result['collisions_skipped'])}")
        for c in result["collisions_skipped"]:
            print(f"  player_id={c['player_id']} '{c['current']}' -> "
                  f"'{c['proposed']}' collides with player_id="
                  f"{c['collides_with_player_id']}")


if __name__ == "__main__":
    main()
