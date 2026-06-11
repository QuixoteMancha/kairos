#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test real con URL de stats source (Sofascore/Flashscore)
Extrae datos y guarda respuesta en archivo
"""

import asyncio
import sys
import json
from datetime import datetime

# Agregar src al path
sys.path.insert(0, 'c:\\Users\\sandman\\Documents\\Programas\\python\\projects\\kairos')

# (TennisStats-specific fetch removed. This probe is legacy.)
# Use current player_stats or scanning flows for Sofascore/Flashscore data extraction.


async def main():
    print("[TEST] TennisStats probe removed (source no longer used).")
    print("Use src/scrapers/player_stats or SofascoreScraper for current stats sources.")
    return
    url = "https://www.sofascore.com/h2h/..."  # example for current
    output_file = "test_response.json"
        
        # Agregar timestamp
        result["test_timestamp"] = datetime.now().isoformat()
        result["test_url"] = url
        
        # Guardar respuesta en archivo
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, default=str)
        
        print(f"[TEST] [OK] Respuesta guardada en {output_file}\n")
        
        # Mostrar resumen
        if result.get("success"):
            print("[TEST] [OK] Extraccion exitosa")
            print(f"[TEST] Provider usado: {result.get('provider')}")
            print(f"[TEST] Tiempo de respuesta: {result.get('response_time', 0):.2f}s")
            data = result.get('data', {})
            print(f"[TEST] Campos extraidos: {len(data)}")
            print(f"[TEST] Jugadores: {data.get('player1_name')} vs {data.get('player2_name')}")
            print(f"[TEST] Torneo: {data.get('tournament')} ({data.get('surface')})")
        else:
            print(f"[TEST] [ER] Error: {result.get('error')}")
            
    except Exception as e:
        print(f"[TEST] [ER] Excepcion: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())


