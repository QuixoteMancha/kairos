"""Tests del vínculo `MatchStatsSet.match_history` (2026-08-09, pedido
explícito del usuario: "match.stats y match.set_stats" -- mismo patrón
que `MatchStats`/`MatchWeather`, backref nativo de Peewee (sin wrapper
custom, "ya está seleccionado el match y la fila que asocia los sets").

Gap real encontrado en el camino: `MatchStatsSet` nunca se había
registrado en `create_tables()` -- la tabla no existía en producción."""

from __future__ import annotations

from datetime import datetime, timezone

from database.db import init_db_historical
from database.historical.tennis.models import MatchHistory, MatchStatsSet


def setup_function(_fn):
    init_db_historical()
    MatchStatsSet.delete().execute()
    MatchHistory.delete().execute()


def _match():
    return MatchHistory.create(
        match_id="test:1", player_a_name="A", player_b_name="B",
        match_date="2026-01-01", winner_side="a", fetched_at=datetime.now(timezone.utc),
    )


def test_match_set_stats_backref_returns_only_this_match_sets():
    m1 = _match()
    m2 = MatchHistory.create(
        match_id="test:2", player_a_name="C", player_b_name="D",
        match_date="2026-01-02", winner_side="a", fetched_at=datetime.now(timezone.utc),
    )
    MatchStatsSet.create(match_history=m1, set_number=1, aces_a=5)
    MatchStatsSet.create(match_history=m1, set_number=2, aces_a=3)
    MatchStatsSet.create(match_history=m2, set_number=1, aces_a=9)

    sets_m1 = list(m1.set_stats)
    assert len(sets_m1) == 2
    assert {s.set_number for s in sets_m1} == {1, 2}
    assert list(m2.set_stats)[0].aces_a == 9


def test_match_set_stats_first_set_via_filter():
    m = _match()
    MatchStatsSet.create(match_history=m, set_number=1, aces_a=7)
    MatchStatsSet.create(match_history=m, set_number=2, aces_a=2)
    first_set = m.set_stats.where(MatchStatsSet.set_number == 1).first()
    assert first_set.aces_a == 7


def test_match_set_stats_empty_when_no_sets_recorded():
    m = _match()
    assert list(m.set_stats) == []
