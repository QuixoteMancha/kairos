"""Migración retroactiva (Fase 1, parte 2) a la entidad canónica
`Player` -- 2026-08-10, pedido explícito del usuario: "los datos de
player deben ir en el modelo de player, y simplemente referenciar a
los players en los matches". `writer.py::_resolve_player_id` ya cubre
las filas NUEVAS (dual-write en `save_match_detail`); este script
llena `player_a_id`/`player_b_id` para las filas YA EXISTENTES en
`MatchHistory` (cientos de miles), sin re-fetchear nada de red -- solo
usa los datos que ya están en la propia tabla.

MECANISMO (pensado para volumen -- NO hace 1 query por fila):
  1. Recolecta la mejor info conocida por NOMBRE EXACTO (string) de
     `player_a_*`/`player_b_*` en toda la tabla -- 1 sola pasada.
  2. Clusteriza esos nombres exactos por identidad REAL: agrupa primero
     por `_approx_identity_key` (apellido+inicial, barato) y DENTRO de
     cada bucket confirma con `_names_confirm_identity` (apellido +
     nombre/inicial completo, evita el caso real Casper/Christian Ruud
     -- mismo apellido, personas distintas) vía union-find -- nunca
     fusiona 2 personas reales distintas solo por compartir la clave
     aproximada.
  3. Por cada cluster (1 persona real), crea 1 fila `Player` con la
     mejor info agregada del cluster completo.
  4. Actualiza `MatchHistory` en LOTES por nombre exacto (no fila por
     fila) -- 1 `UPDATE ... WHERE player_a_name = X` por cada nombre
     distinto, mucho más rápido que iterar 615K filas individualmente."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from database.db import init_db_historical, db_historical
from database.historical.tennis.models import MatchHistory, Player
from database.historical.tennis.writer import _names_confirm_identity, _now
from scripts.tennis.dedupe_triplicated_match_history import _approx_identity_key


def _collect_best_info_by_exact_name() -> dict[str, dict[str, Any]]:
    """1 sola pasada por toda la tabla -- agrega la mejor info conocida
    (primer valor no-nulo encontrado) por cada STRING de nombre exacto
    (no clusteriza todavía, eso es el paso 2)."""
    init_db_historical()
    with db_historical.connection_context():
        rows = list(
            MatchHistory.select(
                MatchHistory.player_a_name, MatchHistory.player_a_slug, MatchHistory.player_a_birthdate,
                MatchHistory.player_a_height_cm, MatchHistory.player_a_weight_kg, MatchHistory.player_a_plays,
                MatchHistory.player_b_name, MatchHistory.player_b_slug, MatchHistory.player_b_birthdate,
                MatchHistory.player_b_height_cm, MatchHistory.player_b_weight_kg, MatchHistory.player_b_plays,
                MatchHistory.tour,
            )
        )

    info_by_name: dict[str, dict[str, Any]] = {}
    for r in rows:
        for prefix in ("a", "b"):
            name = getattr(r, f"player_{prefix}_name")
            if not name:
                continue
            entry = info_by_name.setdefault(
                name, {"slug": None, "birthdate": None, "height_cm": None, "weight_kg": None, "plays": None, "tour": None},
            )
            for field in ("slug", "birthdate", "height_cm", "weight_kg", "plays"):
                val = getattr(r, f"player_{prefix}_{field}")
                if val is not None and entry[field] is None:
                    entry[field] = val
            if r.tour is not None and entry["tour"] is None:
                entry["tour"] = r.tour
    return info_by_name


def _cluster_names_by_identity(names: list[str]) -> list[list[str]]:
    """Agrupa nombres EXACTOS que representan la MISMA persona real --
    union-find dentro de cada bucket de `_approx_identity_key` (barato,
    acota las comparaciones), confirmando con `_names_confirm_identity`
    (apellido + nombre/inicial, no solo apellido) antes de fusionar."""
    buckets: dict[str, list[str]] = defaultdict(list)
    for name in names:
        key = _approx_identity_key(name)
        if key is None:
            key = f"__no_key__:{name}"  # nombre de 1 solo token -- cluster propio, sin fusionar con nadie
        buckets[key].append(name)

    clusters: list[list[str]] = []
    for bucket_names in buckets.values():
        parent = {n: n for n in bucket_names}

        def find(n: str) -> str:
            while parent[n] != n:
                parent[n] = parent[parent[n]]
                n = parent[n]
            return n

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i in range(len(bucket_names)):
            for j in range(i + 1, len(bucket_names)):
                if _names_confirm_identity(bucket_names[i], bucket_names[j]):
                    union(bucket_names[i], bucket_names[j])

        groups: dict[str, list[str]] = defaultdict(list)
        for n in bucket_names:
            groups[find(n)].append(n)
        clusters.extend(groups.values())

    return clusters


def run_migration() -> dict[str, Any]:
    """Punto de entrada real -- fail-closed por diseño: nunca inventa
    un `Player` para un nombre vacío (ya filtrado en la recolección),
    nunca fusiona 2 personas reales distintas (ver `_cluster_names_by_identity`)."""
    info_by_name = _collect_best_info_by_exact_name()
    clusters = _cluster_names_by_identity(list(info_by_name.keys()))

    now = _now()
    name_to_player_id: dict[str, int] = {}
    players_created = 0

    init_db_historical()
    with db_historical.connection_context():
        for cluster in clusters:
            # nombre canónico = el más completo (más tokens) del cluster.
            canonical = max(cluster, key=lambda n: len(n.split()))
            aggregated: dict[str, Any] = {"slug": None, "birthdate": None, "height_cm": None, "weight_kg": None, "plays": None, "tour": None}
            for name in cluster:
                for field, val in info_by_name[name].items():
                    if val is not None and aggregated[field] is None:
                        aggregated[field] = val

            existing = Player.get_or_none(Player.canonical_name == canonical)
            if existing is None:
                player = Player.create(canonical_name=canonical, created_at=now, **aggregated)
                players_created += 1
                player_id = player.id
            else:
                player_id = existing.id

            for name in cluster:
                name_to_player_id[name] = player_id

    # 2026-08-10, fix real de performance (encontrado en producción: 18+
    # minutos sin terminar, matando el proceso) -- `match_history` NO
    # tiene índice en `player_a_name`/`player_b_name` (solo en
    # `match_id`), así que 1 UPDATE por cada uno de los ~32K nombres
    # distintos (2 por nombre, ~63K UPDATEs) hacía un FULL TABLE SCAN
    # de las 619K filas cada vez. Fix: 1 sola lectura de (id +
    # nombres + FK actual) para TODA la tabla, resolver el `Player` en
    # MEMORIA, y actualizar en LOTES por `id` (columna PK, indexada por
    # diseño) -- mismo patrón de batching ya usado en
    # `dedupe_triplicated_match_history.py` para borrados masivos.
    with db_historical.connection_context():
        row_refs = list(
            MatchHistory.select(
                MatchHistory.id, MatchHistory.player_a_name, MatchHistory.player_b_name,
                MatchHistory.player_a_id, MatchHistory.player_b_id,
            )
        )

    ids_by_player_a: dict[int, list[int]] = defaultdict(list)
    ids_by_player_b: dict[int, list[int]] = defaultdict(list)
    for r in row_refs:
        if r.player_a_id is None:
            pid = name_to_player_id.get(r.player_a_name)
            if pid is not None:
                ids_by_player_a[pid].append(r.id)
        if r.player_b_id is None:
            pid = name_to_player_id.get(r.player_b_name)
            if pid is not None:
                ids_by_player_b[pid].append(r.id)

    _BATCH = 500  # límite real de variables por query en SQLite (999 default)
    rows_updated_a = rows_updated_b = 0
    with db_historical.connection_context():
        for pid, ids in ids_by_player_a.items():
            for i in range(0, len(ids), _BATCH):
                rows_updated_a += (
                    MatchHistory.update(player_a_id=pid)
                    .where(MatchHistory.id.in_(ids[i:i + _BATCH]))
                    .execute()
                )
        for pid, ids in ids_by_player_b.items():
            for i in range(0, len(ids), _BATCH):
                rows_updated_b += (
                    MatchHistory.update(player_b_id=pid)
                    .where(MatchHistory.id.in_(ids[i:i + _BATCH]))
                    .execute()
                )

    result = {
        "distinct_names_scanned": len(info_by_name),
        "clusters_found": len(clusters),
        "players_created": players_created,
        "rows_updated_player_a": rows_updated_a,
        "rows_updated_player_b": rows_updated_b,
    }
    print(f"[migrate_players_to_canonical_entity] {result}", flush=True)
    return result


if __name__ == "__main__":
    run_migration()
