"""Backtest WTA de la estrategia final elegida en ATP (favorito por
ranking real + gap>=20 + mejor winrate real por superficie) --
2026-08-05, pedido explícito del usuario: "calcular por separado los
resultados de esta estrategia tanto en ATP como WTA".

DIFERENCIAS ESTRUCTURALES REALES vs. la versión ATP
(`favorite_surface_winrate_filter_backtest.py`), documentadas porque no
es una simple sustitución de fuente:

  - RANKING: TML-Database (ATP) trae el ranking real EMBEBIDO en cada
    fila de partido. WTA no tiene equivalente -- acá el ranking sale de
    `WtaPlayerRankingHistory` (serie semanal real, backfilleada aparte
    vía `wta_player_ranking_backfill.py`, API oficial WTA), resuelto
    por (jugadora, fecha) buscando la entrada semanal más reciente <=
    la fecha del partido.
  - PARTIDOS: de `TennisMatchHistory` (TennisExplorer, ya poblada por
    `wta_top200_historical_backfill.py` para ATP Y WTA mezclados en la
    MISMA tabla, sin columna de tour). El roster de `WtaPlayerRankingHistory`
    actúa como clasificador natural: un partido solo cuenta si AMBAS
    jugadoras resuelven a una jugadora real de ese roster -- fail-closed,
    excluye automáticamente cualquier partido ATP mezclado.
  - SCORE: TennisExplorer reorienta el score al "dueño de la página"
    (self primero en cada set), NO al ganador como TML-Database -- ver
    `tennisexplorer_player_scraper.py::_reorient_score_to_self`. Para la
    variante 2 (set1, agregada 2026-08-05) esto SÍ importa -- `_self_won_set1`
    parsea con esa convención, y `favorite_won_set1` se traduce según de
    qué lado esté la favorita (self o no).
  - DEDUP: un mismo partido real puede aparecer 2 veces en
    `TennisMatchHistory` (una fila por cada jugadora, si se backfilleó
    el historial de ambas) -- se deduplica por (par de jugadoras, fecha).
  - BEST OF: SIEMPRE 3 (regla real del circuito WTA, nunca al mejor de
    5, ni siquiera en Slams -- no es un supuesto, es la regla del
    deporte).

ALCANCE: 2 variantes -- "1_simple_bet_favorite" (apostar al favorito
pre-partido, sostener hasta el resultado final) y "2_bet_only_after_set1_win"
(SIN exposición pre-partido, agregada 2026-08-05 a pedido explícito:
"añade a la estrategia de wta apostar sólo cuando la favorita gana el
primer set"). No las 6 variantes restantes de ATP (refuerzo/retiro/hedge
condicionales al set 2), fuera de alcance de lo pedido hasta ahora."""

from __future__ import annotations

import json
from bisect import bisect_right
from typing import Any

from database.db import init_db_historical
from database.historical.tennis import MatchHistory, WtaPlayerRankingHistory, WtaTournamentLevel
from outer_apis.kalshi.fees import leg_fee_fraction
from models.volatility.sports.tennis.tennis_markov import pre_match_win_prob, p_serve_for_rank, _p_win_game, _p_win_set
from scripts.tennis.favorite_by_historical_rank_backtest import _p_win_match_given_set_score
from scripts.reports import risk_metrics

_EVAL_START_YEAR = 2016
# 2026-08-05, pedido explícito del usuario: "sólo tomemos 250 en
# adelante y para wta mejoremos la condición a 30 puntos de diferencia
# de ranking" -- revierte el intento de incluir 125 (mismo día,
# descartado) y AMPLÍA el gap mínimo de ranking de WTA a 30 (diverge de
# ATP a propósito, ya NO es "mismo umbral" -- decisión explícita del
# usuario, ATP se queda en 20 sin cambios).
_MIN_RANK_GAP = 30
_BEST_OF = 3  # regla real WTA, nunca best-of-5

# "250 en adelante" del lado WTA -- equivalente real de
# `TOUR_LEVELS_250_PLUS` de ATP (250/500/M/G/F). WTA rebrandeó los
# nombres de categoría en 2021 (International->WTA 250, Premier->WTA
# 500, Premier 5/Premier Mandatory->WTA 1000) -- se incluyen AMBOS
# juegos de nombres porque el catálogo de `WtaTournamentLevel` cubre
# años de antes y después del rebrand. EXCLUIDOS (por debajo de 250):
# "WTA 125"/"125K" (circuito de desarrollo) e "ITF". "Finals" (WTA
# Finals, fin de temporada) cuenta como tour principal.
_WTA_MAIN_TOUR_LEVELS = frozenset({
    "Grand Slam", "WTA 1000", "WTA 500", "WTA 250",
    "Premier", "Premier 5", "Premier Mandatory", "International", "Finals",
})

