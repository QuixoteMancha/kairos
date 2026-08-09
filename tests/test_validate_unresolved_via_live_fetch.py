"""Tests del script de validación en vivo de `TennisMatchHistoryUnvalidated`
-- 2026-08-09, pedido explícito del usuario: "dejar un script corriendo
en la mini-pc que vaya validando con la página de match-details...
delays aleatorios y paralelismo pero sin hitear tanto por minuto"."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import MatchHistory, TennisExplorerPlayerSlug, TennisMatchHistoryUnvalidated
from scripts.tennis.validate_unresolved_via_live_fetch import (
    find_matching_candidate, process_player, _get_candidate_slugs, _surname_key_from_te_name,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        TennisMatchHistoryUnvalidated.delete().execute()
        TennisExplorerPlayerSlug.delete().execute()
    yield
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        TennisMatchHistoryUnvalidated.delete().execute()
        TennisExplorerPlayerSlug.delete().execute()


def _now():
    return datetime.now(timezone.utc)


def _unvalidated(player_a, player_b_or_abbrev, is_full_name, match_date, tournament="Toronto WTA", reason="no_sackmann_match"):
    return TennisMatchHistoryUnvalidated.create(
        match_date=match_date, tournament=tournament, player_a_name=player_a,
        player_b_name_or_abbrev=player_b_or_abbrev, player_b_is_full_name=is_full_name,
        reason=reason, fetched_at=_now(),
    )


def test_find_matching_candidate_resolves_singleton_by_abbreviation():
    c = _unvalidated("Amanda Anisimova", "Sabalenka A.", is_full_name=False, match_date="2024-08-10")
    parsed_row = {"opponent": "Sabalenka A."}
    match = find_matching_candidate(parsed_row, "2024-08-10", [c])
    assert match.id == c.id


def test_find_matching_candidate_resolves_paired_by_computed_abbreviation():
    c = _unvalidated("Amanda Anisimova", "Aryna Sabalenka", is_full_name=True, match_date="2024-08-10")
    parsed_row = {"opponent": "Sabalenka A."}
    match = find_matching_candidate(parsed_row, "2024-08-10", [c])
    assert match.id == c.id


def test_find_matching_candidate_none_when_date_differs():
    c = _unvalidated("Amanda Anisimova", "Sabalenka A.", is_full_name=False, match_date="2024-08-10")
    parsed_row = {"opponent": "Sabalenka A."}
    assert find_matching_candidate(parsed_row, "2024-08-11", [c]) is None


def test_find_matching_candidate_none_when_opponent_differs():
    c = _unvalidated("Amanda Anisimova", "Sabalenka A.", is_full_name=False, match_date="2024-08-10")
    parsed_row = {"opponent": "Otra X."}
    assert find_matching_candidate(parsed_row, "2024-08-10", [c]) is None


def test_find_matching_candidate_fail_closed_on_ambiguity():
    c1 = _unvalidated("Amanda Anisimova", "Sabalenka A.", is_full_name=False, match_date="2024-08-10", tournament="T1")
    c2 = _unvalidated("Amanda Anisimova", "Sabalenka A.", is_full_name=False, match_date="2024-08-10", tournament="T2")
    parsed_row = {"opponent": "Sabalenka A."}
    assert find_matching_candidate(parsed_row, "2024-08-10", [c1, c2]) is None


_DETAIL = {
    "match_id": "999", "match_date": "2024-08-10", "tournament": "Toronto WTA", "round": "QF",
    "surface": "hard", "score": "6-4, 6-2", "winner_side": "a",
    "player_a_name": "Amanda Anisimova", "player_a_slug": "anisimova", "player_a_ranking": 10,
    "player_a_birthdate": None, "player_a_height_cm": None, "player_a_weight_kg": None, "player_a_plays": None,
    "player_b_name": "Aryna Sabalenka", "player_b_slug": "sabalenka", "player_b_ranking": 1,
    "player_b_birthdate": None, "player_b_height_cm": None, "player_b_weight_kg": None, "player_b_plays": None,
}


def test_process_player_resolves_and_removes_from_unvalidated():
    c = _unvalidated("Amanda Anisimova", "Sabalenka A.", is_full_name=False, match_date="2024-08-10")
    fake_page_row = {
        "date": "10.08.", "opponent": "Sabalenka A.", "match_id": "999", "tournament": "Toronto WTA",
        "surface": "hard", "round": "QF", "round_full": "quarterfinal", "score": "6-4, 6-2",
        "odds_self": None, "odds_opponent": None,
    }
    with patch(
        "scripts.tennis.validate_unresolved_via_live_fetch.resolve_player_slug", return_value="anisimova",
    ), patch(
        "scripts.tennis.validate_unresolved_via_live_fetch._fetch", return_value="<html>x</html>",
    ), patch(
        "scripts.tennis.validate_unresolved_via_live_fetch._parse_year_html", return_value=[fake_page_row],
    ), patch(
        "scripts.tennis.validate_unresolved_via_live_fetch.get_match_detail", return_value=_DETAIL,
    ), patch(
        "scripts.tennis.validate_unresolved_via_live_fetch._extra_delay",
    ):
        result = process_player("Amanda Anisimova", [c])

    assert result["resolved"] == 1
    assert result["unresolved"] == 0
    assert TennisMatchHistoryUnvalidated.select().where(TennisMatchHistoryUnvalidated.id == c.id).count() == 0
    assert MatchHistory.select().where(MatchHistory.match_id == "999").count() == 1


def test_process_player_leaves_unresolved_when_slug_not_found():
    c = _unvalidated("Nobody Known", "Sabalenka A.", is_full_name=False, match_date="2024-08-10")
    with patch(
        "scripts.tennis.validate_unresolved_via_live_fetch.resolve_player_slug", return_value=None,
    ), patch(
        "scripts.tennis.validate_unresolved_via_live_fetch._extra_delay",
    ):
        result = process_player("Nobody Known", [c])

    assert result["resolved"] == 0
    assert result["unresolved"] == 1
    assert TennisMatchHistoryUnvalidated.select().where(TennisMatchHistoryUnvalidated.id == c.id).count() == 1


def test_surname_key_from_te_name_drops_last_token():
    # 2026-08-09, bug real: convención TE es "Apellido(s) Nombre" (ej.
    # "Budkov Kjaer Nicolai" -- apellido real "Budkov Kjaer", nombre
    # "Nicolai"), al REVÉS de la convención del roster WTA oficial.
    assert _surname_key_from_te_name("Budkov Kjaer Nicolai") == "budkov kjaer"
    assert _surname_key_from_te_name("Djokovic Novak") == "djokovic"
    assert _surname_key_from_te_name("SinApellido") == ""


def test_get_candidate_slugs_finds_previously_harvested_slug():
    now = _now()
    TennisExplorerPlayerSlug.create(surname_key="budkov kjaer", slug="budkov-kjaer", discovered_at=now)
    assert _get_candidate_slugs("Budkov Kjaer Nicolai") == ["budkov-kjaer"]
    assert _get_candidate_slugs("Nadie Conocido") == []


def test_process_player_uses_harvested_candidate_slugs_when_naive_guess_fails():
    # 2026-08-09, reproduce el bug real de producción: `resolve_player_
    # slug` fallaba (naive guess no encuentra "Budkov Kjaer Nicolai")
    # porque `process_player` nunca le pasaba `candidate_slugs` --
    # `process_player` abortaba con 0 resueltos pese a existir un slug
    # REAL ya cosechado en `TennisExplorerPlayerSlug`.
    TennisExplorerPlayerSlug.create(surname_key="budkov kjaer", slug="budkov-kjaer", discovered_at=_now())
    c = _unvalidated("Budkov Kjaer Nicolai", "Herbert P.", is_full_name=False, match_date="2024-08-10")
    fake_page_row = {
        "date": "10.08.", "opponent": "Herbert P.", "match_id": "999", "tournament": "Toronto WTA",
        "surface": "hard", "round": "QF", "round_full": "quarterfinal", "score": "6-4, 6-2",
        "odds_self": None, "odds_opponent": None,
    }
    detail = {
        **_DETAIL, "player_a_name": "Budkov Kjaer Nicolai", "player_a_slug": "budkov-kjaer",
        "player_b_name": "Herbert Pierre-Hugues", "player_b_slug": "herbert-fa5ad",
    }

    def _fake_resolve(name, candidate_slugs=()):
        # simula la heurística naive fallando SIEMPRE -- solo resuelve
        # si recibe el slug real vía `candidate_slugs` (el fix real).
        return candidate_slugs[0] if candidate_slugs else None

    with patch(
        "scripts.tennis.validate_unresolved_via_live_fetch.resolve_player_slug", side_effect=_fake_resolve,
    ), patch(
        "scripts.tennis.validate_unresolved_via_live_fetch._fetch", return_value="<html>x</html>",
    ), patch(
        "scripts.tennis.validate_unresolved_via_live_fetch._parse_year_html", return_value=[fake_page_row],
    ), patch(
        "scripts.tennis.validate_unresolved_via_live_fetch.get_match_detail", return_value=detail,
    ), patch(
        "scripts.tennis.validate_unresolved_via_live_fetch._extra_delay",
    ):
        result = process_player("Budkov Kjaer Nicolai", [c])

    assert result["resolved"] == 1
    assert result["unresolved"] == 0
    assert MatchHistory.select().where(MatchHistory.match_id == "999").count() == 1


def test_process_player_harvests_real_slugs_of_both_players_on_resolve():
    c = _unvalidated("Amanda Anisimova", "Sabalenka A.", is_full_name=False, match_date="2024-08-10")
    fake_page_row = {
        "date": "10.08.", "opponent": "Sabalenka A.", "match_id": "999", "tournament": "Toronto WTA",
        "surface": "hard", "round": "QF", "round_full": "quarterfinal", "score": "6-4, 6-2",
        "odds_self": None, "odds_opponent": None,
    }
    with patch(
        "scripts.tennis.validate_unresolved_via_live_fetch.resolve_player_slug", return_value="anisimova",
    ), patch(
        "scripts.tennis.validate_unresolved_via_live_fetch._fetch", return_value="<html>x</html>",
    ), patch(
        "scripts.tennis.validate_unresolved_via_live_fetch._parse_year_html", return_value=[fake_page_row],
    ), patch(
        "scripts.tennis.validate_unresolved_via_live_fetch.get_match_detail", return_value=_DETAIL,
    ), patch(
        "scripts.tennis.validate_unresolved_via_live_fetch._extra_delay",
    ):
        process_player("Amanda Anisimova", [c])

    slugs = {r.surname_key: r.slug for r in TennisExplorerPlayerSlug.select()}
    # `_surname_key_from_te_name` toma todo menos el ÚLTIMO token -- para
    # "Amanda Anisimova"/"Aryna Sabalenka" (fixture existente en este
    # archivo, orden "Nombre Apellido") eso da "amanda"/"aryna".
    assert slugs["amanda"] == "anisimova"
    assert slugs["aryna"] == "sabalenka"


def test_process_player_rejects_match_detail_pointing_elsewhere():
    c = _unvalidated("Amanda Anisimova", "Sabalenka A.", is_full_name=False, match_date="2024-08-10")
    fake_page_row = {
        "date": "10.08.", "opponent": "Sabalenka A.", "match_id": "999", "tournament": "Toronto WTA",
        "surface": "hard", "round": "QF", "round_full": "quarterfinal", "score": "6-4, 6-2",
        "odds_self": None, "odds_opponent": None,
    }
    wrong_detail = {**_DETAIL, "player_a_name": "Someone Else", "player_b_name": "Another Person"}
    with patch(
        "scripts.tennis.validate_unresolved_via_live_fetch.resolve_player_slug", return_value="anisimova",
    ), patch(
        "scripts.tennis.validate_unresolved_via_live_fetch._fetch", return_value="<html>x</html>",
    ), patch(
        "scripts.tennis.validate_unresolved_via_live_fetch._parse_year_html", return_value=[fake_page_row],
    ), patch(
        "scripts.tennis.validate_unresolved_via_live_fetch.get_match_detail", return_value=wrong_detail,
    ), patch(
        "scripts.tennis.validate_unresolved_via_live_fetch._extra_delay",
    ):
        result = process_player("Amanda Anisimova", [c])

    assert result["resolved"] == 0
    assert result["unresolved"] == 1
    assert TennisMatchHistoryUnvalidated.select().where(TennisMatchHistoryUnvalidated.id == c.id).count() == 1
