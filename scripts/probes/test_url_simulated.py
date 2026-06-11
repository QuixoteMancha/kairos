#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test con respuesta simulada pero realista basada en el schema real
Simula lo que fetch_match.py retornaría si los providers funcionaran
"""

import json
from datetime import datetime

# Datos simulados basados en el schema real de fetch_match.py
simulated_response = {
    "success": True,
    "data": {
        "player1_name": "Daniil Medvedev",
        "player2_name": "Stan Wawrinka",
        "player1_ranking": 5,
        "player2_ranking": 47,
        "player1_age": 28,
        "player2_age": 38,
        "player1_ranking_type": "ATP",
        "player2_ranking_type": "ATP",
        "tournament": "Australian Open 2024",
        "tournament_location": "Melbourne, Australia",
        "surface": "Hard",
        "match_date": "2024-01-22",
        "match_time": "14:00",
        "h2h_player1_wins": 3,
        "h2h_player2_wins": 0,
        "h2h_win_percentage_12m": 1.0,
        "player1_match_wins": 0.73,
        "player2_match_wins": 0.62,
        "player1_set_1_win": 0.68,
        "player2_set_1_win": 0.55,
        "player1_set_2_win": 0.71,
        "player2_set_2_win": 0.58,
        "player1_form_wins_last_10": 7,
        "player1_form_losses_last_10": 3,
        "player2_form_wins_last_10": 5,
        "player2_form_losses_last_10": 5,
        "player1_days_rest": 2,
        "player2_days_rest": 3,
        "player1_matches_last_7d": 2,
        "player2_matches_last_7d": 1,
        "player1_total_minutes_7d": 380,
        "player2_total_minutes_7d": 210,
        "player1_wins_straight_sets": 0.52,
        "player2_wins_straight_sets": 0.38,
        "player1_wins_from_behind": 0.28,
        "player2_wins_from_behind": 0.32,
        "player1_recent_matches": [
            {
                "date": "2024-01-20",
                "tournament": "Australian Open",
                "opponent": "Karen Khachanov",
                "score": "2-0",
                "surface": "Hard",
                "result": "W",
                "minutes": 145
            },
            {
                "date": "2024-01-18",
                "tournament": "Australian Open",
                "opponent": "Frances Tiafoe",
                "score": "2-1",
                "surface": "Hard",
                "result": "W",
                "minutes": 185
            },
            {
                "date": "2024-01-15",
                "tournament": "Australian Open",
                "opponent": "Sebastian Korda",
                "score": "2-0",
                "surface": "Hard",
                "result": "W",
                "minutes": 125
            }
        ],
        "player2_recent_matches": [
            {
                "date": "2024-01-20",
                "tournament": "Australian Open",
                "opponent": "Cameron Norrie",
                "score": "2-0",
                "surface": "Hard",
                "result": "W",
                "minutes": 165
            },
            {
                "date": "2024-01-18",
                "tournament": "Australian Open",
                "opponent": "Jack Draper",
                "score": "1-2",
                "surface": "Hard",
                "result": "L",
                "minutes": 195
            },
            {
                "date": "2024-01-16",
                "tournament": "Australian Open",
                "opponent": "Pedro Martinez",
                "score": "2-1",
                "surface": "Hard",
                "result": "W",
                "minutes": 155
            }
        ]
    },
    "provider": "gemini",
    "response_time": 4.82,
    "test_timestamp": datetime.now().isoformat(),
    "test_url": "https://sofascore.com/h2h/daniil-medvedev-vs-stan-wawrinka-47666",
    "test_note": "Respuesta simulada basada en schema real de fetch_match.py"
}

output_file = "test_response_simulated.json"

# Guardar respuesta en archivo
with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(simulated_response, f, indent=2, default=str)

print(f"[TEST] Archivo guardado: {output_file}")
print(f"[TEST] Tamanio: {len(json.dumps(simulated_response))} bytes")
print(f"[TEST] Campos extraidos: {len(simulated_response['data'])}")
print(f"[TEST] [OK] Respuesta lista para usar")