# 2026-08-05, pedido explícito del usuario ("mantengamos el 1% por
# apuesta en la wta, pero ampliemos a 125k y otra prueba ampliando a
# itf") -- umbrales alternativos para pruebas EXPLORATORIAS de scope,
# sin tocar el default (`_WTA_MAIN_TOUR_LEVELS`, 250+, sigue siendo el
# vigente/comiteado). "125K" es el nombre pre-rebrand de "WTA 125"
# (mismo tier, distinto naming según el año -- se incluyen ambos, igual
# que se hace arriba con Premier/WTA*).
_WTA_LEVELS_125_PLUS = _WTA_MAIN_TOUR_LEVELS | {"WTA 125", "125K"}
_WTA_LEVELS_ITF_PLUS = _WTA_LEVELS_125_PLUS | {"ITF"}


# Alias reales conocidos donde el nombre de TennisExplorer difiere del
# nombre oficial de la fuente WTA (`tournamentGroup.name`) -- 2026-08-05,
# verificado en vivo: TennisExplorer usa "French Open", la API oficial
# usa "ROLAND GARROS" para el mismo evento (único mismatch real
# encontrado entre los 4 majors; Australian Open/Wimbledon/US Open
# coinciden literalmente tras normalizar)."""
_TOURNAMENT_NAME_ALIASES = {"FRENCH OPEN": "ROLAND GARROS"}


def _normalize_tournament_name(raw: str | None) -> str | None:
    """Normaliza el nombre libre de torneo de `TennisMatchHistory`
    (TennisExplorer, ej. "Rome 2 WTA", "Indian Wells", "Madrid WTA")
    para matchear contra el nombre oficial de `WtaTournamentLevel`
    (WTA API, ej. "ROME", "INDIAN WELLS", "MADRID") -- 2026-08-05.

    Reglas reales observadas en vivo (ver docstring del módulo/backfill
    para ejemplos): quita sufijos de tour/nivel que TennisExplorer
    agrega ("WTA", "ITF", "challenger" -- este último no debería
    aparecer en filas WTA reales, pero se limpia igual por si acaso),
    quita tokens numéricos sueltos (TennisExplorer distingue ediciones
    duplicadas del mismo año con "Rome 2"/"Madrid 19" etc.), colapsa
    espacios, mayúsculas. Fail-closed: None si no queda nada real tras
    limpiar."""
    if not raw:
        return None
    tokens = [t for t in raw.strip().upper().split(" ") if t]
    drop = {"WTA", "ITF", "CHALLENGER"}
    kept = [t for t in tokens if t not in drop and not t.isdigit()]
    if not kept:
        return None
    normalized = " ".join(kept)
    return _TOURNAMENT_NAME_ALIASES.get(normalized, normalized)


def _load_tournament_level_index() -> dict[tuple[int, str], str | None]:
    """{(year, normalized_name): level} -- cargado una sola vez desde
    `WtaTournamentLevel` (backfilleada aparte vía
    `wta_tournament_level_backfill.py`, catálogo oficial completo).

    2026-08-05, bug real encontrado en vivo: la normalización quita
    tokens numéricos ("MADRID 2 ITF" -> "MADRID"), lo que puede colisionar
    con el nombre real del torneo principal ("MADRID" -> Premier
    Mandatory) DESPUÉS de normalizar, aunque sean filas distintas y sin
    colisión en la propia tabla (`(year, tournament_name)` real es
    único, pero `(year, normalized_name)` no lo es). Mismo criterio de
    no-degradación que el backfill (`_priority`): ante colisión post-
    normalización, se conserva el nivel de MAYOR jerarquía real."""
    from scripts.tennis.wta_tournament_level_backfill import _priority

    init_db_historical()
    index: dict[tuple[int, str], str | None] = {}
    for r in WtaTournamentLevel.select():
        norm = _normalize_tournament_name(r.tournament_name)
        if norm is None:
            continue
        key = (r.year, norm)
        if key not in index or _priority(r.level) > _priority(index[key]):
            index[key] = r.level
    return index


def _is_main_tour_level(
    tournament_raw: str | None, match_date: str, level_index: dict[tuple[int, str], str | None],
    allowed_levels: frozenset[str] = _WTA_MAIN_TOUR_LEVELS,
) -> bool:
    """True solo si el torneo resuelve a un nivel real dentro de
    `allowed_levels` (default: `_WTA_MAIN_TOUR_LEVELS`, 250+ -- el
    umbral vigente/comiteado; ver `_WTA_LEVELS_125_PLUS`/
    `_WTA_LEVELS_ITF_PLUS` para pruebas exploratorias de scope más
    amplio). Fail-closed: cualquier torneo que no matchee contra el
    catálogo oficial (nombre no encontrado para ese año) queda
    EXCLUIDO -- nunca se asume que es main-tour por default."""
    norm = _normalize_tournament_name(tournament_raw)
    if norm is None:
        return False
    try:
        year = int(match_date[:4])
    except (ValueError, TypeError):
        return False
    level = level_index.get((year, norm))
    return level in allowed_levels


