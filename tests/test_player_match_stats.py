"""
test_player_match_stats.py — stats por-jugador-por-partido (goles/puntos/
asistencias/pases/minutos) para los 4 deportes de equipo, con HTTP
completamente mockeado usando los shapes reales confirmados en vivo por
Priya el 2026-07-07 contra `/event/{id}/lineups` de Sofascore (ver
docstrings de `src/scrapers/sofascore/client.py::get_event_lineups` y
`src/core/orchestration/team_data.py` para la fuente/fecha de cada shape).

No hace red real: `SofascoreScraper.search_team_id` / `.get_team_last_matches`
/ `.get_event_lineups` se monkeypatchean directamente.
"""
from __future__ import annotations

from datetime import date

import pytest


@pytest.fixture(autouse=True)
def _db():
    from src.database.database import init_db
    init_db()
    yield


def _make_team(sport="football", name="Real Madrid PMS Test"):
    from src.database.database import TeamRating

    team, _created = TeamRating.get_or_create(
        sport=sport, name_norm=name.lower(),
        defaults={"name": name, "league": "ESP"},
    )
    return team


def _fake_event(home_id, away_id, home_name, away_name, event_id, ts=1767898800):
    return {
        "id": event_id,
        "homeTeam": {"id": home_id, "name": home_name},
        "awayTeam": {"id": away_id, "name": away_name},
        "startTimestamp": ts,
        "tournament": {"name": "Test League"},
        "status": {"type": "finished"},
    }


# ── populate_player_match_stats — shapes reales por deporte ────────────────

def test_football_lineup_shape_real_madrid_vs_atletico(monkeypatch):
    """Shape real confirmado en vivo (evento 14252105, Atlético 1-2 Real
    Madrid, Supercopa de España)."""
    from src.core.orchestration import team_data
    from src.scrapers.sofascore.client import SofascoreScraper
    from src.database.database import PlayerMatchStats

    team = _make_team(sport="football", name="Real Madrid PMS")

    monkeypatch.setattr(SofascoreScraper, "search_team_id", lambda self, n, s: 2829)
    monkeypatch.setattr(
        SofascoreScraper, "get_team_last_matches",
        lambda self, team_id, n=20: [_fake_event(2836, 2829, "Atlético Madrid", "Real Madrid", 14252105)],
    )

    def _fake_lineups(self, event_id):
        assert event_id == 14252105
        return {
            "home": {"players": [{
                "player": {"name": "Jan Oblak", "position": "G"},
                "teamId": 2836, "position": "G", "substitute": False,
                "statistics": {"minutesPlayed": 90, "totalPass": 19, "accuratePass": 15, "saves": 2},
            }]},
            "away": {"players": [{
                "player": {"name": "Federico Valverde", "position": "M"},
                "teamId": 2829, "position": "M", "substitute": False,
                "statistics": {
                    "minutesPlayed": 90, "goals": 1, "goalAssist": 1,
                    "totalPass": 48, "accuratePass": 39, "keyPass": 1,
                    "totalShots": 2, "rating": 8.1,
                },
            }]},
        }

    monkeypatch.setattr(SofascoreScraper, "get_event_lineups", _fake_lineups)

    result = team_data.populate_player_match_stats(team, "Real Madrid", "football", n_events=1)
    assert result["source"] == "sofascore"
    assert result["new_rows"] == 1  # solo el jugador del EQUIPO consultado (away=Real Madrid)

    row = PlayerMatchStats.get(PlayerMatchStats.team == team)
    assert row.player_name == "Federico Valverde"
    assert row.rival == "Atlético Madrid"
    assert row.minutos_jugados == 90.0
    assert row.goles == 1
    assert row.asistencias == 1
    assert row.pases_totales == 48
    assert row.pases_completados == 39
    assert row.pases_clave == 1
    assert row.tiros == 2
    assert row.rating == 8.1
    assert row.puntos is None  # concepto basketball, no aplica a fútbol


