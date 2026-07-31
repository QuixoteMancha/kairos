"""Tests del rediseño de caching de historial de tenis (2026-07-28, ver
memoria kairos-tennis-brightdata-db-cache-2026-07-28): el cache en
memoria (`_recent_matches_cache`) se eliminó -- cada llamada consulta la
DB de historial (SQLite en WAL, indexada por player_name) antes de pegarle
a Flashscore vía BrightData. Objetivo del rediseño: sobrevivir a un
reinicio del contenedor sin disparar un refetch masivo (el cache viejo
vivía solo en RAM y se vaciaba con cada restart).

2026-07-31: la frescura pasó de ventana rodante de horas a borde de
calendario UTC (00:00 GMT) -- ver
database/historical/tennis/writer.py::_last_midnight_utc. Los tests de
"stale" usan un timestamp de AYER (antes del último borde de medianoche
UTC), no "hace N horas", para reflejar el nuevo diseño correctamente.

Usa la DB SQLite temporal/aislada de tests/conftest.py -- nunca la DB real.
Mockea SOLO el scraper (flashscore_scraper), nunca pega a Flashscore/
BrightData real."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from database.historical.tennis.writer import _last_midnight_utc

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.tennismatchhistory import TennisMatchHistory
from database.historical.tennis.writer import get_recent_match_history
from outer_apis.sports.tennis import match_history


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisMatchHistory.delete().execute()
    yield
    with db_historical.connection_context():
        TennisMatchHistory.delete().execute()


def _insert_match(player: str, fetched_at, opponent="Opponent A", match_date="2026-07-20"):
    with db_historical.connection_context():
        TennisMatchHistory.create(
            player_name=player, opponent_name=opponent, match_date=match_date,
            result="W", score="6-4 6-3", source="flashscore", fetched_at=fetched_at,
        )


def test_get_recent_match_history_returns_none_when_no_rows():
    assert get_recent_match_history("Nobody Ever Fetched") is None


def test_get_recent_match_history_returns_fresh_rows():
    _insert_match("Sinner J.", fetched_at=datetime.now(timezone.utc) - timedelta(minutes=5))
    result = get_recent_match_history("Sinner J.")
    assert result is not None
    assert len(result) == 1
    assert result[0]["oponente"] == "Opponent A"
    assert result[0]["resultado"] == "W"


def test_get_recent_match_history_fresh_even_hours_old_if_fetched_today():
    """Con el borde de calendario (2026-07-31), una fila de HOY sigue
    fresca aunque tenga varias horas -- ya no es una ventana rodante de 1h."""
    fetched_at = _last_midnight_utc() + timedelta(hours=5)  # hoy, 05:00 GMT
    _insert_match("Sinner J.", fetched_at=fetched_at)
    assert get_recent_match_history("Sinner J.") is not None


def test_get_recent_match_history_none_when_stale():
    """Fila real pero de ANTES del borde de medianoche UTC de hoy -- debe
    forzar fetch en vivo, no servir un dato de ayer indefinidamente."""
    fetched_at_yesterday = _last_midnight_utc() - timedelta(minutes=1)
    _insert_match("Alcaraz C.", fetched_at=fetched_at_yesterday)
    assert get_recent_match_history("Alcaraz C.") is None


def test_fetch_and_cache_skips_live_fetch_when_db_is_fresh():
    """EL CASO QUE MOTIVÓ EL REDISEÑO: con datos frescos en DB (ej.
    porque el contenedor se reinició pero la DB sobrevivió), NO debe
    llamarse al scraper -- verificado directamente contando llamadas al
    mock, no solo el resultado."""
    _insert_match("Djokovic N.", fetched_at=datetime.now(timezone.utc) - timedelta(minutes=1))

    with patch("outer_apis.sports.tennis.match_history.flashscore_scraper.get_player_recent_matches") as mock_fetch:
        result = match_history._fetch_and_cache("Djokovic N.")

    mock_fetch.assert_not_called()
    assert len(result) == 1
    assert result[0]["oponente"] == "Opponent A"


def test_fetch_and_cache_calls_live_fetch_when_db_empty_and_persists_result():
    """Sin nada en DB, sí debe llamar al scraper -- y el resultado fresco
    debe persistirse (para que la PRÓXIMA llamada, incluso tras un
    restart, ya no necesite red)."""
    fake_matches = [{"fecha": "2026-07-27", "oponente": "Zverev A.", "resultado": "L",
                      "score": "3-6 4-6", "source": "flashscore"}]

    with patch("outer_apis.sports.tennis.match_history.flashscore_scraper.get_player_recent_matches",
               return_value=fake_matches) as mock_fetch:
        result = match_history._fetch_and_cache("Medvedev D.")

    mock_fetch.assert_called_once()
    assert result == fake_matches

    # Verifica que quedó persistido -- una llamada posterior ya no debería tocar la red.
    with patch("outer_apis.sports.tennis.match_history.flashscore_scraper.get_player_recent_matches") as mock_fetch2:
        result2 = match_history._fetch_and_cache("Medvedev D.")
    mock_fetch2.assert_not_called()
    assert len(result2) == 1
    assert result2[0]["oponente"] == "Zverev A."


def test_get_recent_match_history_fresh_when_todays_match_already_stored_even_if_checked_yesterday():
    """Segundo pedido explícito del usuario (2026-07-31): si el partido
    más reciente GUARDADO ya es de hoy, no hace falta un fetch en vivo
    aunque el último chequeo (`fetched_at`) haya sido ayer -- no puede
    existir un partido "más nuevo" del que ya tenemos."""
    fetched_at_yesterday = _last_midnight_utc() - timedelta(hours=2)
    today_str = _last_midnight_utc().date().isoformat()
    _insert_match("Gauff C.", fetched_at=fetched_at_yesterday, match_date=today_str)

    result = get_recent_match_history("Gauff C.")
    assert result is not None
    assert result[0]["oponente"] == "Opponent A"


def test_get_recent_match_history_stale_when_neither_checked_today_nor_have_todays_match():
    """Contraparte: chequeado ayer Y el partido más reciente guardado
    también es de ayer -- ahí sí debe forzar el fetch en vivo."""
    fetched_at_yesterday = _last_midnight_utc() - timedelta(hours=2)
    yesterday_str = (_last_midnight_utc() - timedelta(days=1)).date().isoformat()
    _insert_match("Rybakina E.", fetched_at=fetched_at_yesterday, match_date=yesterday_str)

    assert get_recent_match_history("Rybakina E.") is None


def test_fetch_and_cache_refetches_when_db_stale():
    """Dato en DB pero de ayer (antes del borde de medianoche UTC de hoy)
    -- sí debe volver a pegarle a Flashscore, no queda pegado a un dato
    de un día natural anterior para siempre."""
    _insert_match("Swiatek I.", fetched_at=_last_midnight_utc() - timedelta(minutes=1))
    fake_matches = [{"fecha": "2026-07-28", "oponente": "Sabalenka A.", "resultado": "W",
                      "score": "6-2 6-1", "source": "flashscore"}]

    with patch("outer_apis.sports.tennis.match_history.flashscore_scraper.get_player_recent_matches",
               return_value=fake_matches) as mock_fetch:
        result = match_history._fetch_and_cache("Swiatek I.")

    mock_fetch.assert_called_once()
    assert result == fake_matches
