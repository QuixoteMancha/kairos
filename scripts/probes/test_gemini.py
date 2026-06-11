#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script para testear respuesta de Gemini
"""

import sys
import asyncio
import json
import io

# Redirigir stdout a UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, '.')

from src.scrapers.fetch_match import extract_match_with_provider
from src.core.config import GOOGLE_API_KEY

async def test_gemini():
    """Testea Gemini con un match real"""
    
    # URL de prueba (match conocido)
    test_url = "https://sofascore.com/h2h/Djokovic-Nadal"
    
    print("=" * 60)
    print("TEST GEMINI API")
    print("=" * 60)
    print()
    print(f"API Key: {GOOGLE_API_KEY[:20]}...")
    print(f"URL: {test_url}")
    print()
    
    result = await extract_match_with_provider(
        match_url=test_url,
        use_auth=False,
        provider="gemini"
    )
    
    print("=" * 60)
    print("RESULTADO:")
    print("=" * 60)
    # Guardar a archivo para evitar problemas de encoding en consola
    with open('test_result.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, default=str, ensure_ascii=False)
    
    # Mostrar resumen
    print(f"Success: {result.get('success')}")
    print(f"Provider: {result.get('provider')}")
    print(f"Response time: {result.get('response_time', 0):.2f}s")
    print()
    
    if result.get('success'):
        print("[OK] Extracción exitosa")
        data = result.get('data', {})
        print(f"[OK] Datos extraídos: {len(data)} campos")
    else:
        print(f"[ERROR] {result.get('error', 'Unknown error')}")
        print(f"[ERROR] Rate limit: {result.get('rate_limit', False)}")
    
    print()
    print("[INFO] Resultado completo guardado en: test_result.json")

if __name__ == '__main__':
    asyncio.run(test_gemini())
