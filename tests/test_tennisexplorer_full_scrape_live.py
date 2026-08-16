"""Prueba live end-to-end del catálogo y perfiles de TennisExplorer.

No forma parte del suite offline normal porque hace múltiples requests reales.
Ejecutar mostrando las tablas:

    RUN_TENNISEXPLORER_LIVE=1 PLAYER_COUNT=10 \
        python3 -m pytest tests/test_tennisexplorer_full_scrape_live.py -s -q
"""

from __future__ import annotations

import os
import random
import re
from collections import defaultdict
from typing import Any

import pytest

from outer_apis.scrapers.tennis import tennisexplorer_player_scraper as player_scraper
from outer_apis.scrapers.tennis import tennisexplorer_scraper as catalog_scraper


def _natural_name(display_name: str) -> str:
    clean = re.sub(r"\s*\(\d{4}\)\s*$", "", display_name).strip()
    if "," not in clean:
        return clean
    surname, given = (part.strip() for part in clean.split(",", 1))
    return f"{given} {surname}"


def _score_sets(score: str) -> list[tuple[int, int]]:
    sets = []
    for raw_set in (score or "").split(","):
        match = re.match(r"\s*(\d+)\s*-\s*(\d+)", raw_set)
        if match:
            sets.append((int(match.group(1)), int(match.group(2))))
    return sets


def _pct(won: int, total: int) -> str:
    return f"{100 * won / total:.1f}%" if total else "n/a"


def _history_summary(history: list[dict[str, Any]]) -> dict[str, str | int]:
    match_wins = set_wins = set_total = tiebreak_wins = tiebreak_total = 0
    surfaces: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    for match in history:
        sets = _score_sets(match.get("score") or "")
        won_sets = sum(a > b for a, b in sets)
        lost_sets = sum(a < b for a, b in sets)
        won_match = won_sets > lost_sets
        match_wins += won_match
        set_wins += won_sets
        set_total += won_sets + lost_sets

        surface = (match.get("surface") or "unknown").title()
        surfaces[surface][1] += 1
        surfaces[surface][0] += won_match

        for own_games, opponent_games in sets:
            if {own_games, opponent_games} == {6, 7}:
                tiebreak_total += 1
                tiebreak_wins += own_games == 7

    surface_summary = ", ".join(
        f"{surface} {_pct(wins, total)} ({wins}/{total})"
        for surface, (wins, total) in sorted(surfaces.items())
    ) or "n/a"
    return {
        "matches": len(history),
        "match_winrate": _pct(match_wins, len(history)),
        "surface_winrate": surface_summary,
        "set_winrate": _pct(set_wins, set_total),
        "tiebreak_winrate": _pct(tiebreak_wins, tiebreak_total),
        "tiebreak_record": f"{tiebreak_wins}/{tiebreak_total}",
    }


def _profile_for_slug(detail: dict[str, Any], slug: str) -> dict[str, Any]:
    side = "a" if detail.get("player_a_slug") == slug else "b"
    if detail.get(f"player_{side}_slug") != slug:
        return {}
    return {
        "ranking": detail.get(f"player_{side}_ranking"),
        "birthdate": detail.get(f"player_{side}_birthdate"),
        "height_cm": detail.get(f"player_{side}_height_cm"),
        "plays": detail.get(f"player_{side}_plays"),
        "tour": detail.get("tour"),
    }


def _print_table(headers: list[str], rows: list[list[Any]]) -> None:
    rendered = [[str(value if value is not None else "-") for value in row] for row in rows]
    widths = [
        max(len(header), *(len(row[index]) for row in rendered))
        for index, header in enumerate(headers)
    ]
    separator = "+-" + "-+-".join("-" * width for width in widths) + "-+"
    print(separator)
    print("| " + " | ".join(header.ljust(widths[i]) for i, header in enumerate(headers)) + " |")
    print(separator)
    for row in rendered:
        print("| " + " | ".join(value.ljust(widths[i]) for i, value in enumerate(row)) + " |")
    print(separator)


def _run_live_scrape(player_count: int) -> list[dict[str, Any]]:
    country_links = catalog_scraper.get_all_player_country_links()
    assert len(country_links) >= player_count, "No se obtuvieron suficientes países"

    results = []
    for country_url in random.SystemRandom().sample(country_links, len(country_links)):
        if len(results) >= player_count:
            break
        discovered = next(catalog_scraper.iter_all_player_pages([country_url], 2026), None)
        if not discovered:
            continue
        name = _natural_name(discovered["player_name"])
        history = player_scraper.get_player_match_history(
            discovered["slug"], name, years=[2026],
        )
        match = next((item for item in history if item.get("match_id")), None)
        if not history or not match:
            continue
        detail = player_scraper.get_match_detail(match["match_id"])
        if not detail:
            continue
        profile = _profile_for_slug(detail, discovered["slug"])
        if not profile:
            continue
        results.append({
            "country": country_url.split("country=", 1)[-1],
            "name": name,
            **profile,
            **_history_summary(history),
        })
    assert len(results) == player_count, f"Solo se completaron {len(results)}/{player_count} jugadores"
    return results


@pytest.mark.skipif(
    os.getenv("RUN_TENNISEXPLORER_LIVE") != "1",
    reason="requiere RUN_TENNISEXPLORER_LIVE=1 y acceso de red",
)
def test_full_tennisexplorer_player_scrape_live() -> None:
    player_count = int(os.getenv("PLAYER_COUNT", "10"))
    results = _run_live_scrape(player_count)

    print("\nPERFILES OBTENIDOS")
    _print_table(
        ["País", "Jugador", "Tour", "Ranking", "Nacimiento", "Altura", "Mano"],
        [[r["country"], r["name"], r["tour"], r["ranking"], r["birthdate"],
          r["height_cm"], r["plays"]] for r in results],
    )
    print("\nRESUMEN DEL HISTORIAL 2026")
    _print_table(
        ["Jugador", "PJ", "Winrate", "Por superficie", "Sets", "Tiebreaks"],
        [[r["name"], r["matches"], r["match_winrate"], r["surface_winrate"],
          r["set_winrate"], f'{r["tiebreak_winrate"]} ({r["tiebreak_record"]})']
         for r in results],
    )
