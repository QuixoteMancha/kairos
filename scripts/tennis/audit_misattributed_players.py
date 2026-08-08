"""Auditoría + reparación de jugadores/as potencialmente mal atribuidos
por el bug real de `_verify_player_page` (2026-08-06, ver
`tennisexplorer_player_scraper.py` para el detalle completo del bug:
"Casper Ruud" -> `guess_player_slug` adivinaba la página real de
"Christian Ruud", su padre -- la tolerancia vieja solo exigía que el
APELLIDO coincidiera, aceptando así a cualquier persona distinta con
el mismo apellido).

INCIDENTE REAL en la primera versión de este script (2026-08-06,
mismo día): confundía "fetch de red falló" (`_fetch` devuelve `None`
ante CUALQUIER timeout/error transitorio, fail-closed por diseño) con
"identidad confirmada como incorrecta" (verificación falló con HTML
real) -- `guess_player_slug` devuelve `None` en AMBOS casos, sin
distinguir. Una ráfaga de 225 requests en un solo tirón disparó
timeouts/rate-limiting reales en varios, y el script los PURGÓ como si
fueran identidades falsas -- borrando datos reales de jugadores/as sin
ninguna ambigüedad genuina (ej. Serena Williams, Naomi Osaka, Taylor
Fritz, Stefanos Tsitsipas: confirmados sin problema en un re-intento
inmediato). Fix: este script ahora llama `_fetch`/`_verify_player_page`
por separado (no `guess_player_slug` de una), reintenta ante fallo de
red real (nunca purga por eso), y SOLO purga cuando el fetch tuvo
éxito Y la verificación de identidad falló de forma real y repetible.

RECUPERACIÓN: en vez de depender de la lista de "self" ya backfilleada
(que la primera corrida ya alteró, purgando ~153 jugadores/as reales),
el universo de riesgo se reconstruye desde fuentes que NO cambiaron
(TML-Database + `WtaPlayerRankingHistory`) -- cualquiera de ese
universo que hoy NO tenga fila alguna en `TennisMatchHistory` como
"self" (nunca backfilleado, O purgado por el incidente) y que ahora
SÍ confirme identidad real, se re-backfillea completo (`backfill_player`,
no solo la verificación) para restaurar sus partidos+cuotas reales."""

from __future__ import annotations

import time

from database.db import init_db_historical, db_historical
from database.historical.tennis import TennisMatchHistory, WtaPlayerRankingHistory
from database.historical.tennis import TmlDatabaseMatch
from outer_apis.scrapers.tennis.tennisexplorer_player_scraper import _fetch, _verify_player_page
from scripts.tennis.wta_top200_historical_backfill import backfill_player

_MAX_NETWORK_RETRIES = 3
_RETRY_PAUSE_S = 5.0


def _slug_from_name(name: str) -> str:
    import re
    last_name = name.strip().split()[-1].lower() if name.strip() else ""
    return re.sub(r"[^a-z\-]", "", last_name)


def _verify_identity(name: str) -> str:
    """"confirmed" | "wrong_person" | "network_error" -- NUNCA colapsa
    fallo de red con identidad incorrecta (ver docstring del módulo,
    ese fue el bug real del incidente)."""
    slug = _slug_from_name(name)
    if not slug:
        return "wrong_person"  # nombre de una sola palabra, no hay apellido que verificar -- fail-closed real
    for attempt in range(_MAX_NETWORK_RETRIES):
        html = _fetch(slug)
        if html is not None:
            return "confirmed" if _verify_player_page(html, name) else "wrong_person"
        time.sleep(_RETRY_PAUSE_S)
    return "network_error"  # los 3 intentos fallaron por red -- NUNCA se purga por esto


