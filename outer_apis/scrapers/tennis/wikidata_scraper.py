"""Resuelve el sexo/género real de un/a tenista vía Wikidata -- 2026-08-10,
pedido explícito del usuario: verificar los jugadores/as con `tour`
ambiguo (contradicción ATP/WTA entre sus propias filas, ver
`scripts/tennis/dedupe_triplicated_match_history.py::find_tour_sex_mismatches`)
SIN seguir hiteando TennisExplorer -- Wikidata es una fuente externa
independiente, con su propio throttle (`wikidata_throttle`, ver
`_request_throttle.py`).

MECANISMO: 1 sola query SPARQL combinada por jugador/a (no 2 requests
separados de "buscar" + "traer detalle") -- usa el servicio `mwapi`
DENTRO de SPARQL para buscar por nombre (misma búsqueda difusa que la
caja de búsqueda de Wikidata, tolera acentos/variantes razonables) Y
filtra en la MISMA query por ocupación real "tennis player" (Q10833314)
antes de traer el campo `sexo o género` (P21) -- evita el riesgo de
homónimos reales (2 personas distintas, mismo nombre, una de ellas SÍ
tenista) trayendo el género de la persona equivocada.

Fail-closed: `None` si no hay ningún resultado que sea tenista, si el
campo de sexo/género no está poblado en Wikidata, o si el fetch/parseo
falla -- nunca se adivina."""

from __future__ import annotations

import requests

from outer_apis.scrapers.tennis._request_throttle import wikidata_throttle

_SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
_REQUEST_TIMEOUT_S = 15
# 2026-08-10, pedido explícito del usuario: contact real requerido por
# la etiqueta de Wikimedia para no ser throttleado como bot anónimo
# (ver Wikidata:Data access) -- identifica el proyecto real, no una
# persona.
_USER_AGENT = "KairosTennisResearchBot/1.0 (internal research tool, non-commercial)"

# Q10833314 = "tennis player" (ocupación real en Wikidata).
# Q6581097 = male, Q6581072 = female (los 2 valores reales que importan
# para este uso -- Wikidata soporta más valores de P21, pero para
# distinguir ATP/WTA solo estos 2 son relevantes; cualquier otro valor
# real (ej. intersex) queda fuera de alcance de este mapeo binario a
# propósito, no se fuerza una clasificación que no corresponde).
_SPARQL_QUERY = """
SELECT ?genderLabel WHERE {{
  SERVICE wikibase:mwapi {{
    bd:serviceParam wikibase:api "EntitySearch".
    bd:serviceParam wikibase:endpoint "www.wikidata.org".
    bd:serviceParam mwapi:search "{name}".
    bd:serviceParam mwapi:language "en".
    ?person wikibase:apiOutputItem mwapi:item.
  }}
  ?person wdt:P106 wd:Q10833314.
  ?person wdt:P21 ?gender.
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
LIMIT 1
"""


def _sparql_escape(name: str) -> str:
    return name.replace('"', '\\"')


def get_tennis_player_tour_by_gender(full_name: str) -> str | None:
    """"ATP" | "WTA" | None -- mismo contrato de retorno que
    `tennisexplorer_player_scraper.get_tour_by_sex`, pensado como fuente
    ALTERNATIVA/de respaldo (no reemplaza TennisExplorer, es una segunda
    señal independiente para no concentrar todo el tráfico de
    verificación en un solo sitio). Fail-closed: `None` ante cualquier
    fallo real (red, parseo, sin resultado que sea tenista, género no
    male/female)."""
    if not full_name or not full_name.strip():
        return None

    wikidata_throttle.wait()
    query = _SPARQL_QUERY.format(name=_sparql_escape(full_name.strip()))
    try:
        resp = requests.get(
            _SPARQL_ENDPOINT,
            params={"query": query, "format": "json"},
            headers={"User-Agent": _USER_AGENT, "Accept": "application/sparql-results+json"},
            timeout=_REQUEST_TIMEOUT_S,
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None

    bindings = data.get("results", {}).get("bindings", [])
    if not bindings:
        return None
    gender_label = bindings[0].get("genderLabel", {}).get("value", "").strip().lower()
    if gender_label == "male":
        return "ATP"
    if gender_label == "female":
        return "WTA"
    return None
