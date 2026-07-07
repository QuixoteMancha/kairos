"""
test_tennis_data_sackmann.py — reactivated Sackmann pipeline populates
PlayerElo / RankingHistory / PlayerStats with mocked CSV responses.

No real network: `requests.get` is monkeypatched to serve small in-memory
CSVs shaped exactly like Sackmann's real columns. This confirms the wiring
(parsing → EloEngine.update_from_sackmann → tennis_data.fetch_all_tennis_data)
without depending on the live GitHub source (which, as of this writing, no
longer serves JeffSackmann/tennis_atp — see session report / commit message
for the live-verification details).
"""
from __future__ import annotations

import tempfile
from datetime import date

import pytest

_ATP_MATCHES_CSV = (
    "tourney_id,tourney_name,surface,tourney_level,tourney_date,"
    "winner_name,loser_name,score,winner_rank,loser_rank,"
    "w_ace,w_df,w_svpt,w_1stIn,w_1stWon,w_2ndWon,w_SvGms,w_bpSaved,w_bpFaced,"
    "l_ace,l_df,l_svpt,l_1stIn,l_1stWon,l_2ndWon,l_SvGms,l_bpSaved,l_bpFaced\n"
    "2023-580,Test Open,Hard,A,20230115,"
    "Test Winner,Test Loser,6-3 6-4,5,40,"
    "8,2,70,45,35,15,10,3,4,"
    "3,4,68,40,25,10,9,2,5\n"
    "2023-580,Test Open,Hard,A,20230115,"
    "Test Winner,Another Rival,7-6(4) 6-2,5,80,"
    "10,1,72,50,38,14,11,4,5,"
    "5,3,60,32,20,12,8,1,3\n"
)

_ATP_PLAYERS_CSV = (
    "player_id,name_first,name_last,hand,dob,country_ioc,height,wikidata_id\n"
    "100001,Test,Winner,R,19950101,USA,188,\n"
    "100002,Test,Loser,R,19960202,ESP,183,\n"
)

_ATP_RANKINGS_CSV = (
    "ranking_date,rank,player,points\n"
    "20230116,1,100001,9000\n"
    "20230116,2,100002,7500\n"
)


class _FakeResp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


def _fake_get(url: str, headers=None, timeout=None, **kw):
    if "atp_matches_2023.csv" in url:
        return _FakeResp(200, _ATP_MATCHES_CSV)
    if "wta_matches_2023.csv" in url:
        return _FakeResp(404, "")
    if "atp_players.csv" in url:
        return _FakeResp(200, _ATP_PLAYERS_CSV)
    if "atp_rankings_current.csv" in url:
        return _FakeResp(200, _ATP_RANKINGS_CSV)
    return _FakeResp(404, "")


@pytest.fixture(autouse=True)
def _db():
    from src.database.database import init_db
    init_db()
    yield


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch, tmp_path):
    # Force a throwaway local cache dir so this test never reads/writes the
    # real /tmp/kairos_sackmann cache used by a live-running engine.
    import src.scrapers.sackmann_scraper as sack_mod
    monkeypatch.setattr(sack_mod, "_CACHE_DIR", str(tmp_path))
    yield


@pytest.fixture(autouse=True)
def _mock_requests(monkeypatch):
    import src.scrapers.sackmann_scraper as sack_mod
    monkeypatch.setattr(sack_mod.requests, "get", _fake_get)
    yield


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    import src.scrapers.sackmann_scraper as sack_mod

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return date(2023, 6, 1)

    monkeypatch.setattr(sack_mod, "date", _FixedDate)
    yield


def test_get_player_matches_parses_stats():
    from src.scrapers.sackmann_scraper import SackmannScraper

    sk = SackmannScraper()
    matches = sk.get_player_matches("Test Winner", years=1, categories=["ATP"])
    assert len(matches) == 2
    m = matches[0]
    assert m["winner_is_player"] is True
    assert m["aces_match"] in (8, 10)
    assert m["surface"] in ("Hard",)


def test_get_top_rankings_parses_csv():
    from src.scrapers.sackmann_scraper import SackmannScraper

    sk = SackmannScraper()
    top = sk.get_top_rankings(n=10, tour="atp")
    assert len(top) == 2
    assert top[0]["nombre"] == "Test Winner"
    assert top[0]["ranking"] == 1
    assert top[0]["points"] == 9000


def test_fetch_all_tennis_data_populates_elo_rankinghistory_playerstats():
    from src.core.orchestration.tennis_data import fetch_all_tennis_data
    from src.database.database import Player, PlayerElo, RankingHistory, PlayerStats

    res = fetch_all_tennis_data(top_n=10, years=1)

    assert res["players"] == 2
    assert res["elo_updated"] >= 1
    assert res["stats_updated"] >= 1
    assert res["ranking_rows"] == 2

    p = Player.get(Player.nombre == "Test Winner")
    assert p.ranking == 1
    assert p.ranking_type == "ATP"

    rh = RankingHistory.get(RankingHistory.player == p)
    assert rh.ranking == 1
    assert rh.elo_points == 9000

    elo_row = PlayerElo.get((PlayerElo.player == p) & (PlayerElo.surface == "Overall"))
    assert elo_row.matches >= 1
    assert elo_row.elo != 1500.0  # moved away from the default after real matches

    ps = PlayerStats.get((PlayerStats.player == p) & (PlayerStats.superficie == "all"))
    assert ps.matches_12m == 2
    assert ps.match_win_rate == pytest.approx(1.0)
    assert ps.aces_por_partido == pytest.approx(9.0)  # (8+10)/2


def test_fetch_all_tennis_data_no_rankings_returns_zero_counts(monkeypatch):
    import src.scrapers.sackmann_scraper as sack_mod

    def _empty_get(*a, **kw):
        return _FakeResp(404, "")

    monkeypatch.setattr(sack_mod.requests, "get", _empty_get)

    from src.core.orchestration.tennis_data import fetch_all_tennis_data
    res = fetch_all_tennis_data(top_n=10, years=1)
    assert res == {"players": 0, "elo_updated": 0, "stats_updated": 0, "ranking_rows": 0}
