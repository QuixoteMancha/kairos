"""Tests de `outer_apis/kalshi/markets/tennis_set_winner.py` -- 2026-09-01,
pairing de mercados reales de Kalshi "quién gana el set"
(`KXATPSETWINNER`/`KXWTASETWINNER`), verificado en vivo contra la API
pública (ver docstring del módulo para la estructura real confirmada)."""

from __future__ import annotations

from outer_apis.kalshi.markets.tennis_set_winner import (
    build_tennis_set_markets, norm_player_name,
)


def _leg(event_ticker, ticker, sub_title, *, price=0.5, title=None,
         rules_secondary="Valencia Open Semifinal", close_time="2026-09-05T18:00:00Z"):
    return {
        "id": ticker, "ticker": ticker, "current_a_price": price,
        "current_b_price": round(1 - price, 4), "close_time": close_time,
        "raw": {
            "event_ticker": event_ticker, "ticker": ticker,
            "title": title or f"Will {sub_title} win set 4 in Valdez vs Montero match?",
            "yes_sub_title": sub_title, "rules_secondary": rules_secondary,
        },
    }


def test_pairs_two_legs_with_set_number_from_event_ticker():
    raw = [
        _leg("KXATPSETWINNER-26SEP01VALMON-4", "KXATPSETWINNER-26SEP01VALMON-4-VAL", "Valdez", price=0.62),
        _leg("KXATPSETWINNER-26SEP01VALMON-4", "KXATPSETWINNER-26SEP01VALMON-4-MON", "Montero", price=0.41),
    ]
    markets = build_tennis_set_markets(raw)
    assert len(markets) == 1
    m = markets[0]
    assert m["set_number"] == 4
    assert m["tour"] == "ATP"
    assert m["player_a_name"] == "Valdez"
    assert m["player_b_name"] == "Montero"
    assert m["current_a_price"] == 0.62
    assert m["current_b_price"] == 0.41
    assert m["ticker_a"] == "KXATPSETWINNER-26SEP01VALMON-4-VAL"
    assert m["ticker_b"] == "KXATPSETWINNER-26SEP01VALMON-4-MON"


def test_wta_series_maps_to_wta_tour():
    raw = [
        _leg("KXWTASETWINNER-26SEP01ABCXYZ-1", "KXWTASETWINNER-26SEP01ABCXYZ-1-ABC", "Player Abc"),
        _leg("KXWTASETWINNER-26SEP01ABCXYZ-1", "KXWTASETWINNER-26SEP01ABCXYZ-1-XYZ", "Player Xyz"),
    ]
    markets = build_tennis_set_markets(raw)
    assert len(markets) == 1
    assert markets[0]["tour"] == "WTA"
    assert markets[0]["set_number"] == 1


def test_fails_closed_when_only_one_leg_present():
    raw = [
        _leg("KXATPSETWINNER-26SEP01VALMON-2", "KXATPSETWINNER-26SEP01VALMON-2-VAL", "Valdez"),
    ]
    assert build_tennis_set_markets(raw) == []


def test_fails_closed_when_event_ticker_has_no_numeric_set_suffix_and_title_missing_it_too():
    raw = [
        _leg(
            "KXATPSETWINNER-26SEP01VALMON-", "KXATPSETWINNER-26SEP01VALMON--VAL", "Valdez",
            title="Will Valdez win the Valdez vs Montero match?",
        ),
        _leg(
            "KXATPSETWINNER-26SEP01VALMON-", "KXATPSETWINNER-26SEP01VALMON--MON", "Montero",
            title="Will Montero win the Valdez vs Montero match?",
        ),
    ]
    assert build_tennis_set_markets(raw) == []


def test_ignores_markets_outside_the_set_winner_series():
    raw = [
        _leg("KXATPMATCH-26SEP01VALMON", "KXATPMATCH-26SEP01VALMON-VAL", "Valdez",
             title="Will Valdez win the Valdez vs Montero: Semifinal match?"),
        _leg("KXATPMATCH-26SEP01VALMON", "KXATPMATCH-26SEP01VALMON-MON", "Montero",
             title="Will Montero win the Valdez vs Montero: Semifinal match?"),
    ]
    assert build_tennis_set_markets(raw) == []


def test_norm_player_name_ignores_accents_and_case():
    assert norm_player_name("José Ángel Núñez") == norm_player_name("jose angel nunez")
