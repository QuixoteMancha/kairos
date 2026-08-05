"""SportsbookReviewsOnline -- odds históricas PRE-PARTIDO de MLB
(moneyline apertura/cierre + run line + totals) -- 2026-08-05, pedido
explícito del usuario tras investigar fuentes gratuitas de odds
históricas ("no hay fuentes de odds históricas de MLB, puedes
investigar?").

Verificado en vivo: `.xlsx` real descargable sin autenticación,
URL estática `https://www.sportsbookreviewsonline.com/wp-content/uploads/
sportsbookreviewsonline_com_737/mlb-odds-{year}.xlsx`, cobertura
CONFIRMADA 2010-2021 (el sitio mismo indica que dejó de actualizarse --
no hay 2022 en adelante, verificado). Esto es SOLO odds PRE-PARTIDO
(apertura/cierre) -- NO odds en vivo dentro del partido (esa fuente
gratuita NO existe, investigado explícitamente y descartado, ver
Obsidian "Backtest Favorito MLB - Kairos.md").

FORMATO REAL del archivo (verificado descargando 2019 en vivo): 2 filas
por partido (visitante primero, luego local -- columna `VH`: 'V'/'H',
'N' para partidos neutrales/interliga en algún caso puntual), columnas
`Date` (MMDD sin año, ej. 320 = 20 de marzo -- el año viene del propio
archivo, un archivo = una temporada), `Team` (código de 3 letras, ej.
'SEA'), `Open`/`Close` (moneyline americano, ej. -160/140), `Run Line`,
`Open OU`/`Close OU` (totales). Acá SOLO se extrae moneyline
apertura/cierre -- run line/totales quedan sin usar por ahora (fuera
del alcance pedido, se podrían agregar después sin re-descargar nada).

Postemporada: el archivo mezcla temporada regular + playoffs sin una
columna que los distinga explícitamente -- se persiste todo tal cual
viene, sin filtrar (igual criterio que TML-Database para tenis: no se
inventa una distinción que la fuente no da explícitamente)."""

from __future__ import annotations

import io
from typing import Any

import requests

_BASE_URL = "https://www.sportsbookreviewsonline.com/wp-content/uploads/sportsbookreviewsonline_com_737"
_REQUEST_TIMEOUT_S = 30
_REAL_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# Cobertura real confirmada en vivo -- el sitio no publica años fuera de
# este rango (2026-08-05).
MIN_YEAR = 2010
MAX_YEAR = 2021

# Código de 3 letras de SBRO -> nombre completo real, tal cual lo guarda
# `MlbGameHistory.home_team`/`away_team` (fuente: statsapi.mlb.com, ver
# `mlb_stats_scraper.py`). Los 30 equipos reales de MLB -- fail-closed:
# un código no reconocido queda `None` (nunca se adivina un equipo).
_TEAM_CODE_TO_NAME = {
    "ARI": "Arizona Diamondbacks", "ATL": "Atlanta Braves", "BAL": "Baltimore Orioles",
    "BOS": "Boston Red Sox", "CHC": "Chicago Cubs", "CWS": "Chicago White Sox",
    "CIN": "Cincinnati Reds", "CLE": "Cleveland Guardians", "COL": "Colorado Rockies",
    "DET": "Detroit Tigers", "HOU": "Houston Astros", "KC": "Kansas City Royals",
    "LAA": "Los Angeles Angels", "LAD": "Los Angeles Dodgers", "MIA": "Miami Marlins",
    "MIL": "Milwaukee Brewers", "MIN": "Minnesota Twins", "NYM": "New York Mets",
    "NYY": "New York Yankees", "OAK": "Oakland Athletics", "PHI": "Philadelphia Phillies",
    "PIT": "Pittsburgh Pirates", "SD": "San Diego Padres", "SEA": "Seattle Mariners",
    "SF": "San Francisco Giants", "STL": "St. Louis Cardinals", "TB": "Tampa Bay Rays",
    "TEX": "Texas Rangers", "TOR": "Toronto Blue Jays", "WAS": "Washington Nationals",
    # Cleveland se llamaba "Indians" antes de 2022 -- el archivo (2010-2021)
    # usa el código 'CLE' consistentemente, mapeado al nombre ACTUAL
    # ("Guardians") porque `MlbGameHistory` (fuente: statsapi.mlb.com
    # en vivo, backfilleada con nombres actuales) usa el nombre de hoy
    # para el mismo equipo real, no el histórico -- necesario para que
    # el join por nombre funcione contra nuestra propia tabla.
}


def team_name_for_code(code: str | None) -> str | None:
    if not code:
        return None
    return _TEAM_CODE_TO_NAME.get(code.strip().upper())


