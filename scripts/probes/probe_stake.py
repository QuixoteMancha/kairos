import sys, re
sys.path.insert(0, '.')
from curl_cffi.requests import Session

s = Session(impersonate='chrome120')
resp = s.get('https://stake.com/sports/tennis', timeout=20)
html = resp.text
print(f"Status: {resp.status_code}, len: {len(html)}")

# GraphQL endpoints
gql = re.findall(r'["\']([^"\']*graphql[^"\']*)["\']', html, re.IGNORECASE)
print("GraphQL:", gql[:5])

# Scripts JS del bundle
scripts = re.findall(r'src="(/[^"]+\.js)"', html)
print("Scripts:", scripts[:5])

# Buscar 'apollo' o 'fetch' con URL
apollo = re.findall(r'(apollo|fetch|axios)[^;]{0,100}stake', html, re.IGNORECASE)
print("Apollo/fetch:", apollo[:3])
