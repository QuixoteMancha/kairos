"""Resuelve `TennisMatchHistoryUnvalidated` (~322,789 candidatos, ver
`dedupe_match_history_candidates.py`/`validate_match_history_against_sackmann.py`)
re-fetcheando en VIVO contra TennisExplorer -- 2026-08-09, pedido
explícito del usuario: "dejar un script corriendo en la mini-pc que
vaya validando con la página de match-details, los datos del match...
delays aleatorios y paralelismo pero sin hitear tanto por minuto".

MECANISMO: para cada jugador con candidatos pendientes, re-fetchea su
página de TennisExplorer por año (mismo scraper que ya usa
`backfill_player`) -- esto entrega el `match_id` real de cada partido
(cosechado gratis del link `/match-detail/?id=...`, ver `_MATCH_RE`).
Se cruza cada fila recién parseada contra los candidatos pendientes de
ESE jugador por (fecha exacta + abreviación del rival) -- si matchea
UN SOLO candidato, se confirma con certeza absoluta llamando
`get_match_detail(match_id)` (nombre completo real + slug + bio de
AMBOS lados) y se guarda en `MatchHistory` (`save_match_detail`,
idempotente). El candidato ya resuelto se borra de
`TennisMatchHistoryUnvalidated` (dejó de ser "sin validar").

Fail-closed: 0 o 2+ candidatos que matcheen la misma fecha+abreviación
-> se deja el candidato intacto en la tabla, nunca se adivina.

RITMO REAL (pedido explícito -- "sin hitear tanto por minuto"): el
throttle global (`tennisexplorer_throttle`, 0.5s mínimo compartido por
TODOS los threads, ya usado en el resto del proyecto) sigue siendo el
piso duro. Encima de eso, cada request acá agrega una espera aleatoria
extra (`_extra_delay()`) para no generar un patrón de tráfico
perfectamente regular -- el paralelismo (`ThreadPoolExecutor`) paraleliza
la ESPERA propia de cada thread, nunca supera el piso real del
throttle compartido (mismo argumento de seguridad ya validado en
`_run_backfill_for_names`)."""

from __future__ import annotations

import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone

from outer_apis.scrapers.tennis.tennisexplorer_player_scraper import (
    resolve_player_slug, _fetch, _parse_year_html, _to_iso_date, get_match_detail,
    expected_opponent_abbreviation,
)

# 2026-08-09, pedido explícito del usuario: subido de 4 a 8 junto con
# bajar el throttle real de TennisExplorer de 0.5s a 0.3s
# (`_request_throttle.py`) -- con el delay extra promedio (~2.25s) por
# thread, 4 workers rendían ~1.78 req/s (ya por debajo del viejo techo
# de 2 req/s del lock compartido); 8 workers rinden ~3.5 req/s, que
# ahora sí satura el nuevo techo de ~3.33 req/s (1 / 0.3s) -- el lock
# compartido pasa a ser el limitante real, que es la intención.
_MAX_WORKERS = int(os.getenv("VALIDATE_UNRESOLVED_MAX_WORKERS", "8"))
_EXTRA_DELAY_MIN_S = float(os.getenv("VALIDATE_UNRESOLVED_DELAY_MIN_S", "1.0"))
_EXTRA_DELAY_MAX_S = float(os.getenv("VALIDATE_UNRESOLVED_DELAY_MAX_S", "3.5"))


def _extra_delay() -> None:
    """Espera aleatoria ADICIONAL al throttle global compartido -- rompe
    el patrón regular de tráfico, pedido explícito del usuario."""
    time.sleep(random.uniform(_EXTRA_DELAY_MIN_S, _EXTRA_DELAY_MAX_S))


def _same_player_name(name_a: str, name_b: str) -> bool:
    """Compara 2 nombres de la MISMA persona sin asumir orden de tokens
    -- `player_a_name` de un candidato viene "Nombre Apellido", pero
    `get_match_detail` devuelve el nombre nativo de TennisExplorer,
    "Apellido Nombre" -- mismo conjunto de tokens, orden distinto."""
    return frozenset(name_a.lower().split()) == frozenset(name_b.lower().split())


