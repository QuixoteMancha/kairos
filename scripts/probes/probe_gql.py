import sys, json, time
sys.path.insert(0, '.')
from curl_cffi.requests import Session

GQL = 'https://stake.com/_api/graphql'

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

print('[*] Warming up with chrome124...')
warm = s.get('https://stake.com/sports/tennis', headers=BROWSER_HEADERS, timeout=20)
print(f'    status={warm.status_code}, cookies={list(s.cookies.keys())}')
if warm.status_code != 200:
    print('FALLO warm up'); exit(1)

time.sleep(1.5)

# Headers de API request (XHR fetch)
api_headers = {
    'Content-Type': 'application/json',
    'Origin': 'https://stake.com',
    'Referer': 'https://stake.com/sports/tennis',
    'Accept': 'application/json, */*',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'en-US,en;q=0.9',
    'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="124", "Google Chrome";v="124"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"Windows"',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'x-language': 'en',
    'x-currency': 'usd',
}

# Introspection
introspect = '{ __schema { queryType { fields { name } } } }'
resp = s.post(GQL, json={'query': introspect}, headers=api_headers, timeout=15)
print(f'[introspect] status={resp.status_code}')
if resp.status_code == 200:
    data = resp.json()
    if 'data' in data and data['data']:
        fields = data['data'].get('__schema', {}).get('queryType', {}).get('fields', [])
        names = [f['name'] for f in fields]
        sport_related = [n for n in names if any(k in n.lower() for k in
            ['sport','event','match','bet','market','odd','tennis','live','fixture','competition'])]
        print(f'Total query fields: {len(names)}')
        print(f'Sport-related: {sport_related}')
        print(f'First 60: {names[:60]}')
    else:
        print('Error:', json.dumps(data, indent=2)[:600])
else:
    print('HTTP error:', resp.text[:300])
