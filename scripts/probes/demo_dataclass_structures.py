#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ejemplo de uso de las clases de datos en fetch_match.py
Muestra cómo estructurar la respuesta usando las dataclasses
"""

import sys
import json
from datetime import datetime

sys.path.insert(0, 'c:\\Users\\sandman\\Documents\\Programas\\python\\projects\\kairos')

from src.scrapers.fetch_match import (
    RecentMatch, PlayerMatchStats, H2HData, MatchMetadata, ExtractedMatchData
)

# Crear instancia de datos del match
medvedev_matches = [
    RecentMatch(
        date="2024-01-20",
        tournament="Australian Open",
        opponent="Karen Khachanov",
        score="2-0",
        surface="Hard",
        result="W",
        minutes=145
    ),
    RecentMatch(
        date="2024-01-18",
        tournament="Australian Open",
        opponent="Frances Tiafoe",
        score="2-1",
        surface="Hard",
        result="W",
        minutes=185
    )
]

wawrinka_matches = [
    RecentMatch(
        date="2024-01-20",
        tournament="Australian Open",
        opponent="Cameron Norrie",
        score="2-0",
        surface="Hard",
        result="W",
        minutes=165
    ),
    RecentMatch(
        date="2024-01-18",
        tournament="Australian Open",
        opponent="Jack Draper",
        score="1-2",
        surface="Hard",
        result="L",
        minutes=195
    )
]

# Crear objetos de datos estructurados
player1 = PlayerMatchStats(
    player_name="Daniil Medvedev",
    ranking=5,
    age=28,
    ranking_type="ATP",
    match_wins=0.73,
    match_wins_3_sets=0.45,
    match_wins_5_sets=0.28,
    wins_straight_sets=0.52,
    wins_from_behind=0.28,
    won_at_least_1_set=0.95,
    set_1_win=0.68,
    set_2_win=0.71,
    set_3_win=0.65,
    set_4_win=0.62,
    set_5_win=0.58,
    form_wins_last_10=7,
    form_losses_last_10=3,
    form_win_percentage=0.70,
    days_rest=2,
    matches_last_7d=2,
    total_minutes_7d=380,
    timezone_changes_7d=0,
    distance_traveled_7d=2450.5,
    recent_matches=medvedev_matches
)

player2 = PlayerMatchStats(
    player_name="Stan Wawrinka",
    ranking=47,
    age=38,
    ranking_type="ATP",
    match_wins=0.62,
    match_wins_3_sets=0.32,
    match_wins_5_sets=0.30,
    wins_straight_sets=0.38,
    wins_from_behind=0.32,
    won_at_least_1_set=0.88,
    set_1_win=0.55,
    set_2_win=0.58,
    set_3_win=0.52,
    set_4_win=0.48,
    set_5_win=0.42,
    form_wins_last_10=5,
    form_losses_last_10=5,
    form_win_percentage=0.50,
    days_rest=3,
    matches_last_7d=1,
    total_minutes_7d=210,
    timezone_changes_7d=1,
    distance_traveled_7d=1890.0,
    recent_matches=wawrinka_matches
)

h2h = H2HData(
    player1_wins=3,
    player2_wins=0,
    win_percentage_12m=1.0
)

match_info = MatchMetadata(
    tournament="Australian Open 2024",
    tournament_location="Melbourne, Australia",
    surface="Hard",
    match_date="2024-01-22",
    match_time="14:00"
)

# Crear objeto con toda la informacion del match
extracted_data = ExtractedMatchData(
    player1=player1,
    player2=player2,
    h2h=h2h,
    match_info=match_info,
    provider="gemini",
    response_time=4.82
)

# Mostrar el resultado
print("[DEMO] Ejemplo de estructura con clases dataclass")
print("=" * 80)

print("\nPlayer 1:")
print(f"  Nombre: {player1.player_name}")
print(f"  Ranking: #{player1.ranking} ({player1.ranking_type})")
print(f"  Edad: {player1.age}")
print(f"  Match Win Rate: {player1.match_wins:.1%}")
print(f"  Set 1-5 Winrates: {player1.set_1_win:.1%} - {player1.set_2_win:.1%} - {player1.set_3_win:.1%} - {player1.set_4_win:.1%} - {player1.set_5_win:.1%}")
print(f"  Form (últimos 10): {player1.form_wins_last_10}-{player1.form_losses_last_10}")
print(f"  Descanso: {player1.days_rest} días, {player1.matches_last_7d} matches en 7d")
print(f"  Matches recientes: {len(player1.recent_matches)}")

print("\nH2H:")
print(f"  {player1.player_name}: {h2h.player1_wins}")
print(f"  {player2.player_name}: {h2h.player2_wins}")
print(f"  Win % (12 meses): {h2h.win_percentage_12m:.1%}")

print("\nMatch Info:")
print(f"  Torneo: {match_info.tournament}")
print(f"  Ubicacion: {match_info.tournament_location}")
print(f"  Superficie: {match_info.surface}")
print(f"  Fecha: {match_info.match_date} a las {match_info.match_time}")

print("\n" + "=" * 80)
print("Convertiendo a diccionario para JSON:")
print("=" * 80)

data_dict = extracted_data.to_dict()
print(json.dumps(data_dict, indent=2))

print("\n" + "=" * 80)
print(f"Total de campos: {len(data_dict)}")
print("Estructura lista para guardar en database.py")