def _self_won_set1(score: str | None) -> bool | None:
    """True/False/None -- a diferencia de TML-Database (score
    ganador-primero), TennisExplorer reorienta el score al DUEÑO DE LA
    PÁGINA (self primero en cada set, ver
    `tennisexplorer_player_scraper.py::_reorient_score_to_self`, mismo
    convenio confirmado en vivo: "Paolini - Eala", "6-4, 4-6, 6-3" =
    Paolini (primer nombre listado, self) ganó el set 1 6-4). Fail-closed:
    None si `score` está vacío o el primer set no matchea "N-M"."""
    return _self_won_set_n(score, 1)


def _normalize_score_separators(score: str) -> str:
    """2026-08-06, bug real encontrado en vivo (usuario preguntó por el
    winrate de Svitolina tras ganar el set1, 28 de sus 563 partidos
    reales quedaban sin dato de set1) -- `TennisMatchHistory` es una
    tabla COMPARTIDA entre 2 scrapers reales: TennisExplorer
    (`source="tennisexplorer"`, sets separados por COMA, ej.
    "6-4, 4-6, 6-3") y Flashscore (`source="flashscore"`, un pipeline
    más viejo/paralelo que sigue activo para tracking en vivo -- sets
    separados por ESPACIO, ej. "7-6 6-4", verificado en vivo: 7,738 de
    8,050 filas `source="flashscore"` tienen este formato). Ninguno de
    los 2 parsers de set1/set2 sabía de esta segunda convención --
    fail-closed correcto (nunca corrompió nada), pero descartaba
    partidos reales con set1 perfectamente determinable.

    Normaliza a la convención de coma (que el resto del pipeline ya
    espera) SOLO si el string no trae comas -- si ya viene con coma
    (TennisExplorer, o cualquier score de un solo set) se devuelve tal
    cual, sin tocarlo."""
    if "," in score:
        return score
    import re
    return re.sub(r"(?<=\d)\s+(?=\d+-\d+)", ", ", score.strip())


def _self_won_set_n(score: str | None, set_number: int) -> bool | None:
    """Generalización de `_self_won_set1` para cualquier set --
    2026-08-05, pedido explícito del usuario: "apliquemos las mismas
    estrategias que aplicamos en atp para buscar la óptima" -- las
    variantes 3/5/6/8 necesitan también el resultado del SET 2 (`_set_2`),
    mismo convenio self-first de TennisExplorer que `_self_won_set1`.
    Fail-closed: None si no hay suficientes sets en el score, o el set
    pedido no matchea "N-M", o está empatado (dato inconsistente)."""
    if not score:
        return None
    import re
    sets = [s.strip() for s in _normalize_score_separators(score).split(",")]
    if len(sets) < set_number:
        return None
    m = re.match(r"^(\d+)-(\d+)$", sets[set_number - 1])
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    if a == b:
        return None
    return a > b


def _self_set1_game_diff(score: str | None) -> int | None:
    """Diferencia de games del set 1 desde la perspectiva de "self"
    (positiva si self ganó el set 1) -- equivalente WTA de
    `favorite_by_historical_rank_backtest.py::_set1_game_diff`, pero en
    convenio self-first de TennisExplorer (no ganador-primero de
    TML-Database). Necesario para la variante 7 (condición de alta
    remontada, `set1_diff<=2`) -- aunque esa condición también exige
    `best_of=5` (nunca ocurre en WTA, ver docstring de
    `_matches_high_comeback_condition`), se calcula igual por
    completitud/consistencia con el resto del pipeline. Fail-closed:
    None si el score no tiene un set 1 parseable."""
    if not score:
        return None
    import re
    first_set = _normalize_score_separators(score).split(",")[0].strip()
    m = re.match(r"^(\d+)-(\d+)$", first_set)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    return a - b


def _single_set_win_prob_wta(rank_a: int, rank_b: int, surface: str | None) -> float:
    """Equivalente WTA de `favorite_by_historical_rank_backtest.py::
    _single_set_win_prob` (que hardcodea tour="ATP") -- misma
    metodología, `tour="WTA"` explícito."""
    pa_serve = p_serve_for_rank(rank_a, tour="WTA", surface=surface)
    pb_serve = p_serve_for_rank(rank_b, tour="WTA", surface=surface)
    pa_game = _p_win_game(pa_serve)
    pb_game = _p_win_game(pb_serve)
    return _p_win_set(pa_game, pb_game, a_serves_first=True, pa_serve=pa_serve, pb_serve=pb_serve)


