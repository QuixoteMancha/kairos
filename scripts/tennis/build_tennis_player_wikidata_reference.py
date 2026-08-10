"""Construye la tabla `TennisPlayerWikidataReference` -- base de verdad
REAL para resolver definitivamente el orden nombre/apellido de
jugadores de tenis (2026-08-10, pedido explícito del usuario: "busca
una lista en wikipedia con nombres de tenistas de atp y wta, todos los
que puedas. Nombre y apellido ese formato, eso dejalo como base para
que después no se armen enredos").

Usa Wikidata (`fetch_all_tennis_players_given_family_names`) en vez de
scrapear HTML de listas de Wikipedia -- `P735`/`P734` son propiedades
ESTRUCTURADAS (nombre de pila / apellido) separadas, así que
"Nombre Apellido" sale de 2 campos reales, nunca de adivinar el orden
de un string suelto (el problema de fondo de toda la saga de
duplicados/reordenes de `Player.canonical_name`).

Dedup por `wikidata_id`: 1 persona puede tener múltiples pares
nombre/apellido en Wikidata (variantes/apodos) -- se queda con el
PRIMER par visto por persona (orden estable de la query, `ORDER BY
?person`), fail-closed simple en vez de intentar adivinar cuál variante
es "la mejor".

Uso:
    python3 -m scripts.tennis.build_tennis_player_wikidata_reference
"""

from __future__ import annotations

from datetime import datetime, timezone

from database.db import init_db_historical, db_historical
from database.historical.tennis.models import TennisPlayerWikidataReference
from outer_apis.scrapers.tennis.wikidata_scraper import (
    fetch_all_tennis_players_given_family_names,
)


def _now():
    return datetime.now(timezone.utc)


def build_reference(max_pages: int | None = None) -> dict:
    raw_rows = fetch_all_tennis_players_given_family_names(max_pages=max_pages)

    by_wikidata_id: dict[str, dict] = {}
    for row in raw_rows:
        wid = row["wikidata_id"]
        if wid not in by_wikidata_id:  # 1er par visto por persona, fail-closed simple
            by_wikidata_id[wid] = row

    now = _now()
    created = 0
    updated = 0
    init_db_historical()
    with db_historical.connection_context():
        for wid, row in by_wikidata_id.items():
            canonical = f"{row['given_name']} {row['family_name']}"
            existing = TennisPlayerWikidataReference.get_or_none(
                TennisPlayerWikidataReference.wikidata_id == wid
            )
            if existing is None:
                TennisPlayerWikidataReference.create(
                    wikidata_id=wid, canonical_name=canonical,
                    given_name=row["given_name"], family_name=row["family_name"],
                    fetched_at=now,
                )
                created += 1
            else:
                existing.canonical_name = canonical
                existing.given_name = row["given_name"]
                existing.family_name = row["family_name"]
                existing.fetched_at = now
                existing.save()
                updated += 1

    result = {
        "raw_rows_fetched": len(raw_rows),
        "distinct_players": len(by_wikidata_id),
        "created": created,
        "updated": updated,
    }
    print(f"[build_tennis_player_wikidata_reference] {result}", flush=True)
    return result


def main() -> None:
    build_reference()


if __name__ == "__main__":
    main()
