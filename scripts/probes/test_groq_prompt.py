#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST PROMPT GROQ - Test directo del prompt usado en fetch_matches.py
"""

import asyncio
import json
from datetime import datetime
from litellm import completion

from src.core.config import GROQ_API_KEY

async def test_groq_prompt():
    """Test directo del prompt con Groq"""

    # Simular contenido HTML de external stats source (ejemplo simplificado)
    mock_html_content = """
    <!DOCTYPE html>
    <html>
    <head><title>external stats source - Today's Matches</title></head>
    <body>
        <h1>Today's Matches - February 25, 2026</h1>

        <div class="match-list">
            <div class="match">
                <div class="players">Carlos Alcaraz vs Novak Djokovic</div>
                <div class="tournament">ATP Miami Open</div>
                <div class="location">Miami, USA</div>
                <div class="surface">Hard</div>
                <div class="time">15:30</div>
                <a href="/match/12345">View Match</a>
            </div>

            <div class="match">
                <div class="players">Aryna Sabalenka vs Iga Swiatek</div>
                <div class="tournament">WTA Miami Open</div>
                <div class="location">Miami, USA</div>
                <div class="surface">Hard</div>
                <div class="time">17:00</div>
                <a href="/match/12346">View Match</a>
            </div>

            <div class="match">
                <div class="players">Daniil Medvedev vs Alexander Zverev</div>
                <div class="tournament">ATP Dubai Championships</div>
                <div class="location">Dubai, UAE</div>
                <div class="surface">Hard</div>
                <div class="time">14:00</div>
                <a href="/match/12347">View Match</a>
            </div>
        </div>

        <div class="past-matches">
            <h2>Yesterday's Results</h2>
            <!-- Resultados pasados -->
        </div>
    </body>
    </html>
    """

# El mismo prompt simplificado que ahora usamos
    prompt = """EXTRAER DATOS DE PARTIDOS DE TENIS

Analiza el contenido HTML de external stats source y devuelve ÚNICAMENTE un objeto JSON válido con todos los partidos programados para HOY.

FORMATO DE SALIDA REQUERIDO (SOLO JSON, sin texto adicional):
{
  "fecha": "2026-02-25",
  "matches": [
    {
      "player1": "Carlos Alcaraz",
      "player2": "Novak Djokovic",
      "tournament": "ATP Miami Open",
      "location": "Miami, USA",
      "surface": "Hard",
      "hora": "15:30",
      "match_url": "https://www.sofascore.com/match/12345"
    }
  ]
}

INSTRUCCIONES:
1. Busca partidos en secciones como "Today's Matches", "match-list", etc.
2. Extrae exactamente estos campos para cada partido de HOY
3. Usa "" para campos faltantes
4. Incluye TODOS los partidos encontrados
5. NO incluyas partidos pasados o futuros
6. Devuelve SOLO el JSON, sin explicaciones ni código

Contenido de la página:
""" + mock_html_content

    print("🔍 TESTEANDO PROMPT CON GROQ")
    print("=" * 50)
    print(f"Prompt length: {len(prompt)} caracteres")
    print()

    try:
        # Llamar directamente a Groq
        response = await asyncio.to_thread(
            completion,
            model="groq/llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            api_key=GROQ_API_KEY,
            temperature=0.1
        )

        result = response.choices[0].message.content
        print("📝 RESPUESTA DE GROQ:")
        print("-" * 30)
        print(result)
        print()

        # Intentar extraer JSON de bloques de código markdown si existe
        json_text = result.strip()
        if json_text.startswith('```json'):
            json_text = json_text[7:]  # Remover ```json
        if json_text.startswith('```'):
            json_text = json_text[3:]  # Remover ```
        if json_text.endswith('```'):
            json_text = json_text[:-3]  # Remover ```
        json_text = json_text.strip()

        # Intentar parsear como JSON
        try:
            parsed = json.loads(json_text)
            print("✅ JSON VÁLIDO (después de limpiar markdown)")
            print(f"Fecha: {parsed.get('fecha', 'N/A')}")
            print(f"Número de matches: {len(parsed.get('matches', []))}")

            for i, match in enumerate(parsed.get('matches', [])):
                print(f"  Match {i+1}: {match.get('player1', '?')} vs {match.get('player2', '?')} - {match.get('tournament', '?')}")

        except json.JSONDecodeError as e:
            print("❌ JSON INVÁLIDO")
            print(f"Error: {e}")
            print(f"Texto recibido: {json_text[:200]}...")

    except Exception as e:
        print(f"❌ ERROR LLAMANDO A GROQ: {e}")

if __name__ == "__main__":
    asyncio.run(test_groq_prompt())