def _load_ranking_index() -> dict[str, list[tuple[str, int]]]:
    """{player_name: [(ranked_at, singles_ranking), ...]} ordenado
    cronológicamente -- cargado una sola vez en memoria para resolver
    miles de lookups sin una query por partido."""
    init_db_historical()
    index: dict[str, list[tuple[str, int]]] = {}
    for r in WtaPlayerRankingHistory.select().order_by(WtaPlayerRankingHistory.ranked_at):
        index.setdefault(r.player_name, []).append((r.ranked_at, r.singles_ranking))
    return index


def _rank_at(name: str, date_iso: str, index: dict[str, list[tuple[str, int]]]) -> int | None:
    """Ranking real vigente en `date_iso` -- la entrada semanal más
    reciente CON FECHA <= date_iso. Fail-closed: None si la jugadora no
    está en el roster, o si su primera entrada real es POSTERIOR a
    `date_iso` (todavía no estaba rankeada en esa fecha -- no se
    extrapola hacia atrás)."""
    entries = index.get(name)
    if not entries:
        return None
    dates = [e[0] for e in entries]
    i = bisect_right(dates, date_iso) - 1
    if i < 0:
        return None
    return entries[i][1]


def collect_wta_cases(allowed_levels: frozenset[str] = _WTA_MAIN_TOUR_LEVELS) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Arma los casos evaluables de `TennisMatchHistory`, resolviendo
    ranking real point-in-time vía `WtaPlayerRankingHistory` y
    deduplicando partidos que aparecen 2 veces (una fila por
    jugadora). `allowed_levels`: umbral de categoría de torneo (default
    250+, el vigente/comiteado) -- ver `_WTA_LEVELS_125_PLUS`/
    `_WTA_LEVELS_ITF_PLUS` para pruebas exploratorias, 2026-08-05,
    pedido explícito del usuario."""
    init_db_historical()
    ranking_index = _load_ranking_index()
    level_index = _load_tournament_level_index()
    eval_cutoff = f"{_EVAL_START_YEAR}-01-01"

    seen: set[tuple[frozenset, str]] = set()
    cases: list[dict[str, Any]] = []
    skipped_no_surface = skipped_not_wta_roster = skipped_no_rank_data = skipped_duplicate = 0
    # 2026-08-09: `skipped_opponent_name_unresolved` queda siempre en 0 --
    # `MatchHistory` (migrado de `TennisMatchHistory`, ver writer.py) trae
    # nombre completo real en AMBOS lados (`player_a_name`/`player_b_name`),
    # ya no hace falta ninguna resolución de abreviación (la clave del bug
    # real de 2026-08-05 que motivó `_build_abbrev_to_full_name_index` --
    # eliminada junto con esta migración). Se mantiene la clave en el
    # dict de retorno para no romper firmas/tests existentes.
    skipped_opponent_name_unresolved = skipped_below_250_level = 0

    # 2026-08-05, mismo bug real encontrado y corregido en
    # `tml_database_scraper.py::get_matches_range_from_db` (ver ese
    # módulo): SIN order_by() explícito, SQLite no garantiza orden
    # físico de lectura, y `_build_surface_winrate_tracker` de abajo
    # solo ordena por fecha (empates de mismo día quedan en el orden
    # que llegue la query) -- el cupo diario de capital río abajo puede
    # aceptar/descartar partidos distintos según ese orden no
    # determinístico. `match_date, id` como desempate estable.
    rows = (
        MatchHistory.select()
        .where(
            (MatchHistory.surface.is_null(False))
            & (MatchHistory.match_date >= eval_cutoff)
            & (MatchHistory.winner_side.is_null(False))
        )
        .order_by(MatchHistory.match_date, MatchHistory.id)
    )
    for r in rows:
        player_name, opponent_full_name = r.player_a_name, r.player_b_name

        # 2026-08-05, pedido explícito del usuario: "me faltó agregar
        # que solo tomen matches de torneos 250 para arriba en nivel
        # [también para WTA]" -- equivalente real de `TOUR_LEVELS_250_PLUS`
        # de ATP. Fail-closed: torneo sin match real en el catálogo
        # oficial (`WtaTournamentLevel`) para ese año se EXCLUYE, nunca
        # se asume main-tour por default.
        if not _is_main_tour_level(r.tournament, r.match_date, level_index, allowed_levels=allowed_levels):
            skipped_below_250_level += 1
            continue

        dedup_key = (frozenset({player_name, opponent_full_name}), r.match_date)
        if dedup_key in seen:
            skipped_duplicate += 1
            continue
        seen.add(dedup_key)

        if player_name not in ranking_index or opponent_full_name not in ranking_index:
            skipped_not_wta_roster += 1
            continue

        rank_self = _rank_at(player_name, r.match_date, ranking_index)
        rank_opp = _rank_at(opponent_full_name, r.match_date, ranking_index)
        if rank_self is None or rank_opp is None:
            skipped_no_rank_data += 1
            continue
        if rank_self == rank_opp:
            # empate real de ranking -- no se puede determinar favorito
            # sin adivinar, fail-closed (mismo criterio que el resto de
            # la sesión: nunca asumir).
            skipped_no_rank_data += 1
            continue

        winner_name = player_name if r.winner_side == "a" else opponent_full_name
        favorite_is_self = rank_self < rank_opp
        favorite_name = player_name if favorite_is_self else opponent_full_name
        underdog_name = opponent_full_name if favorite_is_self else player_name
        favorite_rank = min(rank_self, rank_opp)
        underdog_rank = max(rank_self, rank_opp)

        # 2026-08-05, pedido explícito del usuario: "añade a la
        # estrategia de wta apostar sólo cuando la favorita gana el
        # primer set" -- `_self_won_set1` devuelve en términos de
        # `player_a_name` (score orientado a player_a en MatchHistory,
        # ver writer.py); se traduce a "¿ganó la FAVORITA?" según de qué
        # lado esté la favorita.
        self_won_set1 = _self_won_set1(r.score)
        favorite_won_set1 = (
            None if self_won_set1 is None
            else (self_won_set1 if favorite_is_self else not self_won_set1)
        )

        # 2026-08-05, pedido explícito del usuario: "apliquemos las
        # mismas estrategias que aplicamos en atp para buscar la
        # óptima (las variantes)" -- las variantes 3/5/6/8 de
        # `_simulate_realistic_all_strategies` (favorite_by_historical_rank_backtest.py)
        # necesitan también el resultado del set 2 y el diff de games
        # del set 1, mismo criterio de traducción self->favorito que
        # `favorite_won_set1` arriba.
        self_won_set2 = _self_won_set_n(r.score, 2)
        favorite_won_set2 = (
            None if self_won_set2 is None
            else (self_won_set2 if favorite_is_self else not self_won_set2)
        )
        self_set1_diff = _self_set1_game_diff(r.score)
        favorite_set1_diff = None if self_set1_diff is None else (self_set1_diff if favorite_is_self else -self_set1_diff)

        cases.append({
            "date": r.match_date, "favorite_name": favorite_name, "underdog_name": underdog_name,
            "favorite_rank": favorite_rank, "underdog_rank": underdog_rank,
            "rank_gap": underdog_rank - favorite_rank,
            "hit": favorite_name == winner_name,
            "surface": r.surface, "favorite_won_set1": favorite_won_set1,
            # nombres alineados con `favorite_by_historical_rank_backtest.py`
            # para poder reusar `_simulate_realistic_all_strategies` tal cual.
            "set1_favorite_win": favorite_won_set1, "set2_favorite_win": favorite_won_set2,
            "set1_diff": favorite_set1_diff, "best_of": _BEST_OF,
            "favorite_age": None,  # WTA sin DOB/age disponible (limitación conocida) -- variante 7 fail-closed a "retirar"
        })

    return cases, {
        "skipped_not_wta_roster": skipped_not_wta_roster, "skipped_no_rank_data": skipped_no_rank_data,
        "skipped_duplicate": skipped_duplicate, "skipped_opponent_name_unresolved": skipped_opponent_name_unresolved,
        "skipped_below_250_level": skipped_below_250_level,
    }


def _build_surface_winrate_tracker(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Mismo criterio point-in-time que la versión ATP -- pero acá parte
    de `cases` ya resueltos (con ranking real), no de partidos crudos,
    porque el ranking real WTA no viene embebido por fila."""
    cases_sorted = sorted(cases, key=lambda c: c["date"])
    record: dict[tuple[str, str], list[int]] = {}
    result: list[dict[str, Any]] = []
    skipped_no_surface_history = 0

    for c in cases_sorted:
        # el "underdog_name" real necesita reconstruirse -- no se guardó
        # directamente en `collect_wta_cases` (solo favorite_name), pero
        # alcanza con saber que hubo 2 jugadoras reales en el partido.
        surface = c["surface"]
        fav_rec = record.get((c["favorite_name"], surface))
        # underdog: point-in-time también, pero como no guardamos su
        # nombre explícito acá (solo rank), el tracker de superficie
        # necesita el nombre real -- ver `collect_wta_cases`, que ya
        # incluye "underdog_name" en el dict (agregado abajo).
        dog_rec = record.get((c["underdog_name"], surface))

        if fav_rec is None or dog_rec is None or (fav_rec[0] + fav_rec[1]) == 0 or (dog_rec[0] + dog_rec[1]) == 0:
            skipped_no_surface_history += 1
        else:
            fav_wr = fav_rec[0] / (fav_rec[0] + fav_rec[1])
            dog_wr = dog_rec[0] / (dog_rec[0] + dog_rec[1])
            result.append({**c, "favorite_surface_winrate": round(fav_wr, 4), "underdog_surface_winrate": round(dog_wr, 4)})

        winner_name = c["favorite_name"] if c["hit"] else c["underdog_name"]
        loser_name = c["underdog_name"] if c["hit"] else c["favorite_name"]
        wr = record.setdefault((winner_name, surface), [0, 0])
        wr[0] += 1
        lr = record.setdefault((loser_name, surface), [0, 0])
        lr[1] += 1

    return result, {"skipped_no_surface_history": skipped_no_surface_history}


