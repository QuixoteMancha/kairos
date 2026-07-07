"""
test_team_history_roster.py — fallback DB local -> Sofascore -> Flashscore
para HISTORIAL y PLANTEL de EQUIPOS (multideporte), con HTTP completamente
mockeado usando los shapes reales confirmados en vivo por Priya el
2026-07-07 (ver docstrings de `src/core/orchestration/team_data.py` y
`src/scrapers/sofascore/client.py` para la fuente/fecha de cada shape).

No hace red real: `SofascoreScraper.search_team_id` / `.get_team_last_matches`
/ `.get_team_squad` y `flashscore_scraper.get_team_recent_matches_fs` /
`.get_team_squad_fs` se monkeypatchean directamente.

Nota: existe otro `tests/test_team_data.py` (legacy, skipped, sobre el
módulo ARCHIVADO `src.scrapers.forgotten.team_data.common` de la era
sports-bookies pre-pivote) -- este archivo es sobre el módulo NUEVO
`src.core.orchestration.team_data`, sin relación.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _db():
    from src.database.database import init_db
    init_db()
    yield


def _make_team(sport="football", name="Real Madrid"):
    from src.database.database import TeamRating

    team, _created = TeamRating.get_or_create(
        sport=sport, name_norm=name.lower(),
        defaults={"name": name, "league": "ESP"},
    )
    return team


# ── populate_team_history ────────────────────────────────────────────────

def test_populate_team_history_db_shortcut_no_network(monkeypatch):
    """Si ya hay >= target filas en DB, no debe llamar a ninguna red."""
    from src.core.orchestration import team_data
    from src.database.database import TeamMatchHistory
    from datetime import date, timedelta

    team = _make_team(name="DB Shortcut Test FC")
    for i in range(5):
        TeamMatchHistory.create(
            team=team, fecha=date.today() - timedelta(days=i),
            rival=f"Rival {i}", resultado="W", source="sofascore",
        )

    def _boom(*a, **kw):
        raise AssertionError("no debería golpear red si la DB ya alcanza target")

    monkeypatch.setattr(team_data, "_write_team_history", _boom, raising=False)

    result = team_data.populate_team_history(team, "Real Madrid", "football", target=3)
    assert result == {"source": "db", "new_rows": 0}


def test_populate_team_history_sofascore_real_shape(monkeypatch):
    """Shape real confirmado en vivo contra Real Madrid (id 2829, evento
    Atlético Madrid vs Real Madrid, Supercopa de España, winnerCode=2)."""
    from src.core.orchestration import team_data
    from src.scrapers.sofascore.client import SofascoreScraper
    from src.database.database import TeamMatchHistory

    team = _make_team(name="Real Madrid Sofascore Test")

    def _fake_search_team_id(self, team_name, sport):
        assert sport == "football"
        return 2829

    def _fake_get_team_last_matches(self, team_id, n=20):
        assert team_id == 2829
        return [{
            "homeTeam": {"id": 2836, "name": "Atlético Madrid"},
            "awayTeam": {"id": 2829, "name": "Real Madrid"},
            "homeScore": {"current": 1, "display": 1},
            "awayScore": {"current": 2, "display": 2},
            "startTimestamp": 1767898800,
            "tournament": {"name": "Supercopa de España"},
            "winnerCode": 2,
            "status": {"code": 100, "description": "Ended", "type": "finished"},
        }]

    monkeypatch.setattr(SofascoreScraper, "search_team_id", _fake_search_team_id)
    monkeypatch.setattr(SofascoreScraper, "get_team_last_matches", _fake_get_team_last_matches)

    result = team_data.populate_team_history(team, "Real Madrid", "football", target=20)
    assert result["source"] == "sofascore"
    assert result["new_rows"] == 1

    row = TeamMatchHistory.get(TeamMatchHistory.team == team)
    assert row.rival == "Atlético Madrid"
    assert row.resultado == "W"          # Real Madrid ganó como visitante (winnerCode=2)
    assert row.goles_favor == 2
    assert row.goles_contra == 1
    assert row.es_local is False
    assert row.competicion == "Supercopa de España"
    assert row.source == "sofascore"


def test_populate_team_history_falls_back_to_flashscore_when_sofascore_empty(monkeypatch):
    from src.core.orchestration import team_data
    from src.scrapers.sofascore.client import SofascoreScraper
    from src.database.database import TeamMatchHistory

    team = _make_team(sport="basketball", name="Boston Celtics")

    monkeypatch.setattr(SofascoreScraper, "search_team_id", lambda self, n, s: None)

    def _fake_fs_history(team_name, sport, n=20):
        assert sport == "basketball"
        return [{
            "fecha": "2026-06-01", "rival": "LA Lakers", "resultado": "L",
            "goles_favor": 98, "goles_contra": 101, "competicion": "",
            "es_local": True, "source": "flashscore",
        }]

    import src.scrapers.flashscore_scraper as fs_mod
    monkeypatch.setattr(fs_mod, "get_team_recent_matches_fs", _fake_fs_history)

    result = team_data.populate_team_history(team, "Boston Celtics", "basketball", target=20)
    assert result == {"source": "flashscore", "new_rows": 1}

    row = TeamMatchHistory.get(TeamMatchHistory.team == team)
    assert row.rival == "LA Lakers"
    assert row.resultado == "L"
    assert row.source == "flashscore"


def test_populate_team_history_no_source_returns_none(monkeypatch):
    from src.core.orchestration import team_data
    from src.scrapers.sofascore.client import SofascoreScraper

    team = _make_team(sport="baseball", name="Test Baseball Team")

    monkeypatch.setattr(SofascoreScraper, "search_team_id", lambda self, n, s: None)

    import src.scrapers.flashscore_scraper as fs_mod
    monkeypatch.setattr(fs_mod, "get_team_recent_matches_fs", lambda *a, **kw: [])

    result = team_data.populate_team_history(team, "Test Baseball Team", "baseball", target=20)
    assert result == {"source": "none", "new_rows": 0}


@pytest.mark.parametrize("sport", ["football", "basketball", "baseball", "ice_hockey"])
def test_sport_slug_resolves_for_all_4_sports_sofascore(sport):
    """Confirma que `_SPORT_SLUGS` (client.py) tiene entrada para los 4
    deportes de Kairos -- sin esto, `search_team_id` nunca podría filtrar
    por sport (silenciosamente aceptaría cualquier sport_slug)."""
    from src.scrapers.sofascore.client import _SPORT_SLUGS

    assert sport in _SPORT_SLUGS
    assert _SPORT_SLUGS[sport]  # non-empty


@pytest.mark.parametrize("sport", ["football", "basketball", "baseball", "ice_hockey"])
def test_sport_slug_resolves_for_all_4_sports_flashscore(sport):
    """Idem para `_FS_SPORT_ID` (flashscore_scraper.py)."""
    from src.scrapers.flashscore_scraper import _FS_SPORT_ID

    assert sport in _FS_SPORT_ID


# ── populate_team_roster ─────────────────────────────────────────────────

def test_populate_team_roster_sofascore_real_shape(monkeypatch):
    """Shape real confirmado en vivo contra Real Madrid (id 2829):
    {"players": [{"player": {"name", "position", "jerseyNumber", "country"}}]}."""
    from src.core.orchestration import team_data
    from src.scrapers.sofascore.client import SofascoreScraper
    from src.database.database import TeamRoster

    team = _make_team(name="Real Madrid Roster Test")

    monkeypatch.setattr(SofascoreScraper, "search_team_id", lambda self, n, s: 2829)

    def _fake_get_team_squad(self, team_id):
        assert team_id == 2829
        return [{
            "player": {
                "name": "Kylian Mbappé",
                "position": "F",
                "jerseyNumber": 10,
                "country": {"alpha2": "FR", "alpha3": "FRA", "name": "France", "slug": "france"},
            }
        }]

    monkeypatch.setattr(SofascoreScraper, "get_team_squad", _fake_get_team_squad)

    result = team_data.populate_team_roster(team, "Real Madrid", "football")
    assert result["source"] == "sofascore"
    assert result["new_rows"] == 1

    row = TeamRoster.get(TeamRoster.team == team)
    assert row.player_name == "Kylian Mbappé"
    assert row.posicion == "F"
    assert row.dorsal == 10
    assert row.nacionalidad == "France"
    assert row.source == "sofascore"


def test_populate_team_roster_never_calls_flashscore_gap(monkeypatch):
    """Documenta la decisión: Flashscore roster NO está wireado (gap
    explícito, ver docstring de `populate_team_roster` /
    `get_team_squad_fs`) -- si Sofascore no trae nada, el resultado debe ser
    source="none", nunca debe intentar Flashscore."""
    from src.core.orchestration import team_data
    from src.scrapers.sofascore.client import SofascoreScraper

    team = _make_team(sport="ice_hockey", name="Test Hockey Team")

    monkeypatch.setattr(SofascoreScraper, "search_team_id", lambda self, n, s: None)

    import src.scrapers.flashscore_scraper as fs_mod

    def _boom(*a, **kw):
        raise AssertionError("populate_team_roster no debe llamar a Flashscore (gap documentado)")

    monkeypatch.setattr(fs_mod, "get_team_squad_fs", _boom)

    result = team_data.populate_team_roster(team, "Test Hockey Team", "ice_hockey")
    assert result == {"source": "none", "new_rows": 0}


def test_populate_team_roster_no_overwrite_previous_snapshot(monkeypatch):
    """El plantel es versionado por fecha_snapshot -- una fila de un
    snapshot anterior (fecha distinta) nunca se sobrescribe, ver docstring
    de `TeamRoster`."""
    from src.core.orchestration import team_data
    from src.scrapers.sofascore.client import SofascoreScraper
    from src.database.database import TeamRoster
    from datetime import date, timedelta

    team = _make_team(sport="football", name="Old Snapshot FC")
    old_snap = date.today() - timedelta(days=30)
    TeamRoster.create(
        team=team, player_name="Retired Player", posicion="D", dorsal=4,
        nacionalidad="Spain", fecha_snapshot=old_snap, source="sofascore",
    )

    monkeypatch.setattr(SofascoreScraper, "search_team_id", lambda self, n, s: 999)
    monkeypatch.setattr(
        SofascoreScraper, "get_team_squad",
        lambda self, team_id: [{
            "player": {"name": "New Player", "position": "M", "jerseyNumber": 8,
                       "country": {"name": "Spain"}},
        }],
    )

    team_data.populate_team_roster(team, "Old Snapshot FC", "football")

    all_rows = list(TeamRoster.select().where(TeamRoster.team == team))
    assert len(all_rows) == 2
    old_row = TeamRoster.get(
        (TeamRoster.team == team) & (TeamRoster.fecha_snapshot == old_snap)
    )
    assert old_row.player_name == "Retired Player"  # sigue existiendo, no fue pisada