def _surname_key_from_te_name(name: str) -> str:
    """2026-08-09, bug real encontrado en producción -- `resolve_player_slug`
    solo intenta la heurística naive de slug antes de rendirse (nunca
    recibía `candidate_slugs`, a diferencia de `wta_top200_historical_
    backfill.py::backfill_player`, que sí usa el catálogo real
    `TennisExplorerPlayerSlug`). Esto hacía fallar `process_player`
    entero (con `status: "no_slug"`, 0 resueltos) para cualquier jugador
    sin slug naive-adivinable (ej. "Budkov Kjaer Nicolai" -> slug real
    "budkov-kjaer", pero la heurística naive no lo encuentra), pese a
    que `get_match_detail` SÍ conoce el slug real (lo cosecha del
    `href` del partido, no de una búsqueda por nombre).

    Convención de nombre COMPLETO de TennisExplorer (a diferencia de
    `_surname_key_from_full_name` en `wta_top200_historical_backfill.py`,
    pensada para nombres "Firstname Lastname" del roster oficial WTA):
    acá el nombre viene "Apellido(s) Nombre" (mismo orden que usa
    `_surname_key_from_abbrev` para "Apellido I.") -- el apellido es
    TODO menos el ÚLTIMO token."""
    tokens = name.strip().split(" ")
    if len(tokens) < 2:
        return ""
    surname = " ".join(tokens[:-1])
    return surname.replace("-", " ").strip().lower()


def _get_candidate_slugs(player_name: str) -> list[str]:
    """Slugs REALES ya cosechados (de `hrefs` de partidos previamente
    fetcheados, propios o de `get_match_detail`) para el apellido de
    `player_name` -- ver `TennisExplorerPlayerSlug`/`resolve_player_slug`."""
    from database.db import init_db_historical, db_historical
    from database.historical.tennis import TennisExplorerPlayerSlug

    key = _surname_key_from_te_name(player_name)
    if not key:
        return []
    init_db_historical()
    with db_historical.connection_context():
        return [r.slug for r in TennisExplorerPlayerSlug.select().where(TennisExplorerPlayerSlug.surname_key == key)]


def _harvest_slug(full_name: str, slug: str | None, now) -> None:
    """Persiste (apellido normalizado, slug real visto) para que futuras
    resoluciones de ESTE jugador (u otro candidato con el mismo
    apellido) no dependan de la heurística naive. Idempotente (índice
    único (surname_key, slug))."""
    if not slug:
        return
    from database.historical.tennis import TennisExplorerPlayerSlug

    key = _surname_key_from_te_name(full_name)
    if not key:
        return
    TennisExplorerPlayerSlug.get_or_create(surname_key=key, slug=slug, defaults={"discovered_at": now})


def find_matching_candidate(parsed_row: dict, parsed_date_iso: str, candidates: list) -> object | None:
    """`candidates`: filas de `TennisMatchHistoryUnvalidated` del MISMO
    `player_a_name` -- matchea por (fecha exacta + abreviación real del
    rival, calculada desde el nombre completo si el dedup ya lo
    resolvió, o comparada directo si sigue abreviado). Fail-closed:
    None si no hay exactamente 1 candidato que matchee."""
    matches = []
    for c in candidates:
        if c.match_date != parsed_date_iso:
            continue
        expected_abbrev = (
            expected_opponent_abbreviation(c.player_b_name_or_abbrev)
            if c.player_b_is_full_name else c.player_b_name_or_abbrev
        )
        if expected_abbrev == parsed_row["opponent"]:
            matches.append(c)
    return matches[0] if len(matches) == 1 else None