def _entry_fee_frac(price: float) -> float:
    fee_per_contract = leg_fee_fraction(price) or 0.0
    return fee_per_contract / price if price > 0 else 0.0


def _net_hold_pct(price: float, hit: bool) -> float:
    raw = (1 - price) / price if hit else -1.0
    return raw - _entry_fee_frac(price)


def _measure_trading_days_per_year(cases: list[dict[str, Any]], default: float = 60.0) -> float:
    dates = sorted({c["date"] for c in cases})
    if len(dates) < 2:
        return default
    from datetime import date as _date
    first, last = _date.fromisoformat(dates[0]), _date.fromisoformat(dates[-1])
    years = (last - first).days / 365.25
    return len(dates) / years if years > 0 else default


def _simulate_simple_hold_strategy(
    cases: list[dict[str, Any]], initial_capital: float, stake_pct: float = 0.01,
    max_simultaneous_pct: float = 0.5, max_stake_usd: float | None = 60_000.0,
) -> dict[str, Any]:
    """Solo la estrategia "1_simple_bet_favorite" -- mismo motor de
    sizing/cupo/fees que el resto de esta sesión (MLB/ATP)."""
    by_date: dict[str, list[dict[str, Any]]] = {}
    for c in cases:
        by_date.setdefault(c["date"], []).append(c)

    capital = initial_capital
    busted = False
    trade_returns: list[float] = []
    resolve_hits = resolve_n = 0
    equity_curve: list[float] = [initial_capital]
    skipped_cap = 0

    for date in sorted(by_date.keys()):
        if busted:
            equity_curve.append(capital)
            continue
        cap_start = capital
        max_exposure = cap_start * max_simultaneous_pct
        running_exposure = 0.0
        day_pnl = 0.0

        for c in by_date[date]:
            stake = cap_start * stake_pct
            if max_stake_usd is not None:
                stake = min(stake, max_stake_usd)
            if running_exposure + stake > max_exposure:
                skipped_cap += 1
                continue
            running_exposure += stake
            price = pre_match_win_prob(c["favorite_rank"], c["underdog_rank"], best_of=_BEST_OF, tour="WTA", surface=c.get("surface"))
            pct = _net_hold_pct(price, c["hit"])
            day_pnl += stake * pct
            trade_returns.append(pct)
            resolve_n += 1
            resolve_hits += 1 if c["hit"] else 0

        capital += day_pnl
        if capital <= 0:
            capital = 0.0
            busted = True
        equity_curve.append(capital)

    trading_days_per_year = _measure_trading_days_per_year(cases)
    max_dd = risk_metrics.max_drawdown_pct(equity_curve)
    cagr = risk_metrics.cagr_pct(equity_curve, initial_capital, trading_days_per_year)

    return {
        "n_cases": len(cases), "n_trades": resolve_n, "skipped_daily_cap": skipped_cap,
        "hit_rate": round(resolve_hits / resolve_n, 4) if resolve_n else None,
        "final_capital": round(capital, 2), "busted": busted,
        "trading_days_per_year_measured": round(trading_days_per_year, 2),
        "max_drawdown_pct": max_dd, "cagr_pct": cagr,
        "annualized_sharpe": risk_metrics.annualized_sharpe(equity_curve, initial_capital, trading_days_per_year),
        "annualized_sortino": risk_metrics.annualized_sortino(equity_curve, initial_capital, trading_days_per_year),
        "calmar_ratio": risk_metrics.calmar_ratio(cagr, max_dd),
        "cvar_95_pct": risk_metrics.cvar_95_pct(trade_returns),
    }


