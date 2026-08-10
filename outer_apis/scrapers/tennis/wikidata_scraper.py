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


# 2026-08-10, pedido explícito del usuario: "busca una lista en
# wikipedia con nombres de tenistas de atp y wta, todos los que puedas.
# Nombre y apellido ese formato, eso dejalo como base para que después
# no se armen enredos" -- en vez de scrapear HTML de listas de
# Wikipedia (frágil, mal estructurado, con abreviaturas/orden
# inconsistente igual que TennisExplorer), se usa Wikidata vía SPARQL:
# `P735` (nombre de pila) y `P734` (apellido) son propiedades
# ESTRUCTURADAS separadas para cada persona -- "Nombre Apellido" sale
# de 2 campos reales, nunca de adivinar el orden de un string.
#
# 1 sola query masiva (paginada con LIMIT/OFFSET) en vez de 1 por
# jugador -- SIN esto, ~30-50K jugadores tomarían horas contra el
# límite de 1 req/s de `wikidata_throttle`.
_BULK_QUERY_TEMPLATE = """
SELECT ?person ?givenLabel ?familyLabel WHERE {{
  ?person wdt:P106 wd:Q10833314.
  ?person wdt:P735 ?given.
  ?person wdt:P734 ?family.
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
ORDER BY ?person
LIMIT {limit}
OFFSET {offset}
"""

_BULK_PAGE_SIZE = 2000


def fetch_all_tennis_players_given_family_names(
    max_pages: int | None = None,
) -> list[dict]:
    """Trae TODOS los jugadores/as de tenis (`Q10833314`) que tienen
    AMBOS `P735` (nombre de pila) y `P734` (apellido) poblados en
    Wikidata -- paginado (`LIMIT`/`OFFSET`, `_BULK_PAGE_SIZE` por
    página) para no exceder el límite real de 60s por query del
    endpoint SPARQL. Fail-closed por diseño: nunca incluye una entrada
    a medias (sin AMBOS campos, la query ni la trae).

    Devuelve [{"wikidata_id": "Q38129", "given_name": "Barbara",
    "family_name": "Potter"}, ...] -- SIN deduplicar todavía (1 persona
    puede aparecer más de 1 vez si Wikidata tiene múltiples valores de
    P735/P734, ej. nombre completo vs. apodo -- deduplicación real es
    responsabilidad del script que arma la tabla de referencia, no de
    este fetch crudo).

    `max_pages=None` (default) trae TODO lo que haya; se puede acotar
    para testear sin pegarle a la red completa."""
    results: list[dict] = []
    offset = 0
    page = 0
    while max_pages is None or page < max_pages:
        wikidata_throttle.wait()
        query = _BULK_QUERY_TEMPLATE.format(limit=_BULK_PAGE_SIZE, offset=offset)
        try:
            resp = requests.get(
                _SPARQL_ENDPOINT,
                params={"query": query, "format": "json"},
                headers={"User-Agent": _USER_AGENT, "Accept": "application/sparql-results+json"},
                timeout=60,
            )
        except requests.RequestException:
            break
        if resp.status_code != 200:
            break
        try:
            data = resp.json()
        except ValueError:
            break
        bindings = data.get("results", {}).get("bindings", [])
        if not bindings:
            break
        for row in bindings:
            person_uri = row.get("person", {}).get("value", "")
            wikidata_id = person_uri.rsplit("/", 1)[-1] if person_uri else ""
            given = row.get("givenLabel", {}).get("value", "").strip()
            family = row.get("familyLabel", {}).get("value", "").strip()
            if not wikidata_id or not given or not family:
                continue
            results.append(
                {"wikidata_id": wikidata_id, "given_name": given, "family_name": family}
            )
        if len(bindings) < _BULK_PAGE_SIZE:
            break
        offset += _BULK_PAGE_SIZE
        page += 1
    return results