def process_player(player_a_name: str, candidates: list) -> dict:
    """Procesa TODOS los candidatos pendientes de un jugador -- 1 fetch
    por año necesario (no por partido), fail-closed por candidato."""
    from database.db import init_db_historical, db_historical
    from database.historical.tennis import MatchHistory, save_match_detail, TennisMatchHistoryUnvalidated

    init_db_historical()
    years_needed = sorted({int(c.match_date[:4]) for c in candidates if c.match_date and c.match_date[:4].isdigit()})
    if not years_needed:
        return {"player": player_a_name, "resolved": 0, "unresolved": len(candidates)}

    slug = resolve_player_slug(player_a_name, candidate_slugs=_get_candidate_slugs(player_a_name))
    _extra_delay()
    if slug is None:
        return {"player": player_a_name, "resolved": 0, "unresolved": len(candidates), "status": "no_slug"}

    remaining = list(candidates)
    resolved = 0
    for year in years_needed:
        if not remaining:
            break
        html = _fetch(slug, year=year)
        _extra_delay()
        if html is None:
            continue
        for parsed in _parse_year_html(html, player_a_name):
            if not remaining:
                break
            iso_date = _to_iso_date(parsed["date"], year)
            if iso_date is None or not parsed.get("match_id"):
                continue
            match = find_matching_candidate(parsed, iso_date, remaining)
            if match is None:
                continue
            detail = get_match_detail(parsed["match_id"])
            _extra_delay()
            if detail is None:
                continue
            # Validación final: el detail debe seguir apuntando a este
            # mismo jugador de un lado -- nunca se confía ciegamente en
            # el match_id sin re-chequear identidad real.
            #
            # 2026-08-09, BUG REAL (causa raíz dominante del "resolved=0
            # universal" en producción, verificado en vivo con "Alex
            # Molcan" -- 467 candidatos reales, 46 matches encontrados
            # por `find_matching_candidate` en el primer año probado,
            # los 46 rechazados acá): `player_a_name` de un candidato
            # viene en formato "Nombre Apellido" (convención del
            # dedupe/roster), pero `get_match_detail` devuelve el
            # nombre NATIVO de TennisExplorer, "Apellido Nombre" (ej.
            # "Molcan Alex"). La comparación de substring/igualdad de
            # string exacta NUNCA matchea por el orden invertido --
            # mismo patrón de bug ya documentado en memoria
            # (kairos-wta-name-order-mismatch-breaks-backtest). Fix:
            # comparar por CONJUNTO de tokens, no por string ni orden.
            if not (
                _same_player_name(player_a_name, detail["player_a_name"])
                or _same_player_name(player_a_name, detail["player_b_name"])
            ):
                continue
            with db_historical.connection_context():
                status = save_match_detail(detail)
                if status in ("created", "updated", "unchanged"):
                    TennisMatchHistoryUnvalidated.delete().where(TennisMatchHistoryUnvalidated.id == match.id).execute()
                    remaining.remove(match)
                    resolved += 1
                    # 2026-08-09, cosecha del slug real de AMBOS jugadores
                    # -- `get_match_detail` ya lo conoce con certeza (del
                    # `href`, no de una búsqueda por nombre), así que la
                    # próxima vez que cualquiera de los 2 necesite
                    # resolverse por nombre, `_get_candidate_slugs` ya lo
                    # va a encontrar sin depender de la heurística naive.
                    now = datetime.now(timezone.utc)
                    _harvest_slug(detail["player_a_name"], detail.get("player_a_slug"), now)
                    _harvest_slug(detail["player_b_name"], detail.get("player_b_slug"), now)

    return {"player": player_a_name, "resolved": resolved, "unresolved": len(remaining)}


def run_once(batch_players: int | None = 200) -> dict:
    """Procesa hasta `batch_players` jugadores distintos con candidatos
    pendientes, en paralelo (`_MAX_WORKERS` threads). `batch_players=None`
    -- sin tope, procesa TODOS los jugadores con candidatos pendientes
    (2026-08-09, pedido explícito del usuario: el backfill debe ir por
    lo que realmente falta, no por un tope arbitrario)."""
    from database.db import init_db_historical, db_historical
    from database.historical.tennis import TennisMatchHistoryUnvalidated

    init_db_historical()
    with db_historical.connection_context():
        rows = list(TennisMatchHistoryUnvalidated.select())

    by_player: dict[str, list] = {}
    for r in rows:
        by_player.setdefault(r.player_a_name, []).append(r)

    player_names = list(by_player.keys())[:batch_players]
    total_resolved = total_unresolved = 0
    players_processed = 0

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(process_player, name, by_player[name]): name for name in player_names}
        for fut in as_completed(futures):
            try:
                result = fut.result()
                total_resolved += result["resolved"]
                total_unresolved += result["unresolved"]
                players_processed += 1
            except Exception as e:
                print(f"[validate_unresolved_via_live_fetch] error procesando {futures[fut]}: {e}", flush=True)

    summary = {
        "players_in_queue_total": len(player_names := list(by_player.keys())),
        "players_processed_this_batch": players_processed,
        "resolved": total_resolved, "unresolved_remaining_in_batch": total_unresolved,
    }
    print(f"[validate_unresolved_via_live_fetch] {summary}", flush=True)
    return summary


def run_forever(batch_players: int = 200, sleep_between_batches_s: float = 30.0) -> None:
    """Corre indefinidamente hasta agotar la cola -- pensado para
    dejarse corriendo en background en la mini-pc (nohup/screen).
    Seguro de interrumpir/reanudar en cualquier momento: los candidatos
    ya resueltos se borran de la tabla, así que retomar más tarde solo
    sigue con lo que falte."""
    while True:
        summary = run_once(batch_players=batch_players)
        if summary["players_in_queue_total"] == 0:
            print("[validate_unresolved_via_live_fetch] cola vacía, terminado", flush=True)
            break
        time.sleep(sleep_between_batches_s)


if __name__ == "__main__":
    run_forever()