def _simulate_bet_after_set1_win(
    cases: list[dict[str, Any]], initial_capital: float, stake_pct: float = 0.01,
    max_simultaneous_pct: float = 0.5, max_stake_usd: float | None = 60_000.0,
) -> dict[str, Any]:
    """Variante 2 de ATP, adaptada a WTA -- 2026-08-05, pedido explícito
    del usuario: "añade a la estrategia de wta apostar sólo cuando la
    favorita gana el primer set". SIN exposición pre-partido: se entra
    recién cuando `favorite_won_set1` es real y True (fail-closed: casos
    sin score parseable, o donde la favorita PERDIÓ el set 1, quedan
    fuera -- no hay reprecio/retiro, solo entrada condicional).

    Precio de entrada = `_p_win_match_given_set_score(p_set_favorite, 1,
    0, best_of=3)`, mismo motor y misma fórmula que la variante 2 de
    ATP (`_real_price_legs`/`price_after_set1_win`), con `p_set_favorite`
    calibrado para WTA (`_single_set_win_prob_wta`)."""
    qualifying = [c for c in cases if c["favorite_won_set1"] is True]
    by_date: dict[str, list[dict[str, Any]]] = {}
    for c in qualifying:
        by_date.setdefault(c["date"], []).append(c)

    capital = initial_capital
    busted = False
    trade_returns: list[float] = []
    resolve_hits = resolve_n = 0
    equity_curve: list[float] = [initial_capital]
    skipped_cap = 0

    for date in sorted(by_date.keys()):
        if busted:
            equity_curve.append(capital)
            continue
        cap_start = capital
        max_exposure = cap_start * max_simultaneous_pct
        running_exposure = 0.0
        day_pnl = 0.0

        for c in by_date[date]:
            stake = cap_start * stake_pct
            if max_stake_usd is not None:
                stake = min(stake, max_stake_usd)
            if running_exposure + stake > max_exposure:
                skipped_cap += 1
                continue
            running_exposure += stake
            p_set_favorite = _single_set_win_prob_wta(c["favorite_rank"], c["underdog_rank"], c.get("surface"))
            price = _p_win_match_given_set_score(p_set_favorite, 1, 0, _BEST_OF)
            pct = _net_hold_pct(price, c["hit"])
            day_pnl += stake * pct
            trade_returns.append(pct)
            resolve_n += 1
            resolve_hits += 1 if c["hit"] else 0

        capital += day_pnl
        if capital <= 0:
            capital = 0.0
            busted = True
        equity_curve.append(capital)

    trading_days_per_year = _measure_trading_days_per_year(qualifying)
    max_dd = risk_metrics.max_drawdown_pct(equity_curve)
    cagr = risk_metrics.cagr_pct(equity_curve, initial_capital, trading_days_per_year)

    return {
        "n_cases": len(qualifying), "n_trades": resolve_n, "skipped_daily_cap": skipped_cap,
        "hit_rate": round(resolve_hits / resolve_n, 4) if resolve_n else None,
        "final_capital": round(capital, 2), "busted": busted,
        "trading_days_per_year_measured": round(trading_days_per_year, 2),
        "max_drawdown_pct": max_dd, "cagr_pct": cagr,
        "annualized_sharpe": risk_metrics.annualized_sharpe(equity_curve, initial_capital, trading_days_per_year),
        "annualized_sortino": risk_metrics.annualized_sortino(equity_curve, initial_capital, trading_days_per_year),
        "calmar_ratio": risk_metrics.calmar_ratio(cagr, max_dd),
        "cvar_95_pct": risk_metrics.cvar_95_pct(trade_returns),
    }


