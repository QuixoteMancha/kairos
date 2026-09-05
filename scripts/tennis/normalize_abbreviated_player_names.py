"""Normaliza `Player.canonical_name` abreviado ("Apellido I.") al nombre
completo real -- 2026-09-05, pedido explícito del usuario ("partamos
con normalizar todos los nombres, usando el script que verifica los
matches en tennis explorer para validar el nombre completo").

MÉTODO: mismo principio que `verify_and_merge_duplicate_players.py` --
se toma un `match_id` REAL (no sintético `kalshi:*`) de cada jugador
abreviado y se pide `get_match_detail(match_id)` en vivo. Esa página
trae el nombre completo real de TennisExplorer (`player_a_name`/
`player_b_name`, ya normalizado a "Nombre Apellido" por
`_te_native_to_nombre_apellido`), no una heurística.

DOS CASOS al resolver el nombre completo:
  - Sin colisión (ningún otro `Player` ya tiene ese nombre completo) ->
    simple RENAME de `canonical_name` en la misma fila. Cero riesgo:
    no toca ningún FK, ningún partido, solo el campo de texto.
  - CON colisión (ya existe otro `Player` con ese nombre completo,
    normalmente porque las dos entidades listas para fusionar de
    `verify_and_merge_duplicate_players.py` tenían slugs DISTINTOS y
    por eso nunca se agruparon ahí) -> NO se renombra a ciegas (crearía
    un duplicado de `canonical_name`, que además tiene índice único).
    Se reporta como "REQUIERE FUSIÓN" para revisión con el otro script,
    nunca se fusiona automáticamente acá.

Fail-closed en todo: sin `match_id` real utilizable, o `get_match_detail`
no resuelve, el jugador se reporta como NO VERIFICADO y se deja intacto.

Por default corre en modo DRY-RUN. Pasar `--apply` para renombrar de
verdad."""

from __future__ import annotations

import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database.db import db_historical, init_db_historical
from database.historical.tennis.models import Player
from outer_apis.scrapers.tennis.tennisexplorer_player_scraper import get_match_detail
from scripts.tennis.tennisexplorer_identity import sample_tennisexplorer_match_id

_ABBREVIATED_RE = re.compile(r"^.+ [A-Z]\.$")


def _find_abbreviated_players() -> list[Player]:
    return [p for p in Player.select() if _ABBREVIATED_RE.match(p.canonical_name)]


def _sample_match_id(player: Player) -> tuple[str, str] | None:
    """Delegates to the shared, fail-closed TennisExplorer id selector."""
    return sample_tennisexplorer_match_id(player)


def _verify_full_name(player: Player) -> str | None:
    sample = _sample_match_id(player)
    if sample is None:
        return None
    match_id, side = sample
    try:
        detail = get_match_detail(match_id)
    except Exception:
        return None
    if detail is None:
        return None
    return detail.get(f"player_{side}_name")


def main() -> None:
    apply = "--apply" in sys.argv
    mode = "APLICANDO CAMBIOS" if apply else "DRY-RUN (nada se modifica)"
    print(f"Modo: {mode}\n")

    init_db_historical()
    with db_historical.connection_context():
        players = _find_abbreviated_players()
        print(f"Jugadores con nombre abreviado a verificar: {len(players)}\n")

        n_renamed = 0
        n_needs_merge = 0
        n_unverified = 0

        for p in players:
            full_name = _verify_full_name(p)
            if full_name is None:
                print(f"id={p.id} ({p.canonical_name!r}) -> NO VERIFICADO (sin match_id real o fetch falló)")
                n_unverified += 1
                continue

            if full_name == p.canonical_name:
                # Ya estaba completo pese a matchear el regex (raro, pero
                # posible con nombres de un solo apellido real de 1 letra).
                continue

            collision = Player.get_or_none((Player.canonical_name == full_name) & (Player.id != p.id))
            if collision is not None:
                print(f"id={p.id} ({p.canonical_name!r}) -> REQUIERE FUSIÓN: nombre completo real "
                      f"{full_name!r} ya existe como id={collision.id} -- no se renombra, "
                      f"revisar con verify_and_merge_duplicate_players.py")
                n_needs_merge += 1
                continue

            print(f"id={p.id}: {p.canonical_name!r} -> {full_name!r}")
            if apply:
                p.canonical_name = full_name
                p.save()
            n_renamed += 1

    print(f"\nResumen: renombrados={n_renamed}  requieren_fusion={n_needs_merge}  no_verificados={n_unverified}")
    if not apply:
        print("(dry-run: correr de nuevo con --apply para ejecutar los renombres de verdad)")


if __name__ == "__main__":
    main()
