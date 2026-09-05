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
`TennisPlayerStrength.player`, `TennisPlayerRanking.player`) de la fila
MENOR (menos partidos totales) hacia la fila MAYOR, y se borra la
menor. Nunca se fusiona sin verificación en vivo -- fail-closed: un
grupo cuyo `match_id` no resuelve (fetch falla, header no parsea) se
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

from peewee import fn

from database.historical.tennis.models import (
    Player, MatchHistory, TennisPlayerStrength, TennisPlayerRanking,
)
from outer_apis.scrapers.tennis.tennisexplorer_player_scraper import get_match_detail


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


def _sample_match_id(player: Player) -> tuple[str, str] | None:
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
    row = (MatchHistory
           .select()
           .where((MatchHistory.player_a == player.id)
                  & MatchHistory.match_id.is_null(False)
                  & ~MatchHistory.match_id.startswith("kalshi:"))
           .first())
    if row is not None:
        return row.match_id, "a"
    row = (MatchHistory
           .select()
           .where((MatchHistory.player_b == player.id)
                  & MatchHistory.match_id.is_null(False)
                  & ~MatchHistory.match_id.startswith("kalshi:"))
           .first())
    if row is not None:
        return row.match_id, "b"
    return None


def _verify_real_slug(player: Player) -> str | None:
    """Slug REAL verificado en vivo contra TennisExplorer, vía
    `get_match_detail` de un partido real de `player`. Fail-closed:
    None si no hay `match_id` usable, el fetch falla, o el header no
    parsea (nunca se asume)."""
    sample = _sample_match_id(player)
    if sample is None:
        return None
    match_id, side = sample
    detail = get_match_detail(match_id)
    if detail is None:
        return None
    return detail.get(f"player_{side}_slug")


def _merge(minor: Player, major: Player, apply: bool) -> None:
    print(f"    FUSIONAR: id={minor.id} ({minor.canonical_name!r}, "
          f"{_match_count(minor)} partidos) -> id={major.id} ({major.canonical_name!r}, "
          f"{_match_count(major)} partidos)")
    if not apply:
        return
    with Player._meta.database.atomic():
        MatchHistory.update(player_a=major.id).where(MatchHistory.player_a == minor.id).execute()
        MatchHistory.update(player_b=major.id).where(MatchHistory.player_b == minor.id).execute()
        MatchHistory.update(winner=major.id).where(MatchHistory.winner == minor.id).execute()
        MatchHistory.update(player_retired=major.id).where(MatchHistory.player_retired == minor.id).execute()
        TennisPlayerStrength.update(player=major.id).where(TennisPlayerStrength.player == minor.id).execute()
        TennisPlayerRanking.update(player=major.id).where(TennisPlayerRanking.player == minor.id).execute()
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


def main() -> None:
    apply = "--apply" in sys.argv
    mode = "APLICANDO CAMBIOS" if apply else "DRY-RUN (nada se modifica)"
    print(f"Modo: {mode}\n")

    groups = _find_ambiguous_groups()
    print(f"Grupos ambiguos a verificar: {len(groups)}\n")

    n_merged = 0
    n_kept_separate = 0
    n_unverified = 0

    for group in groups:
        names = [p.canonical_name for p in group]
        print(f"=== slug={group[0].slug!r}: {names} ===")

        real_slugs: dict[int, str | None] = {}
        for p in group:
            real_slugs[p.id] = _verify_real_slug(p)
            print(f"  id={p.id} ({p.canonical_name!r}) -> slug real verificado: {real_slugs[p.id]!r}")

        if any(s is None for s in real_slugs.values()):
            print("  -> NO VERIFICADO (algún match_id no resolvió contra TennisExplorer), se deja intacto.\n")
            n_unverified += 1
            continue

        by_real_slug: dict[str, list[Player]] = defaultdict(list)
        for p in group:
            by_real_slug[real_slugs[p.id]].append(p)

        if len(by_real_slug) == 1:
            # Misma persona real -- fusionar todo hacia la fila con más partidos.
            ordered = sorted(group, key=_match_count, reverse=True)
            major = ordered[0]
            for minor in ordered[1:]:
                _merge(minor, major, apply)
            n_merged += 1
        else:
            # Personas distintas confirmadas -- no se fusiona, solo se
            # corrige el slug guardado de cada una al real verificado.
            print("  -> Personas DISTINTAS confirmadas por TennisExplorer, no se fusiona.")
            for p in group:
                if p.slug != real_slugs[p.id]:
                    _fix_slug(p, real_slugs[p.id], apply)
            n_kept_separate += 1
        print()

    print(f"Resumen: fusionados={n_merged}  mantenidos_separados={n_kept_separate}  no_verificados={n_unverified}")
    if not apply:
        print("\n(dry-run: correr de nuevo con --apply para ejecutar los cambios de verdad)")


if __name__ == "__main__":
    main()