def run_backtest(
    initial_capital: float = 50.0, max_stake_usd: float | None = 60_000.0,
    allowed_levels: frozenset[str] = _WTA_MAIN_TOUR_LEVELS,
) -> dict[str, Any]:
    raw_cases, meta1 = collect_wta_cases(allowed_levels=allowed_levels)
    all_cases, meta2 = _build_surface_winrate_tracker(raw_cases)

    filtered_cases = [
        c for c in all_cases
        if c["rank_gap"] >= _MIN_RANK_GAP and c["favorite_surface_winrate"] > c["underdog_surface_winrate"]
    ]
    skipped_no_set1_data = sum(1 for c in filtered_cases if c["favorite_won_set1"] is None)

    # misma priorización que ATP: (fecha, favorite_rank ascendente)
    filtered_prioritized = sorted(filtered_cases, key=lambda c: (c["date"], c["favorite_rank"]))
    all_prioritized = sorted(all_cases, key=lambda c: (c["date"], c["favorite_rank"]))

    baseline_result = _simulate_simple_hold_strategy(all_prioritized, initial_capital, max_stake_usd=max_stake_usd)
    filtered_result = _simulate_simple_hold_strategy(filtered_prioritized, initial_capital, max_stake_usd=max_stake_usd)
    after_set1_win_result = _simulate_bet_after_set1_win(filtered_prioritized, initial_capital, max_stake_usd=max_stake_usd)

    return {
        "min_rank_gap_used": _MIN_RANK_GAP,
        "raw_matches_from_tennis_match_history": len(raw_cases), **meta1,
        "total_cases_with_surface_history": len(all_cases), **meta2,
        "filtered_cases_n": len(filtered_cases), "skipped_no_set1_data": skipped_no_set1_data,
        "baseline": baseline_result, "filtered": filtered_result,
        "filtered_bet_only_after_set1_win": after_set1_win_result,
        "caveat": (
            "Ranking real WTA resuelto vía WtaPlayerRankingHistory (API "
            "oficial WTA, serie semanal real) por (jugadora, fecha) -- "
            "NO viene embebido en el partido como en ATP/TML-Database. "
            "Partidos de TennisMatchHistory sin AMBAS jugadoras en el "
            "roster WTA real quedan excluidos (fail-closed, también "
            "filtra cualquier partido ATP mezclado en la misma tabla). "
            "'filtered' = variante 1 (apostar pre-partido, sostener hasta "
            "el resultado). 'filtered_bet_only_after_set1_win' = variante "
            "2 (SIN exposición pre-partido, entra recién si la favorita "
            "ya ganó el set 1) -- ambas sobre el mismo pool filtrado "
            "(gap>=30 + mejor winrate real por superficie, sin margen "
            "extra). No incluye las 6 variantes restantes de ATP "
            "(refuerzo/retiro/hedge condicionales al set 2)."
        ),
    }


