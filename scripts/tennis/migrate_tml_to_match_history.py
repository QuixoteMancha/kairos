"""Migra TODO `TmlDatabaseMatch` (Sackmann/TML-Database) a la tabla
única `MatchHistory` -- 2026-08-08, pedido explícito del usuario:
"migraremos todo lo de TML a una única base que se llamará matchhistory,
no puede haber ninguna otra base con matches".

A diferencia de `validate_match_history_against_sackmann.py` (que
cruza candidatos de `TennisMatchHistory` contra Sackmann para
resolverles identidad), esto NO depende de `TennisMatchHistory` en
absoluto: cada fila de `TmlDatabaseMatch` YA tiene identidad completa y
cierta por sí sola (winner_name/loser_name reales, nunca abreviados),
así que se copia directo.

Idempotente por `match_id = f"sackmann:{tml_row.id}"` -- el PK real
(autoincrement, único por diseño) de `TmlDatabaseMatch`, NO una
combinación de nombre+año (ese esquema anterior colisionaba cuando 2
personas se enfrentaban más de una vez el mismo año, ver
`validate_match_history_against_sackmann.py` para el bug real
encontrado y corregido). Re-correr esto no duplica ni pisa partidos
reales distintos entre sí.

CAVEAT conocido y documentado: `TmlDatabaseMatch.date` es la fecha de
INICIO del torneo, no la fecha real del partido (bug de la fuente ya
investigado en sesiones previas) -- se copia tal cual a
`MatchHistory.match_date` por ser la mejor aproximación disponible para
estas filas (sin cruce contra TennisExplorer no hay fecha más precisa),
pero quien consuma esa columna debe saber que es aproximada para las
filas `source="sackmann"`.

ORDEN DE CORRIDA -- IMPORTANTE, regresión real encontrada 2026-08-08:
correr ESTE script (`migrate_all_tml_rows`, cobertura masiva, fecha
aproximada de torneo) DESPUÉS de
`validate_match_history_against_sackmann.py::run_validation` (más
preciso: fecha real de partido + torneo real, cruzado contra
TennisExplorer) SOBRESCRIBE esos ~24,853 partidos ya refinados con la
fecha/torneo menos precisos, perdiendo la mejora. El orden correcto es
SIEMPRE: (1) este script primero (base completa), (2)
`validate_match_history_against_sackmann.py` después (refina el
subconjunto que cruza con TennisExplorer). Si se corre en el orden
equivocado, re-correr (2) al final para restaurar la precisión --
ambos son idempotentes por el mismo esquema de `match_id`, así que
"reparar" el orden es seguro."""

from __future__ import annotations

from datetime import datetime, timezone


def migrate_all_tml_rows() -> dict:
    from database.db import init_db_historical, db_historical
    from database.historical.tennis import TmlDatabaseMatch, MatchHistory

    init_db_historical()
    now = datetime.now(timezone.utc)
    created = updated = unchanged = skipped_invalid = 0

    with db_historical.connection_context():
        # Materializar TODO en memoria antes de escribir -- 2026-08-08,
        # bug real encontrado en producción: `.iterator()` (streaming)
        # mantiene un cursor de lectura abierto sobre la misma conexión
        # mientras se hacen los INSERT/UPDATE de abajo, y SQLite tira
        # "database is locked" -- 197,998 filas caben cómodo en memoria,
        # no hace falta streaming acá.
        tml_rows = list(TmlDatabaseMatch.select())
        total = len(tml_rows)

    with db_historical.connection_context():
        for row in tml_rows:
            if not row.winner_name or not row.loser_name:
                skipped_invalid += 1
                continue
            match_id = f"sackmann:{row.id}"
            fields = {
                "source": "sackmann",
                "tour": "ATP",  # TmlDatabaseMatch es ATP-only por diseño, ver docstring del módulo
                "match_date": row.date, "tournament": row.tourney_name,
                "round": row.round, "surface": row.surface, "score": row.score,
                "winner_side": "a",
                "player_a_name": row.winner_name, "player_a_ranking": row.winner_rank,
                "player_b_name": row.loser_name, "player_b_ranking": row.loser_rank,
            }
            existing = MatchHistory.get_or_none(MatchHistory.match_id == match_id)
            if existing is None:
                MatchHistory.create(match_id=match_id, fetched_at=now, **fields)
                created += 1
                continue
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

    summary = {
        "tml_total_rows": total, "created": created, "updated": updated,
        "unchanged": unchanged, "skipped_invalid": skipped_invalid,
    }
    print(f"[migrate_tml_to_match_history] {summary}", flush=True)
    return summary


if __name__ == "__main__":
    import json
    print(json.dumps(migrate_all_tml_rows(), indent=2, ensure_ascii=False))
