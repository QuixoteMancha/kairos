#!/usr/bin/env python3
"""Test rápido del fallback scraper sin LLM"""
import asyncio
from src.scrapers.fallback_scraper import fetch_matches_fallback

async def main():
    print("🧪 Test Fallback: Requests + BeautifulSoup (sin LLM)")
    result = await fetch_matches_fallback(use_selenium=False, use_llm=False)
    
    print(f"\n📊 Resultado:")
    print(f"  Método: {result['method']}")
    print(f"  Éxito: {result['success']}")
    print(f"  Partidos: {len(result['matches'])}")
    
    if result['matches']:
        # Filtrar solo singles (sin " - " en el nombre)
        singles = [m for m in result['matches'] if ' - ' not in m.get('player1', '')]
        doubles = [m for m in result['matches'] if ' - ' in m.get('player1', '')]
        
        print(f"\n  Singles: {len(singles)}")
        print(f"  Dobles: {len(doubles)}")
        
        print("\n🎾 SINGLES (primeros 10):")
        for i, m in enumerate(singles[:10], 1):
            p1 = m.get('player1', '?')
            p2 = m.get('player2', '?')
            hora = m.get('hora', '')
            rank1 = m.get('rank1', '')
            rank2 = m.get('rank2', '')
            odds1 = m.get('odds1', '')
            odds2 = m.get('odds2', '')
            
            rank_str = f"[{rank1}]" if rank1 else ""
            rank_str2 = f"[{rank2}]" if rank2 else ""
            odds_str = f"({odds1}/{odds2})" if odds1 and odds2 else ""
            
            print(f"  {i}. {p1} {rank_str} vs {p2} {rank_str2} {hora} {odds_str}")
            
        if doubles:
            print("\n🎾 DOBLES (primeros 5):")
            for i, m in enumerate(doubles[:5], 1):
                p1 = m.get('player1', '?')
                p2 = m.get('player2', '?')
                hora = m.get('hora', '')
                print(f"  {i}. {p1} vs {p2} {hora}")

if __name__ == "__main__":
    asyncio.run(main())
