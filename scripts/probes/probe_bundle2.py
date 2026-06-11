import sys, re, json, time
sys.path.insert(0, '.')
from curl_cffi.requests import Session

BROWSER_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'en-US,en;q=0.9',
    'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="124", "Google Chrome";v="124"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"Windows"',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Upgrade-Insecure-Requests': '1',
}

s = Session(impersonate='chrome124')

print('[*] Descargando página de tennis...')
resp = s.get('https://stake.com/sports/tennis', headers=BROWSER_HEADERS, timeout=20)
print(f'    status={resp.status_code}, len={len(resp.text)}')
if resp.status_code != 200:
    print('FALLO'); exit(1)

html = resp.text

# Buscar operation names de Apollo/GraphQL
ops = re.findall(r'operationName["\s:]+["\']([A-Z][a-zA-Z]+)', html)
print(f'\n=== operationName en HTML ({len(set(ops))}): ===')
for op in sorted(set(ops)):
    print(f'  {op}')

# Buscar "query NombreQuery" 
queries_named = re.findall(r'(?:query|mutation|subscription)\s+([A-Z][a-zA-Z]+)', html)
print(f'\n=== query/mutation names ({len(set(queries_named))}): ===')
for q in sorted(set(queries_named)):
    print(f'  {q}')

# Buscar sports-related keywords en la estructura JS
sport_kws = re.findall(r'["\']([^"\']*(?:sport|tennis|market|odds|event|fixture|competition|match)[A-Z][a-zA-Z]*)["\']', html, re.IGNORECASE)
unique_sport = list(set([s.strip() for s in sport_kws if len(s) > 5]))[:30]
print(f'\n=== Sport-related strings ({len(unique_sport)}): ===')
for s in sorted(unique_sport):
    print(f'  {s}')

# Buscar URLs de scripts JS para descargar el bundle
script_urls = re.findall(r'["\'](?:https?://[^"\']*\.js|/_next/[^"\']*\.js)["\']', html)
print(f'\n=== Script URLs ({len(script_urls)}): ===')
for u in list(set(script_urls))[:10]:
    print(f'  {u}')