def find_at_risk_players() -> list[str]:
    """Universo de riesgo reconstruido desde fuentes que el incidente
    NO alteró (TML-Database + `WtaPlayerRankingHistory`) -- cualquier
    nombre que comparta el ÚLTIMO token (proxy real de "mismo
    apellido") con otro nombre real conocido, sin importar si hoy
    tiene o no filas en `TennisMatchHistory` (a propósito -- así se
    detectan también los purgados por el incidente, que ya no
    aparecen ahí).

    ATP restringido a 2016+/250+ (`TOUR_LEVELS_250_PLUS`, mismo
    criterio que `run_backfill_from_atp_roster`) -- 2026-08-06,
    corregido tras medir en vivo que sin este filtro el universo salta
    a 2,605 candidatos (todo el historial 1968-2026, con colisiones
    irrelevantes entre jugadores de épocas totalmente distintas que
    nunca aparecerían en ningún pool evaluable actual)."""
    init_db_historical()
    from outer_apis.scrapers.tennis.tml_database_scraper import TOUR_LEVELS_250_PLUS

    full_universe: set[str] = set()
    tml_query = TmlDatabaseMatch.select().where(
        (TmlDatabaseMatch.date >= "2016-01-01") & (TmlDatabaseMatch.level.in_(list(TOUR_LEVELS_250_PLUS)))
    )
    for r in tml_query:
        full_universe.add(r.winner_name)
        full_universe.add(r.loser_name)
    for r in WtaPlayerRankingHistory.select(WtaPlayerRankingHistory.player_name).distinct():
        full_universe.add(r.player_name)

    by_last_token: dict[str, set[str]] = {}
    for n in full_universe:
        tokens = n.strip().split(" ")
        if len(tokens) < 2:
            continue
        by_last_token.setdefault(tokens[-1].lower(), set()).add(n)

    at_risk = []
    for n in full_universe:
        tokens = n.strip().split(" ")
        if len(tokens) < 2:
            continue
        others = by_last_token.get(tokens[-1].lower(), set()) - {n}
        if others:
            at_risk.append(n)
    return sorted(set(at_risk))


def run_audit_and_repair() -> dict:
    init_db_historical()
    at_risk = find_at_risk_players()
    print(f"[audit_misattributed_players] {len(at_risk)} jugadores/as en riesgo a re-verificar", flush=True)

    with db_historical.connection_context():
        self_names_now = {r.player_name for r in TennisMatchHistory.select(TennisMatchHistory.player_name).distinct()}

    confirmed_had_data = confirmed_restored = wrong_person = network_error = 0
    restored_names: list[str] = []
    wrong_person_names: list[str] = []
    network_error_names: list[str] = []

    for i, name in enumerate(at_risk, start=1):
        result = _verify_identity(name)
        had_data = name in self_names_now

        if result == "network_error":
            network_error += 1
            network_error_names.append(name)
            status = "fallo de red persistente -- NO se toca (ni se purga ni se restaura), reintentar después"
        elif result == "wrong_person":
            wrong_person += 1
            wrong_person_names.append(name)
            if had_data:
                with db_historical.connection_context():
                    from database.historical.tennis import TennisHistoricalOdds
                    TennisMatchHistory.delete().where(TennisMatchHistory.player_name == name).execute()
                    TennisHistoricalOdds.delete().where(TennisHistoricalOdds.player_name == name).execute()
                status = "PURGADO (identidad confirmada como incorrecta, con red funcionando)"
            else:
                status = "identidad no confirmable (sin datos previos que purgar)"
        else:  # confirmed
            if had_data:
                confirmed_had_data += 1
                status = "confirmado (ya tenía datos, sin cambios)"
            else:
                result_backfill = backfill_player(name)
                confirmed_restored += 1
                restored_names.append(name)
                status = f"confirmado -- RESTAURADO ({result_backfill.get('matches_inserted', 0)} partidos, {result_backfill.get('odds_inserted', 0)} cuotas)"

        print(f"[audit_misattributed_players] ({i}/{len(at_risk)}) {name} -> {status}", flush=True)

    summary = {
        "at_risk_total": len(at_risk),
        "confirmed_had_data": confirmed_had_data,
        "confirmed_restored": confirmed_restored,
        "restored_names": restored_names,
        "wrong_person": wrong_person,
        "wrong_person_names": wrong_person_names,
        "network_error_unresolved": network_error,
        "network_error_names": network_error_names,
    }
    print(
        f"[audit_misattributed_players] TERMINADO: {confirmed_had_data} confirmados sin cambios, "
        f"{confirmed_restored} restaurados, {wrong_person} identidad incorrecta confirmada, "
        f"{network_error} sin resolver por red (reintentar)",
        flush=True,
    )
    return summary


if __name__ == "__main__":
    import json
    print(json.dumps(run_audit_and_repair(), indent=2, ensure_ascii=False))
