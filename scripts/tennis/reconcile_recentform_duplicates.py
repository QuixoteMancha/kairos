"""Reconcile provisional ``recentform:`` rows with canonical matches.

The operation is conservative: a provisional row is removed only when one
and only one real match on the same date confirms both player identities.
Ambiguous and synthetic-only rows are left untouched.
"""

from __future__ import annotations

import argparse

from database.db import db_historical, init_db_historical
from database.historical.tennis.models import MatchHistory, MatchStats, MatchWeather
from database.historical.tennis.writer import _names_confirm_identity


def _same_players(synthetic: MatchHistory, canonical: MatchHistory) -> bool:
    synthetic_names = (
        synthetic.player_a.canonical_name, synthetic.player_b.canonical_name,
    )
    canonical_names = (
        canonical.player_a.canonical_name, canonical.player_b.canonical_name,
    )
    return (
        _names_confirm_identity(synthetic_names[0], canonical_names[0])
        and _names_confirm_identity(synthetic_names[1], canonical_names[1])
    ) or (
        _names_confirm_identity(synthetic_names[0], canonical_names[1])
        and _names_confirm_identity(synthetic_names[1], canonical_names[0])
    )


def reconcile_recentform_duplicates(*, apply: bool = False) -> dict[str, int]:
    init_db_historical()
    summary = {
        "synthetic_total": 0, "matched": 0, "deleted": 0,
        "ambiguous": 0, "synthetic_only": 0, "dependencies_moved": 0,
    }
    with db_historical.connection_context():
        synthetic_rows = list(
            MatchHistory.select().where(
                MatchHistory.match_id.startswith("recentform:")
            ).order_by(MatchHistory.id)
        )
        summary["synthetic_total"] = len(synthetic_rows)
        real_by_date: dict[str | None, list[MatchHistory]] = {}
        for row in MatchHistory.select().where(
            ~(MatchHistory.match_id.startswith("recentform:"))
        ):
            real_by_date.setdefault(row.match_date, []).append(row)

        for synthetic in synthetic_rows:
            candidates = [
                row for row in real_by_date.get(synthetic.match_date, [])
                if _same_players(synthetic, row)
            ]
            if not candidates:
                summary["synthetic_only"] += 1
                continue
            if len(candidates) != 1:
                summary["ambiguous"] += 1
                continue
            summary["matched"] += 1
            if not apply:
                continue

            canonical = candidates[0]
            with db_historical.atomic():
                if synthetic.duration_id and not canonical.duration_id:
                    canonical.duration_id = synthetic.duration_id
                    canonical.save(only=[MatchHistory.duration])
                    summary["dependencies_moved"] += 1

                synthetic_stats = MatchStats.get_or_none(
                    MatchStats.match_history == synthetic.id
                )
                canonical_stats = MatchStats.get_or_none(
                    MatchStats.match_history == canonical.id
                )
                if synthetic_stats and not canonical_stats:
                    synthetic_stats.match_history = canonical.id
                    synthetic_stats.save(only=[MatchStats.match_history])
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

                MatchHistory.delete().where(MatchHistory.id == synthetic.id).execute()
                summary["deleted"] += 1
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply deletions; without this flag the command is a dry run.",
    )
    args = parser.parse_args()
    print(reconcile_recentform_duplicates(apply=args.apply))


if __name__ == "__main__":
    main()