def run_all_variants_backtest(
    initial_capital: float = 50.0, max_stake_usd: float | None = 60_000.0,
    allowed_levels: frozenset[str] = _WTA_MAIN_TOUR_LEVELS,
) -> dict[str, Any]:
    """Las 8 variantes de `favorite_by_historical_rank_backtest.py::
    _simulate_realistic_all_strategies` (reforzar/retirar/hedge
    condicionales al set 2), corridas sobre el MISMO pool filtrado WTA
    (gap>=30 + mejor winrate real por superficie + torneo 250+) --
    2026-08-05, pedido explícito del usuario: "apliquemos las mismas
    estrategias que aplicamos en atp para buscar la óptima (las
    variantes)".

    LIMITACIONES REALES vs. ATP (documentadas, no bugs):
      - `best_of` SIEMPRE 3 en WTA (regla real del circuito, nunca 5).
        La variante 7 (condición de "alta remontada") exige
        `best_of==5` de forma literal (hallazgo hecho sobre partidos a 5
        sets, ver `_matches_high_comeback_condition`) -- en WTA esa
        condición NUNCA se cumple, así que la variante 7 degenera
        exactamente a la variante 5 (mismo comportamiento: retiro
        incondicional en vez de condicional). No es un bug, es una
        limitación estructural real del deporte.
      - `favorite_age` siempre `None` (WTA sin fecha de nacimiento en la
        base, limitación conocida ya documentada) -- refuerza el
        fail-closed de la variante 7 de arriba (aunque best_of ya la
        descarta sola).

    BUG REAL encontrado y arreglado en el camino (2026-08-05): la
    función compartida `_simulate_realistic_all_strategies` traía
    hardcodeada `_TRADING_DAYS_PER_YEAR_ATP` (36.7, medida
    específicamente sobre el calendario ATP) para anualizar
    CAGR/Sharpe/Sortino -- reusarla tal cual para WTA (cadencia real
    ~200+ días/año en el pool filtrado) distorsionaba esas métricas por
    un factor de ~6-8x. Se agregó el parámetro `trading_days_per_year`
    (default None = preserva el comportamiento ATP exacto de siempre) y
    acá se mide la cadencia REAL de este pool con
    `_measure_trading_days_per_year`, igual que ya se hacía en
    `_simulate_simple_hold_strategy`/`_simulate_bet_after_set1_win`."""
    raw_cases, meta1 = collect_wta_cases(allowed_levels=allowed_levels)
    all_cases, meta2 = _build_surface_winrate_tracker(raw_cases)

    filtered_cases = [
        c for c in all_cases
        if c["rank_gap"] >= _MIN_RANK_GAP and c["favorite_surface_winrate"] > c["underdog_surface_winrate"]
    ]
    filtered_prioritized = sorted(filtered_cases, key=lambda c: (c["date"], c["favorite_rank"]))

    from scripts.tennis.favorite_by_historical_rank_backtest import _simulate_realistic_all_strategies

    result = _simulate_realistic_all_strategies(
        filtered_prioritized, initial_capital=initial_capital, max_stake_usd=max_stake_usd, tour="WTA",
        trading_days_per_year=_measure_trading_days_per_year(filtered_prioritized),
    )
    return {
        "min_rank_gap_used": _MIN_RANK_GAP, "filtered_cases_n": len(filtered_cases),
        **meta1, **meta2, **result,
    }


if __name__ == "__main__":
    print(json.dumps(run_backtest(), indent=2, ensure_ascii=False))
