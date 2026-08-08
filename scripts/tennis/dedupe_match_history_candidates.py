"""Deduplica `TennisMatchHistory` (2 filas por partido real, una por
jugador) en candidatos de partido ÚNICO, SIN red -- 2026-08-08, pedido
explícito del usuario: "Primero deduplica las filas, es posible?"
(paso previo al backfill masivo de `MatchHistory`).

CRITERIO -- esto es un FILTRO DE EFICIENCIA, nunca la fuente de verdad
de identidad (esa sigue siendo `get_match_detail` contra la página
real, ver ese módulo): agrupa por (match_date, tournament) y empareja
2 filas como "el mismo partido real" solo si la reciprocidad calza en
AMBOS sentidos -- `expected_opponent_abbreviation(row_a.player_name) ==
row_b.opponent_name` Y `expected_opponent_abbreviation(row_b.player_name)
== row_a.opponent_name` -- exigir las 2 direcciones hace que una
colisión falsa (2 partidos reales distintos el mismo día+torneo con
apellidos abreviados coincidentes) sea extremadamente improbable.

Fail-closed ante ambigüedad: si más de 1 fila candidata reciproca en el
mismo bucket, NUNCA se adivina cuál es la correcta -- se reporta como
"conflict" para resolución manual/vía fetch en vivo, no se empareja."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from outer_apis.scrapers.tennis.tennisexplorer_player_scraper import expected_opponent_abbreviation


def find_match_candidates(rows: list[dict[str, Any]]) -> dict[str, list]:
    """`rows`: dicts con al menos player_name/opponent_name/match_date/
    tournament (round/surface/score opcionales, se preservan en el
    candidato si están).

    Returns: {"paired": [...], "singleton": [...], "conflict": [...]}
      - paired: partido con AMBOS nombres completos resueltos por
        reciprocidad real (player_a/player_b, ambos full name).
      - singleton: solo 1 lado backfilleado -- player_a full,
        player_b_abbrev (necesita `get_match_detail` para resolver el
        nombre completo real del rival).
      - conflict: reciprocidad ambigua (2+ candidatos) en el mismo
        bucket -- nunca se adivina, queda para resolución vía fetch en
        vivo por separado."""
    buckets: dict[tuple, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        buckets[(r["match_date"], r.get("tournament"))].append(i)

    consumed = [False] * len(rows)
    paired: list[dict] = []
    singleton: list[dict] = []
    conflict: list[dict] = []

    for i, row in enumerate(rows):
        if consumed[i]:
            continue
        bucket_key = (row["match_date"], row.get("tournament"))
        self_abbrev = expected_opponent_abbreviation(row["player_name"])

        reciprocal_indices = []
        for j in buckets[bucket_key]:
            if j == i or consumed[j]:
                continue
            other = rows[j]
            if other["opponent_name"] != self_abbrev:
                continue
            other_abbrev = expected_opponent_abbreviation(other["player_name"])
            if other_abbrev == row["opponent_name"]:
                reciprocal_indices.append(j)

        if len(reciprocal_indices) == 1:
            j = reciprocal_indices[0]
            consumed[i] = consumed[j] = True
            paired.append({
                "match_date": row["match_date"],
                "tournament": row.get("tournament"),
                "player_a": row["player_name"],
                "player_b": rows[j]["player_name"],
                "round": row.get("round"),
                "surface": row.get("surface"),
                "score_from_a": row.get("score"),
                "result_a": row.get("result"),  # "W"/"L" -- necesario para orientar el score al validar contra Sackmann
            })
        elif len(reciprocal_indices) == 0:
            consumed[i] = True
            singleton.append({
                "match_date": row["match_date"],
                "tournament": row.get("tournament"),
                "player_a": row["player_name"],
                "player_b_abbrev": row["opponent_name"],
                "round": row.get("round"),
                "surface": row.get("surface"),
                "score_from_a": row.get("score"),
                "result_a": row.get("result"),
            })
        else:
            consumed[i] = True
            conflict.append({
                "match_date": row["match_date"],
                "tournament": row.get("tournament"),
                "player_a": row["player_name"],
                "opponent_abbrev": row["opponent_name"],
                "candidate_count": len(reciprocal_indices),
            })

    return {"paired": paired, "singleton": singleton, "conflict": conflict}


def run_dedup_report() -> dict:
    """Corre el dedup contra la `TennisMatchHistory` REAL (solo
    lectura, no escribe nada) y devuelve un resumen con conteos."""
    from database.db import init_db_historical, db_historical
    from database.historical.tennis import TennisMatchHistory

    init_db_historical()
    with db_historical.connection_context():
        rows = [
            {
                "player_name": r.player_name, "opponent_name": r.opponent_name,
                "match_date": r.match_date, "tournament": r.tournament,
                "round": r.round, "surface": r.surface, "score": r.score, "result": r.result,
            }
            for r in TennisMatchHistory.select(
                TennisMatchHistory.player_name, TennisMatchHistory.opponent_name,
                TennisMatchHistory.match_date, TennisMatchHistory.tournament,
                TennisMatchHistory.round, TennisMatchHistory.surface, TennisMatchHistory.score,
                TennisMatchHistory.result,
            )
        ]

    result = find_match_candidates(rows)
    summary = {
        "total_rows": len(rows),
        "paired_matches": len(result["paired"]),
        "singleton_matches": len(result["singleton"]),
        "conflicts": len(result["conflict"]),
        "unique_match_candidates_total": len(result["paired"]) + len(result["singleton"]) + len(result["conflict"]),
    }
    print(f"[dedupe_match_history_candidates] {summary}", flush=True)
    return summary


if __name__ == "__main__":
    import json
    print(json.dumps(run_dedup_report(), indent=2, ensure_ascii=False))
