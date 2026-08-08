"""Tests del import del Match Charting Project (2026-08-02, pedido
explícito del usuario). Mockea toda request HTTP real."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.models import Match as TennisScannedMatch
from database.historical.tennis.models import MatchStats as TennisMatchStats
from database.historical.tennis import ChartedMatch, ChartedMatchStats
from database.historical.tennis.chartedmatchimport import (
    find_overlapping_scanned_match, save_charted_match, import_or_merge_match,
)
from database.historical.tennis.matchstats import save_match_stats
from outer_apis.scrapers.tennis.match_charting_scraper import (
    get_matches_index, get_overview_stats, derive_match_winners, _parse_charting_date,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        ChartedMatchStats.delete().execute()
        ChartedMatch.delete().execute()
        TennisMatchStats.delete().execute()
        TennisScannedMatch.delete().execute()
    yield
    with db_historical.connection_context():
        ChartedMatchStats.delete().execute()
        ChartedMatch.delete().execute()
        TennisMatchStats.delete().execute()
        TennisScannedMatch.delete().execute()


# ── scraper: parsing ─────────────────────────────────────────────────

def test_parse_charting_date():
    assert _parse_charting_date("20260521") == "2026-05-21"
    assert _parse_charting_date("garbage") is None
    assert _parse_charting_date("") is None


_FAKE_MATCHES_CSV = (
    "match_id,Player 1,Player 2,Pl 1 hand,Pl 2 hand,Date,Tournament,Round,Time,Court,Surface,Umpire,Best of,Final TB?,Charted by\n"
    "20260521-M-Roland_Garros-Q3-A-B,Player A,Player B,R,R,20260521,Roland Garros,Q3,,7,Clay,,3,A,x\n"
    "20100101-M-Old_Event-F-C-D,Player C,Player D,R,R,20100101,Old Event,F,,1,Hard,,3,A,x\n"
)


def test_get_matches_index_filters_by_since_date():
    with patch(
        "outer_apis.scrapers.tennis.match_charting_scraper._fetch_csv",
        return_value=list(__import__("csv").DictReader(__import__("io").StringIO(_FAKE_MATCHES_CSV))),
    ):
        result = get_matches_index("m", since_date="2016-01-01")

    assert len(result) == 1
    assert result[0]["match_id"] == "20260521-M-Roland_Garros-Q3-A-B"
    assert result[0]["player1"] == "Player A"
    assert result[0]["tournament"] == "Roland Garros"
    assert result[0]["round"] == "Q3"
    assert result[0]["best_of"] == 3


def test_get_matches_index_invalid_tour():
    assert get_matches_index("x") == []


_FAKE_OVERVIEW_CSV = (
    "match_id,player,set,serve_pts,aces,dfs,first_in,first_won,second_in,second_won,"
    "bk_pts,bp_saved,return_pts,return_pts_won,winners,winners_fh,winners_bh,unforced,unforced_fh,unforced_bh\n"
    "20260521-M-Roland_Garros-Q3-A-B,Player A,Total,80,6,4,48,33,32,13,11,8,61,18,25,13,5,25,15,6\n"
    "20260521-M-Roland_Garros-Q3-A-B,Player B,Total,61,3,2,34,27,27,16,3,3,80,34,28,23,2,21,11,8\n"
    "20260521-M-Roland_Garros-Q3-A-B,Player A,1,52,6,2,32,22,20,8,7,6,34,9,18,8,3,13,7,4\n"
)


def test_get_overview_stats_shapes_like_flashscore():
    import csv
    import io

    def _fetch_side_effect(filename):
        if "matches" in filename:
            return list(csv.DictReader(io.StringIO(_FAKE_MATCHES_CSV)))
        return list(csv.DictReader(io.StringIO(_FAKE_OVERVIEW_CSV)))

    with patch(
        "outer_apis.scrapers.tennis.match_charting_scraper._fetch_csv", side_effect=_fetch_side_effect,
    ):
        result = get_overview_stats("m")

    match_id = "20260521-M-Roland_Garros-Q3-A-B"
    assert result[match_id]["match"]["aces"] == ("6", "3")
    assert result[match_id]["set_1"]["aces"] == ("6", None)  # solo Player A tiene fila de set 1 en el fixture


_FAKE_POINTS_CSV_HEADER = "match_id,Pt,Set1,Set2,Gm1,Gm2,Pts,Gm#,TbSet,Svr,1st,2nd,Notes,PtWinner\n"


def test_derive_match_winners_from_last_point():
    csv_text = _FAKE_POINTS_CSV_HEADER + (
        "M1,1,0,0,0,0,0-0,1,False,1,x,,,1\n"
        "M1,2,1,0,0,0,0-0,2,False,1,x,,,1\n"  # último punto real: Player1 con 1 set a favor
    )
    import csv, io
    with patch(
        "outer_apis.scrapers.tennis.match_charting_scraper._fetch_csv",
        return_value=list(csv.DictReader(io.StringIO(csv_text))),
    ):
        winners = derive_match_winners("m", since_date="2020-01-01")

    assert winners["M1"] == 1


def test_derive_match_winners_uses_ptwinner_to_break_set_tie():
    csv_text = _FAKE_POINTS_CSV_HEADER + (
        "M2,50,1,1,0,0,0-0,1,False,2,x,,,2\n"  # 1-1 en sets, este punto decide -- lo ganó jugador 2
    )
    import csv, io
    with patch(
        "outer_apis.scrapers.tennis.match_charting_scraper._fetch_csv",
        return_value=list(csv.DictReader(io.StringIO(csv_text))),
    ):
        winners = derive_match_winners("m", since_date="2020-01-01")

    assert winners["M2"] == 2


def test_derive_match_winners_fail_closed_no_ptwinner_no_gap():
    csv_text = _FAKE_POINTS_CSV_HEADER + "M3,1,0,0,0,0,0-0,1,False,1,x,,,\n"
    import csv, io
    with patch(
        "outer_apis.scrapers.tennis.match_charting_scraper._fetch_csv",
        return_value=list(csv.DictReader(io.StringIO(csv_text))),
    ):
        winners = derive_match_winners("m", since_date="2020-01-01")

    assert "M3" not in winners


# ── DB: matching/complementing ───────────────────────────────────────

def test_find_overlapping_scanned_match_by_name_and_date():
    from datetime import date
    TennisScannedMatch.create(
        market_id="KX1", ticker_a="KX1-A", ticker_b="KX1-B", tour="ATP",
        player_a_name="Player A", player_b_name="Player B", best_of=3,
        detected_at=datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc),
    )
    match = find_overlapping_scanned_match("Player B", "Player A", date(2026, 5, 21))
    assert match is not None
    assert match.market_id == "KX1"


def test_find_overlapping_scanned_match_none_when_no_match():
    from datetime import date
    assert find_overlapping_scanned_match("Nobody", "Nobody Else", date(2026, 5, 21)) is None


def test_import_or_merge_creates_new_charted_match_when_no_overlap():
    entry = {
        "match_id": "M1", "player1": "Player A", "player2": "Player B",
        "date": "2026-05-21", "tournament": "Roland Garros", "round": "Q3",
        "surface": "Clay", "best_of": 3,
    }
    result = import_or_merge_match(entry, winner_side=1, tour="ATP", stats={"match": {"Aces": ["6", "3"]}})
    assert result["mode"] == "created_new"
    charted = ChartedMatch.get(ChartedMatch.match_id == "M1")
    assert charted.winner == "p1"
    assert ChartedMatchStats.select().where(ChartedMatchStats.match == charted).exists()


def test_import_or_merge_merges_into_existing_scanned_match():
    TennisScannedMatch.create(
        market_id="KX2", ticker_a="KX2-A", ticker_b="KX2-B", tour="ATP",
        player_a_name="Player A", player_b_name="Player B", best_of=3,
        detected_at=datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc),
    )
    save_match_stats("KX2", "fs1", {"match": {"Aces": ["3", "1"]}})

    entry = {
        "match_id": "M2", "player1": "Player A", "player2": "Player B",
        "date": "2026-05-21", "tournament": "Roland Garros", "round": "Q3",
        "surface": "Clay", "best_of": 3,
    }
    result = import_or_merge_match(entry, winner_side=1, tour="ATP", stats={"match": {"aces": ["6", "3"]}})

    assert result["mode"] == "merged_existing"
    assert ChartedMatch.select().where(ChartedMatch.match_id == "M2").count() == 0  # no duplica
    import json
    stats = json.loads(TennisMatchStats.get(TennisMatchStats.market_id == "KX2").raw_stats_json)
    assert stats["match"]["Aces"] == ["3", "1"]  # lo original de Flashscore, intacto
    assert stats["sackmann_match"] == {"aces": ["6", "3"]}  # lo nuevo, prefijado


def test_save_charted_match_is_idempotent():
    entry = {
        "match_id": "M3", "player1": "Player A", "player2": "Player B",
        "date": "2026-05-21", "tournament": "X", "round": "F",
        "surface": "Hard", "best_of": 3,
    }
    first = save_charted_match(entry, 1, "ATP")
    second = save_charted_match(entry, 2, "ATP")  # no debería pisar el ganador ya guardado
    assert first.id == second.id
    assert ChartedMatch.select().where(ChartedMatch.match_id == "M3").count() == 1
