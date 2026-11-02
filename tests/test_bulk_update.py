from unittest import mock

from peewee import PostgresqlDatabase

from database.bulk_update import bulk_update_by_id
from database.db import db_historical, init_db_historical
from database.historical.tennis import MatchHistory


_FIELDS = (MatchHistory.player_a_elo, MatchHistory.player_a_elo_rank)


def test_postgres_path_batches_one_update_from_values_per_chunk():
    database = PostgresqlDatabase("unused")
    rows = [(1, 1500.0, 3), (2, 1490.5, 7), (3, 1510.25, 1)]
    with mock.patch.object(database, "cursor"), \
         mock.patch("psycopg2.extras.execute_values") as execute_values:
        sent = bulk_update_by_id(database, MatchHistory, _FIELDS, rows, batch_size=2)

    assert sent == 3
    assert [len(call.args[2]) for call in execute_values.call_args_list] == [2, 1]
    sql = execute_values.call_args.args[1]
    assert sql.startswith('UPDATE "match_history" AS t SET "player_a_elo" = v."player_a_elo"')
    assert 'FROM (VALUES %s) AS v("id", "player_a_elo", "player_a_elo_rank")' in sql
    assert sql.endswith('WHERE t."id" = v."id"')
    assert execute_values.call_args.kwargs["template"] == "(%s::INTEGER, %s::DOUBLE PRECISION, %s::INTEGER)"


def test_sqlite_path_updates_only_given_rows():
    from datetime import datetime, timezone
    init_db_historical()
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        now = datetime.now(timezone.utc)
        first = MatchHistory.create(match_id="bulk:1", fetched_at=now, player_a_elo=1.0, player_a_elo_rank=1)
        second = MatchHistory.create(match_id="bulk:2", fetched_at=now, player_a_elo=2.0, player_a_elo_rank=2)

        bulk_update_by_id(db_historical, MatchHistory, _FIELDS, [(first.id, 1600.5, 9)])

        assert MatchHistory.get_by_id(first.id).player_a_elo == 1600.5
        assert MatchHistory.get_by_id(first.id).player_a_elo_rank == 9
        assert MatchHistory.get_by_id(second.id).player_a_elo == 2.0
        MatchHistory.delete().execute()
