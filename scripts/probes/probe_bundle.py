import sys, re, json
sys.path.insert(0, '.')
from curl_cffi.requests import Session

s = Session(impersonate='chrome120')

print('[*] Obteniendo página de tennis...')
resp = s.get('https://stake.com/sports/tennis', timeout=20)
print(f'    status={resp.status_code}')
html = resp.text

# Buscar scripts del bundle
scripts = re.findall(r'"(/[^"]+\.js)"', html)
# Filtrar los que parecen ser chunks del app (no vendor puro)
app_scripts = [s for s in scripts if 'chunk' in s or 'app' in s or 'page' in s or 'main' in s]
print(f'App scripts encontrados: {app_scripts[:10]}')
print(f'Todos los scripts: {scripts[:20]}')