def test_basketball_lineup_shape_celtics(monkeypatch):
    """Shape real confirmado en vivo (evento 14442616, Boston Celtics,
    NBA) -- `secondsPlayed` en vez de `minutesPlayed`, sin `totalPass`."""
    from src.core.orchestration import team_data
    from src.scrapers.sofascore.client import SofascoreScraper
    from src.database.database import PlayerMatchStats

    team = _make_team(sport="basketball", name="Boston Celtics PMS")

    monkeypatch.setattr(SofascoreScraper, "search_team_id", lambda self, n, s: 3422)
    monkeypatch.setattr(
        SofascoreScraper, "get_team_last_matches",
        lambda self, team_id, n=20: [_fake_event(3422, 9999, "Boston Celtics", "Rival", 14442616)],
    )

    def _fake_lineups(self, event_id):
        return {
            "home": {"players": [{
                "player": {"name": "Sam Hauser", "position": "GF"},
                "teamId": 3422, "position": "GF", "substitute": False,
                "statistics": {
                    "secondsPlayed": 1748, "points": 8, "assists": 2,
                    "rebounds": 3, "steals": 1, "blocks": 0,
                    "fieldGoalAttempts": 11, "rating": 6.5,
                },
            }]},
            "away": {"players": []},
        }

    monkeypatch.setattr(SofascoreScraper, "get_event_lineups", _fake_lineups)

    result = team_data.populate_player_match_stats(team, "Boston Celtics", "basketball", n_events=1)
    assert result["source"] == "sofascore"
    assert result["new_rows"] == 1

    row = PlayerMatchStats.get(PlayerMatchStats.team == team)
    assert row.player_name == "Sam Hauser"
    assert row.minutos_jugados == pytest.approx(1748 / 60.0, abs=0.05)
    assert row.puntos == 8
    assert row.asistencias == 2
    assert row.tiros == 11
    assert row.goles is None       # concepto fútbol/hockey, no aplica
    assert row.pases_totales is None  # basketball no expone "pase" en este endpoint


def test_ice_hockey_lineup_shape_bruins(monkeypatch):
    """Shape real confirmado en vivo (evento 14201354, Boston Bruins,
    NHL) -- `secondsPlayed` + `goals`/`assists`/`shots`, sin `totalPass`."""
    from src.core.orchestration import team_data
    from src.scrapers.sofascore.client import SofascoreScraper
    from src.database.database import PlayerMatchStats

    team = _make_team(sport="ice_hockey", name="Boston Bruins PMS")

    monkeypatch.setattr(SofascoreScraper, "search_team_id", lambda self, n, s: 3677)
    monkeypatch.setattr(
        SofascoreScraper, "get_team_last_matches",
        lambda self, team_id, n=20: [_fake_event(3677, 8888, "Boston Bruins", "Rival", 14201354)],
    )

    def _fake_lineups(self, event_id):
        return {
            "home": {"players": [{
                "player": {"name": "David Pastrnak", "position": "F"},
                "teamId": 3677, "position": "F", "substitute": False,
                "statistics": {
                    "secondsPlayed": 1200, "goals": 2, "assists": 1,
                    "points": 3, "shots": 5, "hits": 1, "penaltyMinutes": 0,
                },
            }]},
            "away": {"players": []},
        }

    monkeypatch.setattr(SofascoreScraper, "get_event_lineups", _fake_lineups)

    result = team_data.populate_player_match_stats(team, "Boston Bruins", "ice_hockey", n_events=1)
    assert result["source"] == "sofascore"

    row = PlayerMatchStats.get(PlayerMatchStats.team == team)
    assert row.player_name == "David Pastrnak"
    assert row.minutos_jugados == 20.0
    assert row.goles == 2
    assert row.asistencias == 1
    assert row.tiros == 5
    assert row.puntos is None  # basketball-only field
    assert row.pases_totales is None


def test_baseball_lineup_shape_yankees_no_minutes_concept(monkeypatch):
    """Shape real confirmado en vivo (evento 15500874, NY Yankees, MLB):
    solo prefijos `batting*`/`fielding*`, SIN `secondsPlayed`/
    `minutesPlayed` -- confirma el gap documentado explícitamente (baseball
    no tiene concepto de "minutos jugados" en este endpoint)."""
    from src.core.orchestration import team_data
    from src.scrapers.sofascore.client import SofascoreScraper
    from src.database.database import PlayerMatchStats

    team = _make_team(sport="baseball", name="NY Yankees PMS")

    monkeypatch.setattr(SofascoreScraper, "search_team_id", lambda self, n, s: 3654)
    monkeypatch.setattr(
        SofascoreScraper, "get_team_last_matches",
        lambda self, team_id, n=20: [_fake_event(3654, 7777, "New York Yankees", "Rival", 15500874)],
    )

    def _fake_lineups(self, event_id):
        return {
            "home": {"players": [{
                "player": {"name": "Aaron Judge", "position": "OF"},
                "teamId": 3654, "position": "OF", "substitute": False,
                "statistics": {
                    "battingAtBats": 4, "battingHits": 2, "battingHomeRuns": 1,
                    "battingRbi": 3, "fieldingGamesPlayed": 1, "fieldingPutOuts": 2,
                },
            }]},
            "away": {"players": []},
        }

    monkeypatch.setattr(SofascoreScraper, "get_event_lineups", _fake_lineups)

    result = team_data.populate_player_match_stats(team, "New York Yankees", "baseball", n_events=1)
    assert result["source"] == "sofascore"

    row = PlayerMatchStats.get(PlayerMatchStats.team == team)
    assert row.player_name == "Aaron Judge"
    assert row.minutos_jugados is None  # gap confirmado, no fabricado
    assert row.goles is None
    assert row.puntos is None
    assert row.asistencias is None
    assert row.pases_totales is None
    assert "battingHomeRuns" in row.extra_json  # detalle real preservado en extra_json


