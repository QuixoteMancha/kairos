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
from database.historical.tennis import MatchHistory, Player
from database.historical.tennis.writer import (
    save_match_history, _identity_matches_detail_side, _find_existing_match_history_row, _names_confirm_identity,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        Player.delete().execute()
    yield
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        Player.delete().execute()


def _make_player(name):
    existing = Player.get_or_none(Player.canonical_name == name)
    if existing is not None:
        return existing
    return Player.create(canonical_name=name, created_at=datetime.now(timezone.utc))


def _get_row_for_player(player_name: str):
    pid = Player.get(Player.canonical_name == player_name).id
    return MatchHistory.get_or_none(
        (MatchHistory.player_a == pid) | (MatchHistory.player_b == pid)
    )


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
    assert row.player_a.canonical_name == "Anisimova Amanda"  # nombre COMPLETO real, no abreviado
    assert row.player_b.canonical_name == "Sabalenka Aryna"
    assert row.tour == "WTA"
    # 2026-08-11: `tournament` es FK real ahora (ver
    # writer.py::_resolve_tournament_id), no texto libre.
    assert row.tournament.tournament_name == "Toronto WTA"


def test_save_match_history_falls_back_to_abbreviated_when_match_id_missing():
    save_match_history("Amanda Anisimova", [_match(match_id=None)])
    row = _get_row_for_player("Amanda Anisimova")
    assert row.player_b.canonical_name == "Sabalenka A."  # abreviado, comportamiento previo intacto


def test_save_match_history_falls_back_when_match_detail_fetch_fails():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_match_detail", return_value=None,
    ):
        save_match_history("Amanda Anisimova", [_match()])
    row = _get_row_for_player("Amanda Anisimova")
    assert row.player_b.canonical_name == "Sabalenka A."


def test_save_match_history_falls_back_when_identity_does_not_confirm():
    # match_id apunta a un partido REAL, pero de otros 2 jugadores --
    # nunca se debe confiar ciegamente en el match_id.
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.get_match_detail",
        return_value=_detail(player_a_name="Someone Else", player_b_name="Another Person"),
    ):
        save_match_history("Amanda Anisimova", [_match()])
    row = _get_row_for_player("Amanda Anisimova")
    assert row.player_b.canonical_name == "Sabalenka A."


def test_find_existing_match_history_row_root_cause_fix_2026_08_10():
    # 2026-08-10, FIX DE CAUSA RAÍZ de la triplicación real encontrada
    # en producción (ver kairos-matchhistory-triplication-dedup):
    # `_find_existing_match_history_row` comparaba por igualdad de
    # string EXACTA -- el mismo partido real, escrito por el fetch de
    # CADA jugador, casi siempre difiere en formato de nombre ("Elina
    # Svitolina" vs "Svitolina Elina", mayúsculas del rival distintas)
    # y nunca se encontraba a sí mismo -- generaba una fila nueva cada
    # vez (triplicación real verificada en producción). Ahora debe
    # encontrarla sin importar el formato.
    MatchHistory.create(
        match_id="recentform:x", player_a=_make_player("Elina Svitolina"), player_b=_make_player("Alexandrova E."),
        match_date="2026-08-11", source="tennisexplorer", fetched_at=datetime.now(timezone.utc),
    )
    found = _find_existing_match_history_row("Ekaterina Alexandrova", "Svitolina E.", "2026-08-11")
    assert found is not None
    assert found.match_id == "recentform:x"


def test_find_existing_match_history_row_does_not_collide_same_surname_different_person():
    # mismo criterio estricto que `_names_confirm_identity` -- no debe
    # confundir 2 personas reales distintas con el mismo apellido (ver
    # kairos-critical-misattribution-bug-2026-08-06, Casper/Christian Ruud).
    MatchHistory.create(
        match_id="1", player_a=_make_player("Christian Ruud"), player_b=_make_player("Someone Rival"),
        match_date="2026-08-11", source="tennisexplorer", fetched_at=datetime.now(timezone.utc),
    )
    assert _find_existing_match_history_row("Casper Ruud", "Someone Rival", "2026-08-11") is None


def test_save_match_history_no_longer_triplicates_across_name_formats():
    # test end-to-end del fix real: 2 llamadas a save_match_history con
    # el MISMO partido real, cada una desde la perspectiva de un
    # jugador distinto (formato de nombre distinto) -- antes del fix
    # esto generaba 2 filas; ahora debe generar 1 sola.
    save_match_history("Elina Svitolina", [
        {"oponente": "Alexandrova E.", "fecha": "2026-08-11", "resultado": "W", "score": "6-4, 6-2",
         "source": "tennisexplorer", "match_id": None},
    ])
    save_match_history("Ekaterina Alexandrova", [
        {"oponente": "Svitolina E.", "fecha": "2026-08-11", "resultado": "L", "score": "4-6, 2-6",
         "source": "tennisexplorer", "match_id": None},
    ])
    assert MatchHistory.select().count() == 1
