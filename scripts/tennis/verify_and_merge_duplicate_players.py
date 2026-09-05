"""Resuelve DEFINITIVAMENTE los casos ambiguos de jugadores duplicados
(mismo `slug` almacenado, pero AMBAS filas con partidos reales) -- 2026-
09-05, pedido explícito del usuario tras encontrar 249 grupos de slug
duplicado en `Player` (209 huérfanos ya borrados en esta misma sesión,
sin necesidad de este script; quedaban 40 grupos donde ambos lados tenían
historial real -- ahí NO se puede asumir con seguridad que sea la misma
persona, ver ejemplos reales encontrados: "Dali Blanch" vs "Darwin
Blanch" son hermanos distintos que coinciden en slug por casualidad).

MÉTODO (pedido explícito del usuario: "revisar los matches en
tennisexplorer para validar a quien corresponden, se puede hacer
directamente con la fecha y torneo"): en vez de confiar en el `slug`
YA guardado (que es justamente lo ambiguo acá), se toma UN `match_id`
real de cada jugador del grupo y se pide `get_match_detail(match_id)`
en vivo -- esa página resuelve la identidad de AMBOS jugadores del
partido "con CERTEZA" (nombre completo + slug REAL desambiguado por
TennisExplorer, no una heurística de apellido, ver docstring de
`get_match_detail`). Si el slug real verificado coincide entre las dos
filas -> es la MISMA persona, partida en dos `Player` por un problema
de formato de nombre -> se fusiona. Si NO coincide -> son personas
DISTINTAS que solo compartían el slug guardado por una colisión de
nuestro propio fallback de slug (ver `_resolve_player_id`/slugify
naive) -- no se toca la separación, pero se corrige el `slug`
almacenado de cada fila al valor real verificado, para que la próxima
corrida de este mismo script ya no las vuelva a agrupar por error.

FUSIÓN (solo cuando el slug real coincide): se reasignan todos los FKs
reales (`MatchHistory.player_a/player_b/winner/player_retired`,
`TennisPlayerStrength`, `TennisPlayerRanking`, `TennisAbstractElo`,
`PlayerStats`, `PlayerRankingHistory`) de la fila MENOR (menos partidos
totales) hacia la fila MAYOR, y se borra la menor. Las relaciones con
índices únicos conservan el snapshot más reciente si ambas filas ya
tenían uno. Nunca se fusiona sin verificación en vivo -- fail-closed:
un grupo cuyo `match_id` no resuelve (fetch falla, header no parsea) se
reporta como NO VERIFICADO y se deja intacto, nunca se asume identidad.

Por default corre en modo DRY-RUN (solo imprime el plan). Pasar
`--apply` para ejecutar los merges/correcciones de verdad.

2026-09-05, bug real encontrado corriendo esto en producción: 11 de los
40 grupos originales quedaban "NO VERIFICADO" de forma consistente entre
corridas (no era flakeo de red) -- el `match_id` de muestra elegido era
un placeholder SINTÉTICO (`"kalshi:KXATPMATCH-..."`, se crea cuando
Kalshi ya detectó el partido pero TennisExplorer todavía no lo publicó),
no fetcheable contra `/match-detail/`. Fix en `_sample_match_id`: excluye
esos ids explícitamente. Con el fix, los grupos que SÍ tenían algún
`match_id` real quedaron resueltos; los que persisten como NO VERIFICADO
tienen el 100% de sus partidos sintéticos (jugador visto solo vía Kalshi,
sin historial publicado todavía) -- límite real de la fuente, no del
script, va a resolverse solo cuando TennisExplorer publique esos
partidos."""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

# Permite tanto ``python3 -m scripts.tennis...`` como ejecutar este archivo
# directamente desde cualquier directorio.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database.db import db_historical, init_db_historical
from database.historical.tennis.models import (
    MatchHistory, Player, PlayerRankingHistory, PlayerStats, TennisAbstractElo,
    TennisPlayerRanking, TennisPlayerStrength,
)
from outer_apis.scrapers.tennis.tennisexplorer_player_scraper import get_match_detail
from scripts.tennis.tennisexplorer_identity import (
    detail_side_for_player, detail_side_for_verified_slug,
    sample_tennisexplorer_match_id,
)


def _find_ambiguous_groups() -> list[list[Player]]:
    """Grupos de `Player` con mismo `slug` donde >=2 filas tienen algún
    partido real (as_a o as_b > 0) -- los huérfanos puros ya no existen
    (se borraron aparte), así que cualquier grupo restante con >1 fila
    real es, por definición, un caso ambiguo."""
    by_slug: dict[str, list[Player]] = defaultdict(list)
    for p in Player.select().where(Player.slug.is_null(False)):
        by_slug[p.slug].append(p)

    groups = []
    for slug, players in by_slug.items():
        if len(players) < 2:
            continue
        with_matches = [p for p in players if _match_count(p) > 0]
        if len(with_matches) >= 2:
            groups.append(with_matches)
    return groups


