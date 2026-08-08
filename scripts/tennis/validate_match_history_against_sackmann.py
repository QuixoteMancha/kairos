"""Valida los candidatos de `dedupe_match_history_candidates.py` contra
`TmlDatabaseMatch` (Sackmann/TML-Database, YA en nuestra base, SIN red)
-- 2026-08-08, pedido explícito del usuario: "Validaremos los matches
de tennisexplorer con la data de sackmann histórica para validar la
deduplicación, y los que no se puedan validar los dejamos en una tabla
aparte".

POR QUÉ SIN RED: Sackmann trae nombre completo real + ranking + edad de
AMBOS jugadores para cada partido, ya cargado en producción (197,998
filas) -- cruzarlo es gratis comparado con re-fetchear ~350K páginas de
TennisExplorer (~50h mínimo por el throttle global, ver sesión previa).

CRITERIO DE MATCH (nunca solo por torneo -- `TmlDatabaseMatch.date` es
la fecha de INICIO del torneo, no la fecha real del partido, bug ya
documentado -- así que NO se usa como clave de join):
  1. Mismo AÑO + mismos 2 nombres completos (o 1 nombre completo + su
     abreviación esperada resuelta, para candidatos "singleton").
  2. Si hay 2+ partidos candidatos entre las mismas 2 personas el mismo
     año (real, confirmado en producción -- se encuentran más de una
     vez por temporada), se desambigua por SCORE -- comparando sets
     tolerando separador (coma vs espacio) y orientación (winner-first
     en Sackmann vs player_a-first en TennisExplorer, reorientado según
     `result_a`).

Fail-closed: 0 candidatos o 2+ candidatos sin que el score desempate a
exactamente 1 -> NO se valida, va a `TennisMatchHistoryUnvalidated`."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from outer_apis.scrapers.tennis.tennisexplorer_player_scraper import expected_opponent_abbreviation


def _parse_sets(score: str | None) -> list[tuple[int, int]]:
    """Extrae sets (a, b) de un score en CUALQUIER formato real visto
    en este proyecto -- coma (TennisExplorer, "6-4, 6-2") o espacio
    (Sackmann, "6-4 6-2"), ignorando tiebreak/sufijos no numéricos
    ("7-6(4)" -> (7,6), "6-2 3-0 RET" -> [(6,2),(3,0)]). Fail-closed:
    [] si `score` es None/vacío o no hay ningún set parseable."""
    if not score:
        return []
    sets = []
    for token in re.split(r"[,\s]+", score.strip()):
        m = re.match(r"^(\d+)-(\d+)", token)
        if m:
            sets.append((int(m.group(1)), int(m.group(2))))
    return sets


def scores_match(sackmann_score: str | None, score_from_a: str | None, a_is_winner: bool | None) -> bool:
    """True solo si los sets coinciden EXACTO tras reorientar
    `score_from_a` (player_a-first) a winner-first (igual que Sackmann)
    -- `a_is_winner` viene de `result_a` ("W"/"L") ya conocido en
    `TennisMatchHistory`. Fail-closed: False si falta cualquier dato
    (nunca se asume un match de score sin poder compararlo de verdad)."""
    if a_is_winner is None:
        return False
    sackmann_sets = _parse_sets(sackmann_score)
    a_sets = _parse_sets(score_from_a)
    if not sackmann_sets or not a_sets:
        return False
    if not a_is_winner:
        a_sets = [(b, a) for a, b in a_sets]
    return sackmann_sets == a_sets


def build_sackmann_indexes(tml_rows: list[dict[str, Any]]) -> tuple[dict, dict]:
    """`tml_rows`: dicts con year/winner_name/loser_name/winner_rank/
    winner_age/loser_rank/loser_age/round/score/tourney_name.

    Returns (by_pair, by_name):
      - by_pair[(year, frozenset({winner.lower(), loser.lower()}))] ->
        list de filas (para candidatos "paired", ambos nombres ya
        conocidos).
      - by_name[(year, name.lower())] -> list de filas donde ese nombre
        jugó como winner O loser (para candidatos "singleton", solo se
        conoce 1 lado)."""
    by_pair: dict[tuple, list[dict]] = defaultdict(list)
    by_name: dict[tuple, list[dict]] = defaultdict(list)
    for r in tml_rows:
        year = r["year"]
        w, l = r["winner_name"], r["loser_name"]
        by_pair[(year, frozenset({w.lower(), l.lower()}))].append(r)
        by_name[(year, w.lower())].append(r)
        by_name[(year, l.lower())].append(r)
    return dict(by_pair), dict(by_name)


def _year_of(match_date: str) -> int | None:
    m = re.match(r"^(\d{4})-", match_date or "")
    return int(m.group(1)) if m else None


def _bio_from_sackmann_row(row: dict, a_is_winner: bool) -> dict:
    if a_is_winner:
        return {
            "player_a_ranking": row.get("winner_rank"), "player_a_age": row.get("winner_age"),
            "player_b_ranking": row.get("loser_rank"), "player_b_age": row.get("loser_age"),
        }
    return {
        "player_a_ranking": row.get("loser_rank"), "player_a_age": row.get("loser_age"),
        "player_b_ranking": row.get("winner_rank"), "player_b_age": row.get("winner_age"),
    }


def validate_paired(paired: list[dict], by_pair: dict) -> tuple[list[dict], list[dict]]:
    """Returns (validated, unvalidated) -- validated trae bio real de
    Sackmann (ranking/edad de ambos, al momento del partido)."""
    validated, unvalidated = [], []
    for c in paired:
        year = _year_of(c["match_date"])
        candidates = by_pair.get((year, frozenset({c["player_a"].lower(), c["player_b"].lower()})), [])
        if len(candidates) == 1:
            row = candidates[0]
            a_is_winner = row["winner_name"].lower() == c["player_a"].lower()
            validated.append({**c, "sackmann_row": row, "a_is_winner": a_is_winner,
                               **_bio_from_sackmann_row(row, a_is_winner)})
            continue
        if len(candidates) > 1:
            a_is_winner = c.get("result_a") == "W"
            matches = [
                row for row in candidates
                if scores_match(row.get("score"), c.get("score_from_a"), a_is_winner)
            ]
            if len(matches) == 1:
                row = matches[0]
                validated.append({**c, "sackmann_row": row, "a_is_winner": a_is_winner,
                                   **_bio_from_sackmann_row(row, a_is_winner)})
                continue
        unvalidated.append({**c, "reason": "no_sackmann_match" if not candidates else "ambiguous_sackmann_match"})
    return validated, unvalidated


def validate_singleton(singleton: list[dict], by_name: dict) -> tuple[list[dict], list[dict]]:
    """Para cada candidato, busca en Sackmann partidos de `player_a` ese
    año donde el RIVAL abrevie a `player_b_abbrev` -- si resuelve a
    exactamente 1, el nombre completo del rival queda confirmado (y
    valida el candidato) sin ninguna adivinanza."""
    validated, unvalidated = [], []
    for c in singleton:
        year = _year_of(c["match_date"])
        rows = by_name.get((year, c["player_a"].lower()), [])
        matches = []
        for row in rows:
            a_is_winner = row["winner_name"].lower() == c["player_a"].lower()
            other_name = row["loser_name"] if a_is_winner else row["winner_name"]
            if expected_opponent_abbreviation(other_name) == c["player_b_abbrev"]:
                matches.append((row, a_is_winner, other_name))
        if len(matches) == 1:
            row, a_is_winner, other_name = matches[0]
            validated.append({**c, "player_b": other_name, "sackmann_row": row, "a_is_winner": a_is_winner,
                               **_bio_from_sackmann_row(row, a_is_winner)})
            continue
        if len(matches) > 1:
            a_is_winner = c.get("result_a") == "W"
            score_matches = [
                (row, other_name) for row, _aw, other_name in matches
                if scores_match(row.get("score"), c.get("score_from_a"), a_is_winner)
            ]
            if len(score_matches) == 1:
                row, other_name = score_matches[0]
                validated.append({**c, "player_b": other_name, "sackmann_row": row, "a_is_winner": a_is_winner,
                                   **_bio_from_sackmann_row(row, a_is_winner)})
                continue
        unvalidated.append({**c, "reason": "no_sackmann_match" if not matches else "ambiguous_sackmann_match"})
    return validated, unvalidated


def run_validation() -> dict:
    """Corre dedup + validación contra Sackmann sobre la base REAL,
    escribe resultados: validados -> `MatchHistory` (source=
    "sackmann"), no validados (incl. los `conflict` del dedup mismo) ->
    `TennisMatchHistoryUnvalidated`. Idempotente en ambos casos."""
    from datetime import datetime, timezone

    from database.db import init_db_historical, db_historical
    from database.historical.tennis import (
        TennisMatchHistory, TmlDatabaseMatch, MatchHistory, TennisMatchHistoryUnvalidated,
    )
    from scripts.tennis.dedupe_match_history_candidates import find_match_candidates

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
        tml_rows = [
            {
                "id": r.id, "year": r.year, "winner_name": r.winner_name, "loser_name": r.loser_name,
                "winner_rank": r.winner_rank, "winner_age": r.winner_age,
                "loser_rank": r.loser_rank, "loser_age": r.loser_age,
                "score": r.score, "tourney_name": r.tourney_name,
            }
            for r in TmlDatabaseMatch.select()
        ]

    dedup_result = find_match_candidates(rows)
    by_pair, by_name = build_sackmann_indexes(tml_rows)

    paired_validated, paired_unvalidated = validate_paired(dedup_result["paired"], by_pair)
    singleton_validated, singleton_unvalidated = validate_singleton(dedup_result["singleton"], by_name)

    now = datetime.now(timezone.utc)
    created = updated = unchanged = 0
    with db_historical.connection_context():
        for c in paired_validated + singleton_validated:
            row = c["sackmann_row"]
            # 2026-08-08, bug real encontrado y arreglado: el id sintético
            # anterior (`sackmann:{winner}|{loser}|{year}`) NO era único
            # cuando 2 personas se enfrentan más de una vez el mismo año
            # (confirmado real en producción, ver `pares que se repiten
            # mismo año` en la investigación) -- causó 2,983 colisiones
            # silenciosas (partidos reales distintos pisándose entre sí)
            # en la primera corrida. Fix: usar el PK real (autoincrement,
            # único por diseño) de la fila de `TmlDatabaseMatch`.
            match_id = f"sackmann:{row['id']}"
            fields = {
                "source": "sackmann",
                "match_date": c["match_date"], "tournament": c.get("tournament"),
                "round": c.get("round"), "surface": c.get("surface"),
                "score": row.get("score"),
                "winner_side": "a" if c["a_is_winner"] else "b",
                "player_a_name": c["player_a"], "player_a_ranking": c.get("player_a_ranking"),
                "player_b_name": c["player_b"], "player_b_ranking": c.get("player_b_ranking"),
            }
            existing = MatchHistory.get_or_none(MatchHistory.match_id == match_id)
            if existing is None:
                MatchHistory.create(match_id=match_id, fetched_at=now, **fields)
                created += 1
            else:
                changed = False
                for f, v in fields.items():
                    if getattr(existing, f) != v:
                        setattr(existing, f, v)
                        changed = True
                if changed:
                    existing.updated_at = now
                    existing.save()
                    updated += 1
                else:
                    unchanged += 1

        unvalidated_all = (
            paired_unvalidated + singleton_unvalidated
            + [{**c, "player_b_abbrev": c["opponent_abbrev"], "reason": "dedup_conflict"} for c in dedup_result["conflict"]]
        )
        unvalidated_created = unvalidated_unchanged = 0
        for c in unvalidated_all:
            player_b_field = c.get("player_b") or c.get("player_b_abbrev")
            exists = TennisMatchHistoryUnvalidated.select().where(
                (TennisMatchHistoryUnvalidated.match_date == c["match_date"])
                & (TennisMatchHistoryUnvalidated.tournament == c.get("tournament"))
                & (TennisMatchHistoryUnvalidated.player_a_name == c["player_a"])
                & (TennisMatchHistoryUnvalidated.player_b_name_or_abbrev == player_b_field)
            ).exists()
            if exists:
                unvalidated_unchanged += 1
                continue
            TennisMatchHistoryUnvalidated.create(
                match_date=c["match_date"], tournament=c.get("tournament"),
                round=c.get("round"), surface=c.get("surface"), score_from_a=c.get("score_from_a"),
                player_a_name=c["player_a"], player_b_name_or_abbrev=player_b_field,
                player_b_is_full_name="player_b" in c, reason=c["reason"], fetched_at=now,
            )
            unvalidated_created += 1

    summary = {
        "dedup": {k: len(v) for k, v in dedup_result.items()},
        "paired_validated": len(paired_validated), "paired_unvalidated": len(paired_unvalidated),
        "singleton_validated": len(singleton_validated), "singleton_unvalidated": len(singleton_unvalidated),
        "match_detail_created": created, "match_detail_updated": updated, "match_detail_unchanged": unchanged,
        "unvalidated_created": unvalidated_created, "unvalidated_already_existed": unvalidated_unchanged,
    }
    print(f"[validate_match_history_against_sackmann] {summary}", flush=True)
    return summary


if __name__ == "__main__":
    import json
    print(json.dumps(run_validation(), indent=2, ensure_ascii=False))