def test_populate_player_match_stats_db_shortcut_no_network(monkeypatch):
    from src.core.orchestration import team_data
    from src.database.database import PlayerMatchStats

    team = _make_team(sport="football", name="DB Shortcut PMS FC")
    PlayerMatchStats.create(
        team=team, sofascore_event_id=1, player_name="P1", sport="football",
        fecha=date.today(), source="sofascore",
    )

    def _boom(*a, **kw):
        raise AssertionError("no debería golpear red si la DB ya alcanza n_events")

    from src.scrapers.sofascore.client import SofascoreScraper
    monkeypatch.setattr(SofascoreScraper, "search_team_id", _boom)

    result = team_data.populate_player_match_stats(team, "DB Shortcut PMS FC", "football", n_events=1)
    assert result == {"source": "db", "new_rows": 0}


def test_populate_player_match_stats_no_source_returns_none(monkeypatch):
    from src.core.orchestration import team_data
    from src.scrapers.sofascore.client import SofascoreScraper

    team = _make_team(sport="football", name="No Source PMS FC")
    monkeypatch.setattr(SofascoreScraper, "search_team_id", lambda self, n, s: None)

    result = team_data.populate_player_match_stats(team, "No Source PMS FC", "football", n_events=3)
    assert result == {"source": "none", "new_rows": 0}


# ── compute_player_criticality ──────────────────────────────────────────────

def test_compute_player_criticality_football_participation_and_weight():
    from src.core.orchestration import team_data
    from src.database.database import PlayerMatchStats
    from datetime import timedelta

    team = _make_team(sport="football", name="Criticality FC")
    today = date.today()
    # 4 partidos: el arquero (G) jugó los 4 (90'), un titular de campo jugó 3 de 4 (>=45'),
    # un suplente jugó 1 de 4 con pocos minutos (no supera umbral).
    for i in range(4):
        PlayerMatchStats.create(
            team=team, sofascore_event_id=100 + i, player_name="Goalkeeper X",
            sport="football", fecha=today - timedelta(days=i * 7),
            posicion="G", minutos_jugados=90.0, source="sofascore",
        )
    for i in range(3):
        PlayerMatchStats.create(
            team=team, sofascore_event_id=100 + i, player_name="Midfielder Y",
            sport="football", fecha=today - timedelta(days=i * 7),
            posicion="M", minutos_jugados=70.0, source="sofascore",
        )
    PlayerMatchStats.create(
        team=team, sofascore_event_id=103, player_name="Sub Z",
        sport="football", fecha=today - timedelta(days=21),
        posicion="F", minutos_jugados=10.0, source="sofascore",
    )

    result = team_data.compute_player_criticality(team, "football", window=10)
    assert result["n_matches"] == 4
    crit = result["criticality"]
    assert crit["Goalkeeper X"] == pytest.approx(1.0 * 1.5)   # participación 4/4, peso arquero 1.5
    assert crit["Midfielder Y"] == pytest.approx(0.75 * 1.0)  # participación 3/4, peso default 1.0
    assert crit["Sub Z"] == pytest.approx(0.0)                # nunca superó el umbral (45')


def test_compute_player_criticality_baseball_always_fail_closed():
    """Vesper §3/§5.3: baseball nunca debe aproximarse con roster histórico
    -- el input real que falta es el abridor probable DEL DÍA."""
    from src.core.orchestration import team_data
    from src.database.database import PlayerMatchStats

    team = _make_team(sport="baseball", name="Fail Closed Baseball")
    PlayerMatchStats.create(
        team=team, sofascore_event_id=1, player_name="Aaron Judge",
        sport="baseball", fecha=date.today(), source="sofascore",
    )

    result = team_data.compute_player_criticality(team, "baseball")
    assert result["criticality"] == {}
    assert result["n_matches"] == 0
    assert "reason" in result


def test_compute_player_criticality_no_data_fail_closed():
    from src.core.orchestration import team_data

    team = _make_team(sport="ice_hockey", name="No Data Hockey Team")
    result = team_data.compute_player_criticality(team, "ice_hockey")
    assert result["criticality"] == {}
    assert result["n_matches"] == 0
    assert "reason" in result