def _match_count(player: Player) -> int:
    return (MatchHistory.select().where(MatchHistory.player_a == player.id).count()
            + MatchHistory.select().where(MatchHistory.player_b == player.id).count())


def _sample_match_id(player: Player) -> tuple[str, str | None] | None:
    """Un `match_id` REAL de TennisExplorer de `player`, con el lado
    (a/b) en el que aparece. 2026-09-05, bug real encontrado en vivo:
    algunas filas de `MatchHistory` tienen un `match_id` SINTÉTICO
    (`"kalshi:KXATPMATCH-..."`, placeholder que se crea cuando Kalshi ya
    detectó el partido pero TennisExplorer todavía no lo publicó, ver
    `MatchHistory` synthetic rows) -- ese id NO es fetcheable contra
    `/match-detail/`, así que se excluye explícitamente. Fail-closed:
    None si `player` no tiene NINGÚN `match_id` real (todas sus filas
    son sintéticas -- jugador detectado solo vía Kalshi, TennisExplorer
    no lo publicó todavía), nunca se intenta con el placeholder."""
    return sample_tennisexplorer_match_id(player)


def _verify_real_slug(player: Player) -> str | None:
    """Slug REAL verificado en vivo contra TennisExplorer, vía
    `get_match_detail` de un partido real de `player`. Fail-closed:
    None si no hay `match_id` usable, el fetch falla, o el header no
    parsea (nunca se asume)."""
    sample = _sample_match_id(player)
    if sample is None:
        return None
    match_id, side = sample
    try:
        detail = get_match_detail(match_id)
    except Exception:
        # El contrato es fail-closed aun si el cliente HTTP/proxy lanza.
        return None
    if detail is None:
        return None
    verified_side = detail_side_for_player(player, detail)
    if verified_side is None and side is None:
        verified_side = detail_side_for_verified_slug(player, detail)
    if verified_side is None:
        return None
    return detail.get(f"player_{verified_side}_slug")


def _newer(left, right) -> bool:
    """True si ``left`` representa el snapshot más reciente."""
    return left.fetched_at > right.fetched_at


def _merge_strengths(minor: Player, major: Player) -> None:
    """Mueve strengths sin violar la unicidad ``(player, tour)``."""
    for strength in TennisPlayerStrength.select().where(TennisPlayerStrength.player == minor.id):
        existing = TennisPlayerStrength.get_or_none(
            (TennisPlayerStrength.player == major.id)
            & (TennisPlayerStrength.tour == strength.tour)
        )
        if existing is None:
            strength.player = major.id
            strength.save(only=[TennisPlayerStrength.player])
            continue
        if strength.updated_at > existing.updated_at:
            existing.overall_elo = strength.overall_elo
            existing.hard_elo = strength.hard_elo
            existing.clay_elo = strength.clay_elo
            existing.grass_elo = strength.grass_elo
            existing.last_match_date = strength.last_match_date
            existing.updated_at = strength.updated_at
            existing.save()
        strength.delete_instance()


def _merge_ranking_history(minor: Player, major: Player) -> None:
    """Mueve ranking semanal, resolviendo la unicidad ``player+fecha``."""
    for snapshot in PlayerRankingHistory.select().where(PlayerRankingHistory.player == minor.id):
        existing = PlayerRankingHistory.get_or_none(
            (PlayerRankingHistory.player == major.id)
            & (PlayerRankingHistory.ranked_at == snapshot.ranked_at)
        )
        if existing is None:
            snapshot.player = major.id
            snapshot.save(only=[PlayerRankingHistory.player])
            continue
        if _newer(snapshot, existing):
            existing.singles_ranking = snapshot.singles_ranking
            existing.fetched_at = snapshot.fetched_at
            existing.save()
        snapshot.delete_instance()


