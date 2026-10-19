"""Tests de `workers/tennis_match_timing_worker.py` -- 2026-09-05,
captura de hora real de inicio/fin de partidos recién finalizados. No
pega red real: `get_match_timing` se monkeypatchea."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from database.db import init_db_historical
from database.historical.tennis.models import Match, MatchDuration, MatchHistory, Player
from workers import tennis_match_timing_worker as worker


@pytest.fixture(autouse=True)
def clean_tables():
    init_db_historical()
    models = (Match, MatchDuration, MatchHistory, Player)
    for model in models:
        model.delete().execute()
    yield
    for model in models:
        model.delete().execute()


def _now():
    return datetime.now(timezone.utc)


def _match_day() -> str:
    """A real candidate must stay within Flashscore's one-day window."""
    return _now().date().isoformat()


def _ticker_day() -> str:
    return _now().strftime("%y%b%d").upper()


def _match_history():
    a = Player.create(canonical_name="Player A", created_at=_now())
    b = Player.create(canonical_name="Player B", created_at=_now())
    return MatchHistory.create(
        match_id="kalshi:KX-TIMING-1", source="kalshi", match_date=_match_day(),
        player_a=a, player_b=b, fetched_at=_now(),
    )


def _scanned_match(market_id, *, winner="a", flashscore_match_id="fsX", match_history=None):
    return Match.create(
        market_id=market_id, ticker_a="A", ticker_b="B", tour="ATP",
        player_a_name="Player A", player_b_name="Player B", best_of=3,
        close_time=f"{_match_day()}T18:00:00Z", detected_at=_now(), winner=winner,
        flashscore_match_id=flashscore_match_id, match_history=match_history,
    )


_TIMING = {
    "start_at": "2026-09-05T17:10:00+00:00",
    "end_at": "2026-09-05T19:53:57+00:00",
    "duration_minutes": 164,
}


def test_run_once_saves_timing_for_a_real_candidate(monkeypatch):
    history = _match_history()
    _scanned_match(f"KXATPMATCH-{_ticker_day()}AAABBB", match_history=history)
    monkeypatch.setattr(worker, "get_match_timing", lambda fsid: dict(_TIMING))

    result = worker.run_once()

    assert result["saved"] == 1
    assert result["errors"] == 0
    row = MatchHistory.get(MatchHistory.id == history.id)
    assert row.duration is not None
    assert row.duration.start_at is not None


def test_run_once_skips_match_without_flashscore_id(monkeypatch):
    history = _match_history()
    Match.create(
        market_id=f"KXATPMATCH-{_ticker_day()}NOFS", ticker_a="A", ticker_b="B", tour="ATP",
        player_a_name="Player A", player_b_name="Player B", best_of=3,
        close_time=f"{_match_day()}T18:00:00Z", detected_at=_now(), winner="a",
        flashscore_match_id=None, match_history=history,
    )
    monkeypatch.setattr(worker, "get_match_timing", lambda fsid: dict(_TIMING))

    result = worker.run_once()

    assert result["candidates"] == 0
    assert result["saved"] == 0


def test_run_once_skips_unresolved_match(monkeypatch):
    history = _match_history()
    Match.create(
        market_id=f"KXATPMATCH-{_ticker_day()}OPEN", ticker_a="A", ticker_b="B", tour="ATP",
        player_a_name="Player A", player_b_name="Player B", best_of=3,
        close_time=f"{_match_day()}T18:00:00Z", detected_at=_now(), winner=None,
        flashscore_match_id="fsY", match_history=history,
    )
    monkeypatch.setattr(worker, "get_match_timing", lambda fsid: dict(_TIMING))

    result = worker.run_once()

    assert result["candidates"] == 0


def test_run_once_reports_out_of_window_for_old_match(monkeypatch):
    old_day = (_now() - timedelta(days=10)).date()
    history = MatchHistory.create(
        match_id="kalshi:KX-OLD", source="kalshi", match_date=old_day.isoformat(),
        player_a=Player.create(canonical_name="Old A", created_at=_now()),
        player_b=Player.create(canonical_name="Old B", created_at=_now()),
        fetched_at=_now(),
    )
    _scanned_match(f"KXATPMATCH-{old_day.strftime('%y%b%d').upper()}OLDONE", flashscore_match_id="fsOld", match_history=history)
    monkeypatch.setattr(worker, "get_match_timing", lambda fsid: dict(_TIMING))

    result = worker.run_once()

    assert result["out_of_window"] == 1
    assert result["saved"] == 0


def test_run_once_skips_already_done_match(monkeypatch):
    history = _match_history()
    _scanned_match(f"KXATPMATCH-{_ticker_day()}DONE", flashscore_match_id="fsDone", match_history=history)

    calls = []
    monkeypatch.setattr(worker, "get_match_timing", lambda fsid: (calls.append(fsid), dict(_TIMING))[1])
    worker.run_once()
    assert len(calls) == 1

    result = worker.run_once()
    assert result["already_done"] == 1
    assert len(calls) == 1  # no volvió a pedir Flashscore


def test_run_once_reports_not_yet_available(monkeypatch):
    history = _match_history()
    _scanned_match(f"KXATPMATCH-{_ticker_day()}NADA", flashscore_match_id="fsNada", match_history=history)
    monkeypatch.setattr(worker, "get_match_timing", lambda fsid: None)

    result = worker.run_once()

    assert result["not_yet_available"] == 1
    assert result["saved"] == 0
