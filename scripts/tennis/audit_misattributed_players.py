"""Auditoría + limpieza de jugadores/as potencialmente mal atribuidos
por el bug real de `_verify_player_page` (2026-08-06, ver
`tennisexplorer_player_scraper.py` para el detalle completo del bug:
"Casper Ruud" -> `guess_player_slug` adivinaba la página real de
"Christian Ruud", su padre -- la tolerancia vieja solo exigía que el
APELLIDO coincidiera, aceptando así a cualquier persona distinta con
el mismo apellido).

Alcance real medido antes de este script: 225/1,285 jugadores/as ya
backfilleados como "self" comparten apellido con al menos otra persona
real en el universo conocido (TML-Database + WtaPlayerRankingHistory +
TennisMatchHistory) -- riesgo real y amplio, verificado con al menos 1
caso confirmado (Casper Ruud).

Con el fix YA aplicado (`_verify_player_page` exige apellido exacto +
nombre exacto/inicial/prefijo real), este script re-verifica cada uno
de esos 225 vía `guess_player_slug` (que ahora usa la verificación
corregida): si CONFIRMA la identidad real, se deja como está. Si NO
puede confirmarla (fail-closed, `None`), los datos existentes
(`TennisMatchHistory`/`TennisHistoricalOdds` de esa persona) quedan
SIN GARANTÍA de pertenecer a quien creemos -- se BORRAN en vez de
dejarlos silenciosamente corruptos en la base (mejor sin dato que con
un dato de la persona equivocada).

Throttle: reusa `guess_player_slug` tal cual (mismo
`tennisexplorer_throttle`, 0.5s mínimo entre requests, ya vigente en
todo el módulo) -- 1 sola request por jugador/a (no re-descarga los 11
años, solo la página base para confirmar identidad)."""

from __future__ import annotations

from database.db import init_db_historical, db_historical
from database.historical.tennis import TennisMatchHistory, TennisHistoricalOdds, WtaPlayerRankingHistory
from database.historical.tennis.tmldatabasematch import TmlDatabaseMatch
from outer_apis.scrapers.tennis.tennisexplorer_player_scraper import guess_player_slug


def find_at_risk_players() -> list[str]:
    """Jugadores/as ya backfilleados como "self" que comparten el
    ÚLTIMO token del nombre (proxy real de "mismo apellido") con al
    menos otra persona real conocida -- ver docstring del módulo."""
    init_db_historical()
    self_names = [r.player_name for r in TennisMatchHistory.select(TennisMatchHistory.player_name).distinct()]

    full_universe: set[str] = set(self_names)
    for r in TmlDatabaseMatch.select(TmlDatabaseMatch.winner_name).distinct():
        full_universe.add(r.winner_name)
    for r in TmlDatabaseMatch.select(TmlDatabaseMatch.loser_name).distinct():
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
    for n in self_names:
        tokens = n.strip().split(" ")
        if len(tokens) < 2:
            continue
        others = by_last_token.get(tokens[-1].lower(), set()) - {n}
        if others:
            at_risk.append(n)
    return sorted(at_risk)


def run_audit() -> dict:
    init_db_historical()
    at_risk = find_at_risk_players()
    print(f"[audit_misattributed_players] {len(at_risk)} jugadores/as en riesgo a re-verificar", flush=True)

    confirmed = purged = 0
    purged_names: list[str] = []
    for i, name in enumerate(at_risk, start=1):
        slug = guess_player_slug(name)
        if slug is not None:
            confirmed += 1
            status = "confirmado"
        else:
            with db_historical.connection_context():
                TennisMatchHistory.delete().where(TennisMatchHistory.player_name == name).execute()
                TennisHistoricalOdds.delete().where(TennisHistoricalOdds.player_name == name).execute()
            purged += 1
            purged_names.append(name)
            status = "PURGADO (identidad no confirmable, datos previos podían ser de otra persona)"
        print(f"[audit_misattributed_players] ({i}/{len(at_risk)}) {name} -> {status}", flush=True)

    summary = {"at_risk_total": len(at_risk), "confirmed": confirmed, "purged": purged, "purged_names": purged_names}
    print(f"[audit_misattributed_players] TERMINADO: {confirmed} confirmados, {purged} purgados", flush=True)
    return summary


if __name__ == "__main__":
    import json
    print(json.dumps(run_audit(), indent=2, ensure_ascii=False))