def _merge(minor: Player, major: Player, apply: bool) -> None:
    print(f"    FUSIONAR: id={minor.id} ({minor.canonical_name!r}, "
          f"{_match_count(minor)} partidos) -> id={major.id} ({major.canonical_name!r}, "
          f"{_match_count(major)} partidos)")
    if not apply:
        return
    with db_historical.atomic():
        MatchHistory.update(player_a=major.id).where(MatchHistory.player_a == minor.id).execute()
        MatchHistory.update(player_b=major.id).where(MatchHistory.player_b == minor.id).execute()
        MatchHistory.update(winner=major.id).where(MatchHistory.winner == minor.id).execute()
        MatchHistory.update(player_retired=major.id).where(MatchHistory.player_retired == minor.id).execute()
        TennisPlayerRanking.update(player=major.id).where(TennisPlayerRanking.player == minor.id).execute()
        PlayerStats.update(player=major.id).where(PlayerStats.player == minor.id).execute()
        _merge_strengths(minor, major)
        # `TennisAbstractElo`/`PlayerRankingHistory` -- 2026-09-05, bug
        # real encontrado antes del primer --apply de esta versión: sus
        # tablas (`tennis_abstract_elo`/`wta_player_ranking_history`) NO
        # existen en producción (modelos huérfanos, ver sus propios
        # docstrings de "eliminada"/nunca migrada). Como este merge corre
        # dentro de `db_historical.atomic()`, tocarlas sin chequeo hacía
        # fallar (rollback) CUALQUIER fusión real, no solo estas dos
        # tablas -- se guardan detrás de `table_exists` para que una
        # migración futura las reactive sin tocar este código.
        if db_historical.table_exists(TennisAbstractElo._meta.table_name):
            TennisAbstractElo.update(player=major.id).where(TennisAbstractElo.player == minor.id).execute()
        if db_historical.table_exists(PlayerRankingHistory._meta.table_name):
            _merge_ranking_history(minor, major)
        # Enriquecimiento progresivo: si la fila mayor tiene campos NULL
        # que la menor sí tenía, se completan antes de borrar la menor.
        changed = False
        for field_name in ("tour", "birthdate", "height_cm", "weight_kg", "plays", "flashscore_participant_id"):
            if getattr(major, field_name) is None and getattr(minor, field_name) is not None:
                setattr(major, field_name, getattr(minor, field_name))
                changed = True
        if changed:
            major.save()
        minor.delete_instance()


def _fix_slug(player: Player, real_slug: str, apply: bool) -> None:
    print(f"    CORREGIR SLUG: id={player.id} ({player.canonical_name!r}) "
          f"slug guardado={player.slug!r} -> real verificado={real_slug!r}")
    if not apply:
        return
    player.slug = real_slug
    player.save()


def _reconcile_verified_group(
    group: list[Player], real_slugs: dict[int, str], apply: bool,
) -> tuple[int, int]:
    """Fusiona cada subgrupo de identidad confirmada y corrige sus slugs.

    Un grupo de slug guardado puede contener ``A, A, B``. La presencia de
    B no debe impedir fusionar las dos filas que TennisExplorer confirmó
    como A.
    """
    by_real_slug: dict[str, list[Player]] = defaultdict(list)
    for player in group:
        by_real_slug[real_slugs[player.id]].append(player)

    merged_identities = 0
    corrected_slugs = 0
    for real_slug, same_identity in by_real_slug.items():
        ordered = sorted(same_identity, key=_match_count, reverse=True)
        major = ordered[0]
        if major.slug != real_slug:
            _fix_slug(major, real_slug, apply)
            corrected_slugs += 1
        if len(ordered) > 1:
            for minor in ordered[1:]:
                _merge(minor, major, apply)
            merged_identities += 1

    return merged_identities, corrected_slugs


def main() -> None:
    apply = "--apply" in sys.argv
    mode = "APLICANDO CAMBIOS" if apply else "DRY-RUN (nada se modifica)"
    print(f"Modo: {mode}\n")

    init_db_historical()
    with db_historical.connection_context():
        groups = _find_ambiguous_groups()
        print(f"Grupos ambiguos a verificar: {len(groups)}\n")

        n_merged = 0
        n_kept_separate = 0
        n_unverified = 0
        n_corrected = 0

        for group in groups:
            names = [p.canonical_name for p in group]
            print(f"=== slug={group[0].slug!r}: {names} ===")

            real_slugs: dict[int, str | None] = {}
            for p in group:
                real_slugs[p.id] = _verify_real_slug(p)
                print(f"  id={p.id} ({p.canonical_name!r}) -> slug real verificado: {real_slugs[p.id]!r}")

            if any(slug is None for slug in real_slugs.values()):
                print("  -> NO VERIFICADO (algún match_id no resolvió contra TennisExplorer), se deja intacto.\n")
                n_unverified += 1
                continue

            verified_slugs = {player_id: slug for player_id, slug in real_slugs.items() if slug is not None}
            merged, corrected = _reconcile_verified_group(group, verified_slugs, apply)
            n_merged += merged
            n_corrected += corrected
            if len(set(verified_slugs.values())) > 1:
                n_kept_separate += 1
                print("  -> Personas distintas confirmadas; se fusionaron solo los subgrupos con slug real idéntico.")
            print()

    print(f"Resumen: identidades_fusionadas={n_merged}  grupos_con_personas_distintas={n_kept_separate}  slugs_corregidos={n_corrected}  no_verificados={n_unverified}")
    if not apply:
        print("\n(dry-run: correr de nuevo con --apply para ejecutar los cambios de verdad)")


if __name__ == "__main__":
    main()
