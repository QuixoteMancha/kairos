"""Reconcile `MatchHistory` rows creadas erróneamente vía el bug real de
`matchtracking.py::_match_date_from_close_time` (fixed 2026-08-28, ver
`resolve_scanned_match_date`) -- `close_time` de mercados KX*MATCH a veces
es la expiración de la SERIE (semanas después del partido real), no el
ticker. Esto causaba que `ensure_match_history_for_scanned_match` buscara
el `MatchHistory` real de TennisExplorer con la fecha EQUIVOCADA, nunca lo
encontrara, y creara un duplicado sintético (`match_id="kalshi:..."`) sin
torneo/superficie/ranking reales.

Mismo patrón conservador que `reconcile_recentform_duplicates.py`: un
duplicado sintético se funde con el real SOLO si hay exactamente un
candidato real (mismos jugadores, fecha REAL recalculada del ticker) --
ambiguo o sin candidato se deja intacto, nunca se adivina. Antes de borrar
el duplicado se mueven sus dependencias CASCADE (`MatchStats`,
`MatchStatsSet`, `MatchWeather`, `TennisHistoricalOdds`) al real, para no
perder datos reales que hayan quedado enganchados al duplicado por error.
"""

from __future__ import annotations

import argparse

from database.db import db_historical, init_db_historical
from database.historical.tennis.matchtracking import resolve_scanned_match_date
from database.historical.tennis.models import (
    Match, MatchHistory, MatchStats, MatchStatsSet, MatchWeather,
    TennisHistoricalOdds,
)
from database.historical.tennis.writer import _names_confirm_identity


def _same_players(synthetic: MatchHistory, canonical: MatchHistory) -> bool:
    synthetic_names = (synthetic.player_a.canonical_name, synthetic.player_b.canonical_name)
    canonical_names = (canonical.player_a.canonical_name, canonical.player_b.canonical_name)
    return (
        _names_confirm_identity(synthetic_names[0], canonical_names[0])
        and _names_confirm_identity(synthetic_names[1], canonical_names[1])
    ) or (
        _names_confirm_identity(synthetic_names[0], canonical_names[1])
        and _names_confirm_identity(synthetic_names[1], canonical_names[0])
    )


def reconcile_kalshi_duplicate_match_history(*, apply: bool = False) -> dict[str, int]:
    init_db_historical()
    summary = {
        "synthetic_total": 0, "no_scanned_match": 0, "date_unresolved": 0,
        "no_candidate_yet": 0, "ambiguous": 0, "matched": 0, "deleted": 0,
        "dependencies_moved": 0, "winner_propagated": 0,
    }
    with db_historical.connection_context():
        synthetic_rows = list(
            MatchHistory.select().where(
                MatchHistory.match_id.startswith("kalshi:")
            ).order_by(MatchHistory.id)
        )
        summary["synthetic_total"] = len(synthetic_rows)
        real_by_date: dict[str | None, list[MatchHistory]] = {}
        for row in MatchHistory.select().where(
            ~(MatchHistory.match_id.startswith("kalshi:"))
        ):
            real_by_date.setdefault(row.match_date, []).append(row)

        for synthetic in synthetic_rows:
            scanned = Match.get_or_none(Match.match_history == synthetic.id)
            if scanned is None:
                summary["no_scanned_match"] += 1
                continue
            real_date = resolve_scanned_match_date(scanned.market_id, scanned.close_time)
            if real_date is None:
                summary["date_unresolved"] += 1
                continue

            candidates = [
                row for row in real_by_date.get(real_date, [])
                if row.id != synthetic.id and _same_players(synthetic, row)
            ]
            if not candidates:
                summary["no_candidate_yet"] += 1
                continue
            if len(candidates) != 1:
                summary["ambiguous"] += 1
                continue
            summary["matched"] += 1
            if not apply:
                continue

            canonical = candidates[0]
            with db_historical.atomic():
                if synthetic.winner_id and not canonical.winner_id:
                    canonical.winner_id = synthetic.winner_id
                    canonical.save(only=[MatchHistory.winner])
                    summary["winner_propagated"] += 1

                synthetic_stats = MatchStats.get_or_none(MatchStats.match_history == synthetic.id)
                canonical_stats = MatchStats.get_or_none(MatchStats.match_history == canonical.id)
                if synthetic_stats and not canonical_stats:
                    synthetic_stats.match_history = canonical.id
                    synthetic_stats.save(only=[MatchStats.match_history])
                    summary["dependencies_moved"] += 1

                canonical_sets = {
                    row.set_number for row in MatchStatsSet.select().where(
                        MatchStatsSet.match_history == canonical.id
                    )
                }
                for set_row in MatchStatsSet.select().where(
                    MatchStatsSet.match_history == synthetic.id
                ):
                    if set_row.set_number not in canonical_sets:
                        set_row.match_history = canonical.id
                        set_row.save(only=[MatchStatsSet.match_history])
                        canonical_sets.add(set_row.set_number)
                        summary["dependencies_moved"] += 1

                canonical_phases = {
                    row.phase for row in MatchWeather.select().where(
                        MatchWeather.match_history == canonical.id
                    )
                }
                for weather in MatchWeather.select().where(
                    MatchWeather.match_history == synthetic.id
                ):
                    if weather.phase not in canonical_phases:
                        weather.match_history = canonical.id
                        weather.save(only=[MatchWeather.match_history])
                        canonical_phases.add(weather.phase)
                        summary["dependencies_moved"] += 1

                for odds in TennisHistoricalOdds.select().where(
                    TennisHistoricalOdds.match_history == synthetic.id
                ):
                    odds.match_history = canonical.id
                    odds.save(only=[TennisHistoricalOdds.match_history])
                    summary["dependencies_moved"] += 1

                scanned.match_history = canonical.id
                scanned.save(only=[Match.match_history])

                MatchHistory.delete().where(MatchHistory.id == synthetic.id).execute()
                summary["deleted"] += 1
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply the merge/deletion; without this flag the command is a dry run.",
    )
    args = parser.parse_args()
    print(reconcile_kalshi_duplicate_match_history(apply=args.apply))


if __name__ == "__main__":
    main()
