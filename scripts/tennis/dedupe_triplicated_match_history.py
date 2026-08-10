"""Repara la triplicación real de partidos en `MatchHistory` --
2026-08-10, pedido explícito del usuario tras encontrar el bug en vivo
consultando el H2H/winrate real de Svitolina vs Alexandrova: cada
partido resuelto vía match-detail queda con 1 fila REAL (`match_id`
numérico, `source="tennisexplorer"`) + hasta 2 filas SINTÉTICAS
"recentform:..." (una por cada jugador, escritas por
`save_match_history` desde la perspectiva de cada uno ANTES de que
`save_next_match`/`process_player` resolviera el match-detail real) --
`_find_existing_match_history_row` (dedup existente en `writer.py`) las
debería haber fusionado, pero compara `player_a_name`/`player_b_name`
por IGUALDAD DE STRING EXACTA, y las 2 filas sintéticas de un mismo
partido casi siempre difieren en formato ("Elina Svitolina" vs
"Svitolina Elina", mayúsculas/abreviación del rival distinta según
quién la escribió) -- nunca matchean entre sí ni contra la fila real.

MECANISMO: agrupa TODAS las filas por una clave canónica
`(match_date, apellido_normalizado_lado_1, apellido_normalizado_lado_2)`
-- el apellido se aproxima con el TOKEN MÁS LARGO del nombre (heurística
ya verificada en vivo: robusta contra "Nombre Apellido" vs "Apellido
Nombre" vs "Apellido Inicial.", porque el apellido real casi siempre es
más largo que un nombre de pila corto o una inicial). Dentro de cada
grupo con 2+ filas (mismo partido real duplicado):

  1. Si HAY una fila con `match_id` real (no empieza con "recentform:"
     ni "tmldb:"), se queda -- es la fuente de verdad (identidad
     completa vía match-detail). Antes de borrar las demás, se
     COMPLETAN los campos NULL de la fila ganadora con cualquier valor
     no-nulo real de las filas perdedoras (nunca se pierde un dato ya
     cosechado).
  2. Si NINGUNA es real (2 sintéticas puras, partido aún no resuelto
     vía match-detail), se colapsan en 1 sola fila (se queda la de
     `match_id` sintético lexicográficamente menor, determinístico),
     mismo criterio de fusión de campos.

Fail-closed: grupos ambiguos (3+ filas con match_id REAL distintos --
no debería pasar nunca por diseño, `match_id` es único por partido real
en TennisExplorer, pero si pasara sería una colisión real de datos) se
DEJAN INTACTOS y se reportan aparte -- nunca se borra a ciegas cuando
hay más de 1 candidato "real"."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from database.db import init_db_historical, db_historical
from database.historical.tennis.models import MatchHistory, TennisMatchHistoryUnvalidated

_SYNTHETIC_PREFIXES = ("recentform:", "tmldb:")

# Mismos campos que `_MATCH_DETAIL_COMPARABLE_FIELDS` (writer.py) más
# `level`/`best_of`/`winner_age`/`loser_age`/`minutes` -- cualquier campo
# real del modelo que pueda venir poblado en una fila y NULL en otra.
_MERGEABLE_FIELDS = (
    "source", "tour", "tournament", "round", "surface", "score", "winner_side",
    "player_a_name", "player_a_slug", "player_a_ranking", "player_a_birthdate",
    "player_a_height_cm", "player_a_weight_kg", "player_a_plays",
    "player_b_name", "player_b_slug", "player_b_ranking", "player_b_birthdate",
    "player_b_height_cm", "player_b_weight_kg", "player_b_plays",
    "level", "best_of", "winner_age", "loser_age", "minutes",
)


def _is_synthetic(match_id: str) -> bool:
    return any(match_id.startswith(p) for p in _SYNTHETIC_PREFIXES)


def _surname_guess(full_name: str) -> str:
    """Aproxima el apellido real -- el TOKEN MÁS LARGO del nombre,
    robusto contra los 3 formatos reales vistos en producción ("Nombre
    Apellido", "Apellido Nombre", "Apellido Inicial.") sin necesitar
    saber cuál es. Verificado en vivo (2026-08-10) contra el caso real
    Svitolina/Alexandrova."""
    tokens = [t.strip(".") for t in full_name.replace("-", " ").split() if t.strip(".")]
    return max(tokens, key=len).lower() if tokens else ""


def _canonical_key(row) -> tuple[str, str, str] | None:
    a, b = _surname_guess(row.player_a_name), _surname_guess(row.player_b_name)
    if not (row.match_date and a and b):
        return None
    # orden alfabético -- el mismo partido real puede tener los lados
    # invertidos entre sus filas duplicadas (cada jugador se ve a sí
    # mismo como "player_a").
    side_1, side_2 = sorted((a, b))
    return (row.match_date, side_1, side_2)


def run_dedup() -> dict[str, Any]:
    """Recorre TODA la tabla `MatchHistory` -- full scan, pensado para
    correr UNA VEZ (o cada vez que se sospeche recurrencia del bug, no
    en cada ciclo). Devuelve un resumen real: cuántos grupos duplicados
    se encontraron, cuántas filas se borraron, cuántos grupos quedaron
    ambiguos sin tocar."""
    init_db_historical()
    with db_historical.connection_context():
        rows = list(MatchHistory.select())

    groups: dict[tuple[str, str, str], list] = defaultdict(list)
    skipped_no_key = 0
    for r in rows:
        key = _canonical_key(r)
        if key is None:
            skipped_no_key += 1
            continue
        groups[key].append(r)

    rows_deleted = 0
    groups_deduped = 0
    groups_ambiguous = 0
    fields_merged = 0

    with db_historical.connection_context():
        for key, group_rows in groups.items():
            if len(group_rows) < 2:
                continue

            real_rows = [r for r in group_rows if not _is_synthetic(r.match_id)]
            if len(real_rows) > 1:
                groups_ambiguous += 1
                continue  # 2+ match_id reales distintos para el "mismo" partido -- no se adivina, se deja para revisión manual

            if real_rows:
                winner = real_rows[0]
            else:
                # todas sintéticas -- se queda la de match_id menor (determinístico)
                winner = min(group_rows, key=lambda r: r.match_id)

            losers = [r for r in group_rows if r.id != winner.id]

            changed = False
            for f in _MERGEABLE_FIELDS:
                if getattr(winner, f) is None:
                    for loser in losers:
                        val = getattr(loser, f)
                        if val is not None:
                            setattr(winner, f, val)
                            changed = True
                            fields_merged += 1
                            break
            if changed:
                winner.save()

            for loser in losers:
                loser.delete_instance()
                rows_deleted += 1

            groups_deduped += 1

    result = {
        "rows_scanned": len(rows),
        "rows_skipped_no_key": skipped_no_key,
        "groups_deduped": groups_deduped,
        "groups_ambiguous_skipped": groups_ambiguous,
        "rows_deleted": rows_deleted,
        "fields_merged": fields_merged,
    }
    print(f"[dedupe_triplicated_match_history] {result}", flush=True)
    return result


def prune_resolved_candidates_from_queue() -> dict[str, Any]:
    """2026-08-10, pedido explícito del usuario: "puede ser que las 300k
    filas que no tienen data sean simplemente duplicados de filas de
    matches ya existentes en la DB" -- muchos candidatos de
    `TennisMatchHistoryUnvalidated` (pendientes de resolver vía red)
    pueden corresponder al MISMO partido real que YA tiene una fila con
    identidad completa en `MatchHistory` (`match_id` real, no
    sintético) -- simplemente bajo otro formato de nombre, así que
    nunca matchearon contra `TennisMatchHistory`/`TmlDatabaseMatch` en
    el pipeline de dedupe original mismo llegaron acá.

    Solo se podan candidatos cuya clave canónica coincide con una fila
    REAL (match-detail resuelto) -- NUNCA contra una fila sintética
    "recentform:" (esa SÍ necesita el fetch real para conseguir
    identidad completa; podarla ahí perdería la oportunidad de
    resolverla alguna vez). Sin red -- solo lectura/borrado de DB."""
    init_db_historical()
    with db_historical.connection_context():
        all_rows = list(MatchHistory.select())
    real_keys: set[tuple[str, str, str]] = set()
    for r in all_rows:
        if _is_synthetic(r.match_id):
            continue
        key = _canonical_key(r)
        if key is not None:
            real_keys.add(key)

    with db_historical.connection_context():
        candidates = list(TennisMatchHistoryUnvalidated.select())

    to_delete_ids = []
    for c in candidates:
        opponent_raw = c.player_b_name_or_abbrev
        a_surname = _surname_guess(c.player_a_name)
        b_surname = _surname_guess(opponent_raw)
        if not (c.match_date and a_surname and b_surname):
            continue
        key = (c.match_date, *sorted((a_surname, b_surname)))
        if key in real_keys:
            to_delete_ids.append(c.id)

    if to_delete_ids:
        with db_historical.connection_context():
            # en lotes -- SQLite tiene un límite real de variables por
            # query (999 por default), no se puede pasar todo en un
            # solo IN(...) con listas grandes.
            batch = 500
            for i in range(0, len(to_delete_ids), batch):
                TennisMatchHistoryUnvalidated.delete().where(
                    TennisMatchHistoryUnvalidated.id.in_(to_delete_ids[i:i + batch])
                ).execute()

    result = {
        "queue_candidates_scanned": len(candidates),
        "already_resolved_pruned": len(to_delete_ids),
        "real_match_keys_known": len(real_keys),
    }
    print(f"[dedupe_triplicated_match_history] prune_queue {result}", flush=True)
    return result


if __name__ == "__main__":
    run_dedup()
    prune_resolved_candidates_from_queue()
