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

MISMO patrón real en `TennisMatchHistoryUnvalidated`: el mismo partido
real, sin resolver todavía, puede quedar en la cola como 2 candidatos
DISTINTOS -- uno generado desde la página de cada jugador (cada uno se
ve a sí mismo como "player_a"), sin que el dedupe original de esa cola
los reconozca como el mismo partido.

VERIFICACIÓN DE IDENTIDAD (2026-08-10, pedido explícito del usuario:
"añade verificación más avanzada de deduplicación"): comparar SOLO el
apellido (heurística "token más largo") es insuficiente -- 2 personas
reales pueden compartir apellido (ver
kairos-critical-misattribution-bug-2026-08-06, "Casper Ruud" vs
"Christian Ruud", padre/hijo, mismo apellido exacto). `_names_confirm_
identity` exige apellido Y AL MENOS 1 token de nombre/inicial
coincidente (no solo apellido), tolerante a abreviación ("E." matchea
"Ekaterina") y a las 2 convenciones de orden reales del proyecto
("Nombre Apellido" / "Apellido Nombre") sin necesitar saber cuál es
cada lado.

ORDEN DE EJECUCIÓN (pedido explícito): `dedupe_unvalidated_queue()`
PRIMERO (fusiona candidatos reciprocos dentro de la cola, sin tocar
`MatchHistory`), después `run_dedup()` (triplicación real en
`MatchHistory`) y `prune_resolved_candidates_from_queue()` (poda contra
`MatchHistory` ya limpia)."""

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

_UNVALIDATED_MERGEABLE_FIELDS = ("tournament", "round", "surface", "score_from_a", "reason")


def _is_synthetic(match_id: str) -> bool:
    return any(match_id.startswith(p) for p in _SYNTHETIC_PREFIXES)


def _tokens(name: str) -> list[str]:
    return [t.strip(".") for t in name.replace("-", " ").split() if t.strip(".")]


def _surname_guess(full_name: str) -> str:
    """Aproxima el apellido real -- el TOKEN MÁS LARGO del nombre,
    robusto contra los 3 formatos reales vistos en producción ("Nombre
    Apellido", "Apellido Nombre", "Apellido Inicial.") sin necesitar
    saber cuál es. Verificado en vivo (2026-08-10) contra el caso real
    Svitolina/Alexandrova."""
    tokens = _tokens(full_name)
    return max(tokens, key=len).lower() if tokens else ""


def _names_confirm_identity(name_a: str, name_b: str) -> bool:
    """Verificación de identidad MÁS ESTRICTA que solo-apellido --
    2026-08-10, pedido explícito del usuario ("verificación más
    avanzada de deduplicación"): exige que el APELLIDO coincida Y que
    exista AL MENOS 1 token de nombre/inicial en común (igualdad
    completa o coincidencia de inicial) -- nunca confía en el apellido
    solo, evita colisiones reales de personas distintas con el mismo
    apellido (ver kairos-critical-misattribution-bug-2026-08-06: "Casper
    Ruud" vs "Christian Ruud", padre/hijo). Fail-closed: si a alguno de
    los 2 lados le falta el nombre de pila por completo (nombre de 1
    solo token), no se confirma -- no hay suficiente información para
    descartar una colisión real."""
    tokens_a, tokens_b = _tokens(name_a), _tokens(name_b)
    if not tokens_a or not tokens_b:
        return False
    surname_a = max(tokens_a, key=len).lower()
    surname_b = max(tokens_b, key=len).lower()
    if surname_a != surname_b:
        return False
    given_a = [t.lower() for t in tokens_a if t.lower() != surname_a]
    given_b = [t.lower() for t in tokens_b if t.lower() != surname_b]
    if not given_a or not given_b:
        return False
    for ga in given_a:
        for gb in given_b:
            if ga == gb or (len(ga) == 1 and gb.startswith(ga)) or (len(gb) == 1 and ga.startswith(gb)):
                return True
    return False


def _canonical_key(row) -> tuple[str, str, str] | None:
    a, b = _surname_guess(row.player_a_name), _surname_guess(row.player_b_name)
    if not (row.match_date and a and b):
        return None
    # orden alfabético -- el mismo partido real puede tener los lados
    # invertidos entre sus filas duplicadas (cada jugador se ve a sí
    # mismo como "player_a").
    side_1, side_2 = sorted((a, b))
    return (row.match_date, side_1, side_2)


def dedupe_unvalidated_queue() -> dict[str, Any]:
    """PASO 1 (pedido explícito del usuario: correr esto primero) --
    fusiona candidatos RECÍPROCOS dentro de `TennisMatchHistoryUnvalidated`:
    el mismo partido real, sin resolver, puede haber quedado como 2
    filas -- una generada desde la página de cada jugador. Verificación
    vía `_names_confirm_identity` (apellido + nombre/inicial) en ambas
    direcciones: `c1.player_a_name` debe confirmar contra el rival de
    `c2`, Y `c2.player_a_name` debe confirmar contra el rival de `c1`.

    Se queda con el candidato MÁS COMPLETO (rival en nombre completo >
    abreviado; más campos no-nulos como desempate) y fusiona los campos
    faltantes desde el descartado antes de borrarlo -- nunca se pierde
    información ya cosechada."""
    init_db_historical()
    with db_historical.connection_context():
        candidates = list(TennisMatchHistoryUnvalidated.select())

    by_date: dict[str, list] = defaultdict(list)
    for c in candidates:
        if c.match_date:
            by_date[c.match_date].append(c)

    to_delete_ids: list[int] = []
    fields_merged = 0
    pairs_merged = 0
    deleted_ids: set[int] = set()

    with db_historical.connection_context():
        for date, group in by_date.items():
            n = len(group)
            for i in range(n):
                c1 = group[i]
                if c1.id in deleted_ids:
                    continue
                for j in range(i + 1, n):
                    c2 = group[j]
                    if c2.id in deleted_ids:
                        continue
                    if not (
                        _names_confirm_identity(c1.player_a_name, c2.player_b_name_or_abbrev)
                        and _names_confirm_identity(c2.player_a_name, c1.player_b_name_or_abbrev)
                    ):
                        continue

                    # más completo: rival en nombre completo gana sobre abreviado.
                    if c1.player_b_is_full_name and not c2.player_b_is_full_name:
                        winner, loser = c1, c2
                    elif c2.player_b_is_full_name and not c1.player_b_is_full_name:
                        winner, loser = c2, c1
                    else:
                        winner, loser = (c1, c2) if c1.id < c2.id else (c2, c1)

                    changed = False
                    for f in _UNVALIDATED_MERGEABLE_FIELDS:
                        if getattr(winner, f) is None and getattr(loser, f) is not None:
                            setattr(winner, f, getattr(loser, f))
                            changed = True
                            fields_merged += 1
                    if changed:
                        winner.save()

                    loser.delete_instance()
                    deleted_ids.add(loser.id)
                    to_delete_ids.append(loser.id)
                    pairs_merged += 1
                    break  # c1 ya fusionado con su reciprocidad -- no seguir buscando más matches para c1

    result = {
        "candidates_scanned": len(candidates),
        "reciprocal_pairs_merged": pairs_merged,
        "rows_deleted": len(to_delete_ids),
        "fields_merged": fields_merged,
    }
    print(f"[dedupe_triplicated_match_history] dedupe_queue {result}", flush=True)
    return result


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
    sintético) -- simplemente bajo otro formato de nombre.

    VERIFICACIÓN AVANZADA (2026-08-10, pedido explícito): en vez de solo
    comparar apellidos, cada candidato se valida contra las filas reales
    de la MISMA fecha con `_names_confirm_identity` en ambos lados (el
    `player_a_name` del candidato -- SIEMPRE nombre completo -- contra
    un lado de la fila real, y el rival del candidato -- completo o
    abreviado -- contra el otro lado) -- exige apellido Y nombre/inicial
    coincidentes, no solo apellido, mismo criterio que `dedupe_unvalidated_
    queue`. Solo se podan candidatos que confirman contra una fila REAL
    (no sintética) -- esa SÍ necesita el fetch real para conseguir
    identidad completa. Sin red -- solo lectura/borrado de DB."""
    init_db_historical()
    with db_historical.connection_context():
        all_rows = list(MatchHistory.select())
    real_by_date: dict[str, list] = defaultdict(list)
    for r in all_rows:
        if _is_synthetic(r.match_id) or not r.match_date:
            continue
        real_by_date[r.match_date].append(r)

    with db_historical.connection_context():
        candidates = list(TennisMatchHistoryUnvalidated.select())

    to_delete_ids = []
    for c in candidates:
        if not c.match_date:
            continue
        for real_row in real_by_date.get(c.match_date, ()):
            for known_side, other_side in (
                (real_row.player_a_name, real_row.player_b_name),
                (real_row.player_b_name, real_row.player_a_name),
            ):
                if _names_confirm_identity(c.player_a_name, known_side) and _names_confirm_identity(
                    c.player_b_name_or_abbrev, other_side,
                ):
                    to_delete_ids.append(c.id)
                    break
            else:
                continue
            break

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
        "real_rows_indexed": len(all_rows),
    }
    print(f"[dedupe_triplicated_match_history] prune_queue {result}", flush=True)
    return result


if __name__ == "__main__":
    dedupe_unvalidated_queue()
    run_dedup()
    prune_resolved_candidates_from_queue()