def _american_odds_to_implied_prob(odds: float | int | None) -> float | None:
    """Fail-closed: None si `odds` no es un número real. Fórmula
    estándar: favorito (odds negativas) -> |odds|/(|odds|+100);
    underdog (odds positivas) -> 100/(odds+100)."""
    if odds is None:
        return None
    try:
        odds = float(odds)
    except (TypeError, ValueError):
        return None
    if odds == 0:
        return None
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)


def _mmdd_to_iso(raw_date: Any, year: int) -> str | None:
    """`raw_date` real del archivo es un int/float tipo 320 (=20/marzo)
    o 1003 (=3/octubre) -- SIN separador, SIN año (el año lo da el
    propio archivo). Postemporada real puede cruzar a enero del año
    siguiente (World Series tardía) -- fail-closed: si el mes/día no
    parsea a una fecha real, devuelve None en vez de adivinar."""
    try:
        raw = int(raw_date)
    except (TypeError, ValueError):
        return None
    month = raw // 100
    day = raw % 100
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    # postemporada tardía (World Series en noviembre real, verificado
    # 2010-2021) nunca cruza a enero -- si algún día se ve un caso real
    # que sí cruce, este mapeo simple (mismo año que el archivo) fallaría
    # silenciosamente hacia una fecha incorrecta del mismo año; no
    # observado en los años verificados, documentado como riesgo conocido.
    return f"{year:04d}-{month:02d}-{day:02d}"


def _fetch_xlsx_bytes(year: int) -> bytes | None:
    url = f"{_BASE_URL}/mlb-odds-{year}.xlsx"
    try:
        resp = requests.get(url, timeout=_REQUEST_TIMEOUT_S, headers={"User-Agent": _REAL_USER_AGENT})
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    return resp.content


def get_year_odds(year: int) -> list[dict[str, Any]]:
    """Odds pre-partido reales de una temporada completa -- fail-closed:
    [] si el año está fuera de `MIN_YEAR`/`MAX_YEAR`, el fetch falla, o
    el archivo no parsea. Cada partido real ocupa 2 filas consecutivas
    en el archivo (visitante, local) -- se combinan acá en 1 dict con
    ambos lados.

    Returns: lista de dicts {date (ISO o None), home_team, away_team,
    home_moneyline_open, home_moneyline_close, away_moneyline_open,
    away_moneyline_close, home_implied_prob_open, home_implied_prob_close,
    away_implied_prob_open, away_implied_prob_close}. Fail-closed por
    fila: un partido cuyo par de filas no se pueda emparejar limpio
    (código de equipo no reconocido, fecha inválida) se descarta, no se
    fabrica a medias."""
    if not (MIN_YEAR <= year <= MAX_YEAR):
        return []

    content = _fetch_xlsx_bytes(year)
    if content is None:
        return []

    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
    except Exception:
        return []

    if not rows:
        return []
    header = [str(h).strip() if h else "" for h in rows[0]]
    try:
        idx_date = header.index("Date")
        idx_vh = header.index("VH")
        idx_team = header.index("Team")
        idx_open = header.index("Open")
        idx_close = header.index("Close")
    except ValueError:
        return []  # fail-closed: formato inesperado, no se adivina el layout

    result: list[dict[str, Any]] = []
    data_rows = rows[1:]
    i = 0
    while i + 1 < len(data_rows):
        r1, r2 = data_rows[i], data_rows[i + 1]
        i += 2
        if r1 is None or r2 is None:
            continue

        vh1 = (r1[idx_vh] or "").strip().upper() if r1[idx_vh] else None
        vh2 = (r2[idx_vh] or "").strip().upper() if r2[idx_vh] else None
        # el par esperado es (V, H) en ese orden -- si no matchea así,
        # el archivo desalineó en algún punto (fila faltante/corrupta) y
        # este partido se descarta en vez de emparejar mal 2 partidos
        # distintos.
        if vh1 != "V" or vh2 != "H":
            continue

        away_name = team_name_for_code(r1[idx_team])
        home_name = team_name_for_code(r2[idx_team])
        date_iso = _mmdd_to_iso(r1[idx_date], year)
        if not away_name or not home_name or not date_iso:
            continue

        away_open, away_close = r1[idx_open], r1[idx_close]
        home_open, home_close = r2[idx_open], r2[idx_close]

        result.append({
            "date": date_iso, "home_team": home_name, "away_team": away_name,
            "home_moneyline_open": home_open, "home_moneyline_close": home_close,
            "away_moneyline_open": away_open, "away_moneyline_close": away_close,
            "home_implied_prob_open": _american_odds_to_implied_prob(home_open),
            "home_implied_prob_close": _american_odds_to_implied_prob(home_close),
            "away_implied_prob_open": _american_odds_to_implied_prob(away_open),
            "away_implied_prob_close": _american_odds_to_implied_prob(away_close),
        })

    return result


def get_range_odds(start_year: int = MIN_YEAR, end_year: int = MAX_YEAR) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for year in range(start_year, end_year + 1):
        result.extend(get_year_odds(year))
    return result
