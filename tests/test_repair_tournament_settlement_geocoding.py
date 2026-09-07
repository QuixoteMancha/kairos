from scripts.tennis.repair_tournament_settlement_geocoding import is_verified_wrong_coordinate, parse_target


def _reference():
    return {"lat": 45.5398, "lon": 10.2200}


def test_verified_wrong_coordinate_needs_all_three_independent_signals():
    verified, evidence = is_verified_wrong_coordinate(
        "Brescia", 45.77958, 10.425873, [1149], _reference(), 149,
        {"address": {"town": "Lavenone", "country": "Italy"}},
    )

    assert verified is True
    assert evidence["stored_coordinate_locality_matches"] is False


def test_same_city_reverse_result_prevents_automatic_repair():
    verified, _ = is_verified_wrong_coordinate(
        "Denver", 39.739235, -104.98486, [1615], {"lat": 39.0, "lon": -100.0}, 288,
        {"address": {"city": "Denver", "country": "United States"}},
    )

    assert verified is False


def test_target_parser_requires_city_and_country():
    assert parse_target("Brescia|Italy") == ("Brescia", "Italy")
