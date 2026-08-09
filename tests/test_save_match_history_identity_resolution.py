"""Tests de `save_match_history` resolviendo identidad real vía
match-detail -- 2026-08-09, pedido explícito del usuario:
"save_match_history debe ser modificada usando la lógica del script que
estamos corriendo que está normalizando y filleando masivamente...
sacar los nombres completos, usando el primer nombre completo del
jugador" + "esta función también podría verificar el tour y el
tournament y por supuesto el level".

Antes de esto, `save_match_history` SIEMPRE persistía el rival tal cual
aparece en la tabla de resultados propia del jugador (casi siempre
ABREVIADO, ej. "Sabalenka A.") -- ahora, si la fila trae un `match_id`
real, se resuelve identidad completa (+ tour/tournament/level) vía
`get_match_detail`, con fallback real al comportamiento anterior si la
resolución falla."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import MatchHistory, AtpTournamentLevel, WtaTournamentLevel
from database.historical.tennis.writer import save_match_history, _identity_matches_detail_side


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        AtpTournamentLevel.delete().execute()
        WtaTournamentLevel.delete().execute()
    yield
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        AtpTournamentLevel.delete().execute()
        WtaTournamentLevel.delete().execute()


def _detail(**overrides) -> dict:
    base = {
        "match_id": "999",
        "match_date": "2024-08-10",
        "tournament": "Toronto WTA",
        "tour": "WTA",
        "round": "quarterfinal",
        "surface": "hard",
        "score": "6-4, 6-2",
        "winner_side": "a",
        "player_a_name": "Anisimova Amanda",
        "player_a_slug": "anisimova-3be62",
        "player_a_ranking": 10,
        "player_a_birthdate": "2001-08-31",
        "player_a_height_cm": 180,
        "player_a_weight_kg": 59,
        "player_a_plays": "right",
        "player_b_name": "Sabalenka Aryna",
        "player_b_slug": "sabalenka",
        "player_b_ranking": 1,
        "player_b_birthdate": "1998-05-05",
        "player_b_height_cm": 182,
        "player_b_weight_kg": 80,
        "player_b_plays": "right",
    }
    base.update(overrides)
    return base


def _match(match_id="999", oponente="Sabalenka A.", fecha="2024-08-10", resultado="W", score="6-4, 6-2"):
    return {"oponente": oponente, "fecha": fecha, "resultado": resultado, "score": score,
            "source": "tennisexplorer", "match_id": match_id}


def test_identity_matches_detail_side_ignores_token_order_and_extra_tokens():
    assert _identity_matches_detail_side("Amanda Anisimova", "Anisimova Amanda") is True
    assert _identity_matches_detail_side("Eunhye Lee", "Lee Eunhye (2000)") is True
    assert _identity_matches_detail_side("Amanda Anisimova", "Sabalenka Aryna") is False


def test_save_match_history_resolves_full_names_via_match_detail():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_match_detail",
        return_value=_detail(),
    ):
        save_match_history("Amanda Anisimova", [_match()])

    row = MatchHistory.get(MatchHistory.match_id == "999")
    assert row.player_a_name == "Anisimova Amanda"  # nombre COMPLETO real, no abreviado
    assert row.player_b_name == "Sabalenka Aryna"
    assert row.tour == "WTA"
    assert row.tournament == "Toronto WTA"


def test_save_match_history_falls_back_to_abbreviated_when_match_id_missing():
    save_match_history("Amanda Anisimova", [_match(match_id=None)])
    row = MatchHistory.get(MatchHistory.player_a_name == "Amanda Anisimova")
    assert row.player_b_name == "Sabalenka A."  # abreviado, comportamiento previo intacto


def test_save_match_history_falls_back_when_match_detail_fetch_fails():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_match_detail", return_value=None,
    ):
        save_match_history("Amanda Anisimova", [_match()])
    row = MatchHistory.get(MatchHistory.player_a_name == "Amanda Anisimova")
    assert row.player_b_name == "Sabalenka A."


def test_save_match_history_falls_back_when_identity_does_not_confirm():
    # match_id apunta a un partido REAL, pero de otros 2 jugadores --
    # nunca se debe confiar ciegamente en el match_id.
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_match_detail",
        return_value=_detail(player_a_name="Someone Else", player_b_name="Another Person"),
    ):
        save_match_history("Amanda Anisimova", [_match()])
    row = MatchHistory.get(MatchHistory.player_a_name == "Amanda Anisimova")
    assert row.player_b_name == "Sabalenka A."


def test_save_match_history_fills_level_from_catalog_on_resolve():
    # 2026-08-09, pedido explícito del usuario: "esta función también
    # podría verificar el tour y el tournament y por supuesto el level".
    WtaTournamentLevel.create(
        year=2024, tournament_name="Toronto", level="WTA 1000", fetched_at=datetime.now(timezone.utc),
    )
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_match_detail",
        return_value=_detail(),
    ):
        save_match_history("Amanda Anisimova", [_match()])
    row = MatchHistory.get(MatchHistory.match_id == "999")
    assert row.level == "WTA 1000"


def test_save_match_history_fills_tour_bonus_when_unknown_and_level_resolves_unambiguously():
    AtpTournamentLevel.create(
        year=2024, tournament_name="TORONTO", level="M", fetched_at=datetime.now(timezone.utc),
    )
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_match_detail",
        return_value=_detail(tour=None),
    ):
        save_match_history("Amanda Anisimova", [_match()])
    row = MatchHistory.get(MatchHistory.match_id == "999")
    assert row.tour == "ATP"
    assert row.level == "M"
