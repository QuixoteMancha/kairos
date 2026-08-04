"""
Backtest de "apostar siempre al favorito" usando el ranking REAL que
cada jugador tenía AL MOMENTO del partido (no el ranking de hoy, a
diferencia de `scripts/favorite_strategy_backtest.py`) -- pedido
explícito del usuario 2026-08-02, resuelto vía
`outer_apis/scrapers/tennis/tml_database_scraper.py::TML-Database`
(registro COMPLETO del tour ATP, no sesgado, ver docstring de ese
módulo).

ALCANCE: SOLO ATP (sin equivalente WTA del mismo autor, investigado).
Torneos "250 en adelante" (`TOUR_LEVELS_250_PLUS`: 250/500/Masters/
Slam/Finals -- excluye Davis Cup y exhibición).

Ya no hace falta el caveat de "ranking actual como proxy" de los
backtests anteriores (`favorite_strategy_backtest.py`,
`sf_final_hypothesis_validation.py`) -- acá el ranking es real y
exacto para cada partido individual."""

from __future__ import annotations

import json
import statistics
from math import comb
from typing import Any

from outer_apis.scrapers.tennis.tml_database_scraper import get_matches_range, TOUR_LEVELS_250_PLUS
from outer_apis.kalshi.fees import leg_fee_fraction
from models.volatility.sports.tennis.tennis_markov import (
    pre_match_win_prob, p_serve_for_rank, _p_win_game, _p_win_set,
)

_DEFAULT_START_YEAR = 2016  # ~10 años atrás, mismo alcance usado en el resto de la sesión
_DEFAULT_END_YEAR = 2026


def parse_set1_winner(score: str | None) -> str | None:
    """'winner' | 'loser' | None -- quién de los dos (referido al
    GANADOR REAL del partido) se llevó el primer set, parseando el
    campo `score` real de TML-Database (2026-08-02, pedido explícito
    del usuario: "cuál es el winrate del favorito al set 1").

    Convención real confirmada en vivo (misma que Match Charting
    Project): el score SIEMPRE lista las games del GANADOR DEL PARTIDO
    primero en cada set -- ej. '0-6 6-4 6-4' significa que el ganador
    del partido PERDIÓ el primer set 0-6 antes de remontar. NO asume
    que quien ganó el partido ganó el primer set.

    Fail-closed: None si `score` está vacío, es un walkover/retiro sin
    set 1 jugable ('W/O'), o el primer token no matchea el patrón
    'N-M' (con o sin tiebreak entre paréntesis) -- nunca adivina."""
    if not score:
        return None
    first_set = score.strip().split(" ")[0]
    if not first_set or first_set.upper() in ("W/O", "WO"):
        return None
    import re
    m = re.match(r"^(\d+)-(\d+)", first_set)
    if not m:
        return None
    winner_games, loser_games = int(m.group(1)), int(m.group(2))
    if winner_games == loser_games:
        return None  # dato inconsistente, nunca debería pasar en un set real terminado
    return "winner" if winner_games > loser_games else "loser"


def parse_set2_winner(score: str | None) -> str | None:
    """'winner' | 'loser' | None -- igual que `parse_set1_winner` pero
    para el SEGUNDO set (2026-08-03, pedido explícito del usuario para
    la variante 8: "si el favorito pierde el segundo set, se retira la
    segunda apuesta"). Misma convención (games del GANADOR DEL PARTIDO
    primero) y mismo criterio fail-closed -- None si no hay un segundo
    set jugable (partido resuelto en 1 set por retiro, o el segundo
    token no matchea 'N-M')."""
    if not score:
        return None
    tokens = score.strip().split(" ")
    if len(tokens) < 2:
        return None
    second_set = tokens[1]
    if not second_set or second_set.upper() in ("W/O", "WO", "RET"):
        return None
    import re
    m = re.match(r"^(\d+)-(\d+)", second_set)
    if not m:
        return None
    winner_games, loser_games = int(m.group(1)), int(m.group(2))
    if winner_games == loser_games:
        return None
    return "winner" if winner_games > loser_games else "loser"


def _set1_game_diff(score: str | None) -> int | None:
    """Diferencia ABSOLUTA de games del set 1 -- vive acá (no en
    `scripts/favorite_comeback_pattern_analysis.py`, que la usó primero)
    para que la variante 7 de estrategia (2026-08-03, ver
    `_matches_high_comeback_condition`) pueda reutilizarla sin crear un
    import circular entre ambos scripts. Fail-closed: None en los
    mismos casos que `parse_set1_winner` -> None."""
    if not score:
        return None
    first_set = score.strip().split(" ")[0]
    import re
    m = re.match(r"^(\d+)-(\d+)", first_set)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    if a == b:
        return None
    return abs(a - b)


def _build_strategy_comparison(
    hits: int, n: int,
    match_hits_after_set1: int, match_after_set1_n: int,
    favorite_comeback_hits: int, favorite_comeback_n: int,
) -> dict[str, Any]:
    """5 verificaciones pedidas explícitamente por el usuario (3 el
    2026-08-02, +2 el 2026-08-03 tras revisar un dashboard con 5
    categorías) -- mismo dataset, mismas métricas ya calculadas arriba,
    solo re-empaquetadas como comparación directa de estrategia:

    1. 'simple': apostar al favorito y sostener hasta el final del
       partido -- tasa de acierto = `overall_hit_rate` (ya calculado).
    2. 'bet_only_after_favorite_wins_set1': NO hay posición pre-match --
       se entra recién cuando el favorito YA ganó el set 1. Numéricamente
       coincide con `match_win_rate_after_favorite_wins_set1` (mismo
       condicional), pero es una estrategia distinta de la 3: acá no hay
       capital pre-match expuesto mientras se juega el set 1.
    3. 'double_down_if_favorite_wins_set1': SÍ hay posición pre-match; si
       el favorito gana el set 1, se apuesta DE NUEVO al mismo lado
       (posición reforzada). Como ambas apuestas van al mismo lado, el
       resultado final es el mismo condicional que la 2, pero el capital
       en juego es distinto (2 exposiciones vs. 1).
    4. 'exit_if_favorite_loses_set1': apostar al favorito, RETIRAR la
       apuesta si pierde el set 1 -- el retiro fue la decisión CORRECTA
       cuando el favorito efectivamente termina perdiendo el partido
       también (si hubiera remontado, retirarse fue un error, se pierde
       la ganancia). Tasa de acierto del retiro = 1 -
       `favorite_comeback_rate_after_losing_set1`.
    5. 'combined_1_3_4': la estrategia REAL end-to-end -- apostar
       pre-match (1), reforzar si gana el set 1 (3), retirarse si lo
       pierde (4). Tasa de acierto = % de TODOS los casos con dato de
       set 1 (ambas ramas) donde la decisión tomada en cada rama fue la
       correcta -- pool de "reforzó y ganó" + "se retiró y el favorito
       de verdad perdía" sobre el total de casos con set 1 conocido.

    Fail-closed: cualquier tasa queda `None` si no hay casos evaluables
    (nunca se fabrica un porcentaje de 0 muestras)."""
    combined_n = match_after_set1_n + favorite_comeback_n
    combined_correct = match_hits_after_set1 + (favorite_comeback_n - favorite_comeback_hits)
    return {
        "1_simple_bet_favorite": {
            "description": "Apostar al favorito y sostener hasta el final del partido.",
            "evaluable_cases": n,
            "win_rate": round(hits / n, 4) if n else None,
        },
        "2_bet_only_after_favorite_wins_set1": {
            "description": (
                "SIN posición pre-match: entrar recién cuando el favorito "
                "YA ganó el set 1 (sin capital expuesto durante el set 1)."
            ),
            "evaluable_cases": match_after_set1_n,
            "win_rate": round(match_hits_after_set1 / match_after_set1_n, 4) if match_after_set1_n else None,
        },
        "3_double_down_if_favorite_wins_set1": {
            "description": (
                "Apostar al favorito; si gana el set 1, apostar DE NUEVO "
                "al favorito (misma dirección, posición reforzada, YA "
                "había capital pre-match expuesto durante el set 1)."
            ),
            "evaluable_cases": match_after_set1_n,
            "win_rate": round(match_hits_after_set1 / match_after_set1_n, 4) if match_after_set1_n else None,
        },
        "4_exit_if_favorite_loses_set1": {
            "description": (
                "Apostar al favorito; RETIRAR la apuesta si el favorito "
                "pierde el set 1 (stop-loss temprano)."
            ),
            "evaluable_cases": favorite_comeback_n,
            "win_rate": (
                round(1 - (favorite_comeback_hits / favorite_comeback_n), 4)
                if favorite_comeback_n else None
            ),
        },
        "5_combined_double_down_and_exit": {
            "description": (
                "Estrategia completa: apostar pre-match (1), reforzar si "
                "gana el set 1 (3), retirarse si lo pierde (4). Tasa de "
                "acierto pooled sobre TODOS los casos con dato de set 1."
            ),
            "evaluable_cases": combined_n,
            "win_rate": round(combined_correct / combined_n, 4) if combined_n else None,
        },
    }


# Sizing reutilizado de decisiones YA tomadas explícitamente por el
# usuario en esta misma investigación -- NO son números nuevos
# inventados para esta simulación:
#   - 5% pre-match: el tamaño real que el usuario usó en su compra
#     manual de Pegula (ver strategies/set1_favorite_reaction.py docstring).
#   - 1% al confirmarse el set 1: `SET1_ENTRY_SIZE_PCT` en
#     strategies/set1_favorite_reaction.py, pedido explícito del usuario.
_PRE_MATCH_STAKE_PCT = 0.05
_SET1_ADD_STAKE_PCT = 0.01


def _max_drawdown_pct(equity_curve: list[float]) -> float | None:
    """Máxima caída pico-a-valle de la curva de equity, en % del pico
    (escala-invariante -- no se ve afectado por que el capital final
    termine en magnitudes absurdas, ver advertencia metodológica abajo)."""
    if not equity_curve:
        return None
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
    return round(max_dd * 100, 2)


_FAIR_PRICE_BUCKETS = [
    (0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 0.75),
    (0.75, 0.80), (0.80, 0.85), (0.85, 0.90), (0.90, 1.01),
]


def _model_fair_price_calibration(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Pedido explícito del usuario 2026-08-03: "¿existe forma de
    aproximar las probabilidades pre-match históricas?" -- SÍ, para el
    motor BASE de `TennisFModel` (Markov puro por ranking+superficie,
    `models/volatility/sports/tennis/tennis_markov.py::pre_match_win_prob`),
    porque es una función PURA de ranking real al momento del partido
    (que TML-Database ya provee) + superficie + best_of -- sin ninguna
    dependencia de un scraper "en vivo".

    IMPORTANTE -- por qué NO se usan acá los otros 9 blends de
    `TennisFModel` (Elo de Tennis Abstract, forma reciente, H2H, forma
    por superficie, momentum de torneo, etc.): todos esos leen el
    snapshot ACTUAL de sus fuentes (ver p.ej.
    `outer_apis/sports/tennis/tennisabstract_elo.py::_fetch_elo_table`,
    que cachea el Elo de HOY, no un histórico point-in-time). Si se
    corrieran contra un partido de 2018, el Elo/forma que verían serían
    los de 2026 -- lookahead bias real, mismo problema ya documentado
    para Mercurius (`kairos-volumeprofile-lookahead-confirmed`). Por eso
    esta calibración usa SOLO el motor base, que es la única pieza
    100% point-in-time correcta con los datos que ya tenemos. Extender
    esto a los otros blends requeriría construir snapshots históricos
    reales de Elo/forma con fecha, que HOY no existen (siguiente paso
    posible, no construido acá).

    Bucketiza por el fair_a MODELADO del FAVORITO (no del ranking crudo)
    y compara contra el winrate REAL observado en cada bucket -- si el
    motor está bien calibrado, ambos deberían coincidir aproximadamente."""
    from models.volatility.sports.tennis.tennis_markov import pre_match_win_prob

    buckets: dict[str, dict[str, Any]] = {}
    for low, high in _FAIR_PRICE_BUCKETS:
        buckets[f"{low:.2f}-{min(high, 1.0):.2f}"] = {"n": 0, "hits": 0, "fair_a_sum": 0.0}

    skipped = 0
    for c in cases:
        best_of = c.get("best_of") or 3
        if best_of not in (3, 5):
            skipped += 1
            continue
        try:
            fair_a = pre_match_win_prob(
                c["favorite_rank"], c["underdog_rank"], best_of=best_of,
                tour="ATP", surface=c.get("surface"),
            )
        except ValueError:
            skipped += 1
            continue

        for low, high in _FAIR_PRICE_BUCKETS:
            if low <= fair_a < high:
                label = f"{low:.2f}-{min(high, 1.0):.2f}"
                b = buckets[label]
                b["n"] += 1
                b["hits"] += 1 if c["hit"] else 0
                b["fair_a_sum"] += fair_a
                break

    result = {}
    for label, b in buckets.items():
        n = b["n"]
        result[label] = {
            "n": n,
            "avg_model_fair_a": round(b["fair_a_sum"] / n, 4) if n else None,
            "actual_hit_rate": round(b["hits"] / n, 4) if n else None,
        }
    return {
        "buckets": result, "skipped_no_surface_or_bestof_info": skipped,
        "caveat": (
            "Calibración del motor BASE (ranking+superficie) únicamente -- "
            "los 9 blends adicionales de TennisFModel (Elo, forma, H2H, "
            "momentum, etc.) NO están incluidos acá porque sus fuentes son "
            "snapshots ACTUALES, no históricos point-in-time -- incluirlos "
            "sería lookahead bias real. Ver docstring de "
            "`_model_fair_price_calibration`."
        ),
    }


def _single_set_win_prob(rank_a: int, rank_b: int, surface: str | None) -> float:
    """P(A gana UN set) real, calibrado por ranking+superficie -- misma
    metodología interna que usa `pre_match_win_prob` (reutiliza sus
    mismos bloques, `p_serve_for_rank`/`_p_win_game`/`_p_win_set`, ver
    docstring de `pre_match_win_prob` sobre por qué esta vía -- no la de
    `instantaneous_match_win_prob` -- es la correcta para esto)."""
    pa_serve = p_serve_for_rank(rank_a, tour="ATP", surface=surface)
    pb_serve = p_serve_for_rank(rank_b, tour="ATP", surface=surface)
    pa_game = _p_win_game(pa_serve)
    pb_game = _p_win_game(pb_serve)
    return _p_win_set(pa_game, pb_game, a_serves_first=True, pa_serve=pa_serve, pb_serve=pb_serve)


def _p_win_match_given_set_score(p_set: float, sets_a: int, sets_b: int, best_of: int) -> float:
    """P(A gana el partido) dado el marcador de sets actual, asumiendo
    sets i.i.d. Bernoulli(p_set) -- análogo a
    `tennis_markov._p_win_match_from_sets`, pero con el p_set REAL
    calibrado por ranking en vez de la aproximación fija de 50/50 que
    usa esa función (documentada ahí mismo como aceptable solo para
    ramas de cola de una simulación EN VIVO, no para una estimación de
    precio real -- mismo razonamiento que el docstring de
    `pre_match_win_prob` sobre por qué NO usar `instantaneous_match_win_prob`
    para esto)."""
    sets_to_win = (best_of // 2) + 1
    if sets_a >= sets_to_win:
        return 1.0
    if sets_b >= sets_to_win:
        return 0.0
    remaining_a = sets_to_win - sets_a
    remaining_b = sets_to_win - sets_b
    total = 0.0
    for k in range(remaining_b):
        total += comb(remaining_a - 1 + k, k) * (p_set ** remaining_a) * ((1 - p_set) ** k)
    return total


# Condición de "alta remontada" encontrada en
# `scripts/favorite_comeback_pattern_analysis.py::find_high_comeback_intersection`
# (2026-08-03, pedido explícito del usuario) -- intersección de 5
# categorías (sin walkover ni ronda, que no aportaban) sobre los 9,772
# casos donde el favorito perdió el set 1: n=69, comeback rate 59.4%
# vs. 29.81% baseline. Umbrales LITERALES de esa corrida real, no
# valores redondeados/generalizados -- ver ese script para el detalle
# completo de cómo se encontraron (incluye advertencia de riesgo de
# sobreajuste ex-post, n chico, sin validar fuera de muestra todavía).
_HIGH_COMEBACK_MAX_FAVORITE_RANK = 20
_HIGH_COMEBACK_MIN_RANK_GAP = 50
_HIGH_COMEBACK_AGE_RANGES = ((0, 23), (26, 29))  # <23 O 26-28 (NO contiguo -- 23-25/29-31 no calificaron)
_HIGH_COMEBACK_BEST_OF = 5
_HIGH_COMEBACK_MAX_SET1_DIFF = 2  # "cerrado": 1-2 games


def _matches_high_comeback_condition(
    favorite_rank: int, rank_gap: int, favorite_age: float | None,
    best_of: int, set1_diff: int | None,
) -> bool:
    """Variante 7 de estrategia (2026-08-03, pedido explícito): "si se
    cumplen [estas condiciones] se deja la apuesta... si no se cumplen,
    se retira". Fail-closed hacia False (retirar, la opción más
    conservadora) si falta cualquier dato -- nunca se asume que la
    condición de alta remontada se cumple sin poder verificarla."""
    if favorite_rank > _HIGH_COMEBACK_MAX_FAVORITE_RANK:
        return False
    if rank_gap < _HIGH_COMEBACK_MIN_RANK_GAP:
        return False
    if favorite_age is None or not any(low <= favorite_age < high for low, high in _HIGH_COMEBACK_AGE_RANGES):
        return False
    if best_of != _HIGH_COMEBACK_BEST_OF:
        return False
    if set1_diff is None or set1_diff > _HIGH_COMEBACK_MAX_SET1_DIFF:
        return False
    return True


def _real_price_legs(
    favorite_rank: int, underdog_rank: int, best_of: int, surface: str | None,
) -> tuple[float, float, float]:
    """3 precios REALES (motor base, point-in-time, sin lookahead) para
    el lado FAVORITO en los 3 momentos que necesitan las 5 estrategias:
    pre-match, tras confirmarse que ganó el set 1 (para las patas de
    refuerzo/entrada-limpia), y tras confirmarse que lo PERDIÓ (para
    revaluar/"cerrar" la posición de la estrategia de retiro a un precio
    real de mercado, en vez de asumir breakeven)."""
    price_pre_match = pre_match_win_prob(favorite_rank, underdog_rank, best_of=best_of, tour="ATP", surface=surface)
    p_set_favorite = _single_set_win_prob(favorite_rank, underdog_rank, surface)
    price_after_set1_win = _p_win_match_given_set_score(p_set_favorite, 1, 0, best_of)
    price_after_set1_loss = _p_win_match_given_set_score(p_set_favorite, 0, 1, best_of)
    return price_pre_match, price_after_set1_win, price_after_set1_loss


def _price_at_1_1(favorite_rank: int, underdog_rank: int, best_of: int, surface: str | None) -> float:
    """Precio REAL (motor base) del favorito estando 1-1 en sets --
    2026-08-03, pedido explícito del usuario para la variante 8: "si el
    favorito pierde el segundo set [tras ganar el primero], se retira
    la segunda apuesta". Necesario para revaluar/cerrar SOLO la pata de
    refuerzo (no la pre-match) a un precio real de mercado en ese
    momento, mismo criterio que `price_after_set1_loss` para la
    estrategia de retiro tras perder el set 1."""
    p_set_favorite = _single_set_win_prob(favorite_rank, underdog_rank, surface)
    return _p_win_match_given_set_score(p_set_favorite, 1, 1, best_of)


def _simulate_algobetting_concurrency_capped(
    cases: list[dict[str, Any]], initial_capital: float = 50.0,
    stake_pct: float = 0.01, max_simultaneous_pct: float = 0.5,
    max_stake_usd: float | None = None,
) -> dict[str, Any]:
    """Simulación pedida explícitamente 2026-08-03: "vamos a usar
    algobetting (para eso es kairos), puedes apostar en todos los
    matches, siempre y cuando no se supere el 50% con apuestas
    simultáneas. usar el 1% del capital total por apuesta." -- versión
    "apostar SIEMPRE al favorito" (estrategia 1) pero con un límite real
    de exposición concurrente, en vez del supuesto (poco realista) de
    que cada apuesta se liquida antes de que empiece la siguiente.

    PRECIO: el mismo motor base (ranking+superficie) que
    `_simulate_capital_paths_priced`, como proxy de precio real de
    mercado -- no el payout 1:1.

    APROXIMACIÓN DE "SIMULTÁNEO" (documentada, TML-Database NO trae hora
    de inicio, solo fecha): se agrupan los partidos por FECHA -- todos
    los partidos de un mismo día se tratan como potencialmente
    concurrentes (no hay forma de saber solapamiento real intra-día sin
    timestamp). Dentro de un día:
    1. Se calcula el stake propuesto (1% del capital al INICIO del día,
       no del capital fluctuante) para cada partido, en el orden en que
       aparecen.
    2. Se acepta la apuesta solo si la exposición acumulada del día
       (suma de stakes ya aceptados) + este stake no supera
       `max_simultaneous_pct` del capital del día -- si lo supera, ESE
       partido puntual se salta (Kairos no tendría margen para tomarlo
       ese día bajo el límite de riesgo).
    3. TODAS las apuestas aceptadas ese día se liquidan juntas al cierre
       del día (mismo supuesto de granularidad diaria) -- el capital
       recién se actualiza al pasar al día siguiente.

    Esto es más realista que asumir liquidación instantánea entre
    partidos (que permitía compounding partido-a-partido sin límite de
    riesgo, la causa de los resultados astronómicos de
    `_simulate_capital_paths_priced`), pero sigue siendo una
    APROXIMACIÓN por la granularidad de fecha (no hora) del dataset.

    `max_stake_usd` (2026-08-03, pedido explícito del usuario tras
    observar que los mercados reales de partidos de tenis en Kalshi
    mueven ~$2-4M de volumen TOTAL de vida del mercado): techo de
    liquidez real por posición individual -- si `1% del capital` supera
    este valor, el stake se recorta a `max_stake_usd` en vez de escalar
    libremente con el capital. Esto es lo que rompe la explosión
    exponencial pura: una vez que el capital crece lo suficiente, el
    tamaño de la apuesta deja de crecer con él y el crecimiento pasa a
    ser aproximadamente LINEAL en vez de exponencial. `None` (default)
    = sin techo, comportamiento idéntico a antes de este parámetro.
    IMPORTANTE: el volumen TOTAL de vida de un mercado no es lo mismo
    que la profundidad instantánea disponible sin mover el precio --
    `max_stake_usd` debe fijarse como una FRACCIÓN conservadora de ese
    volumen total (no el volumen total completo), juicio explícito del
    usuario/arquitecta, no un dato medido de profundidad real."""
    priceable = []
    skipped = 0
    for c in cases:
        best_of = c.get("best_of") or 3
        if best_of not in (3, 5):
            skipped += 1
            continue
        price_pre = pre_match_win_prob(
            c["favorite_rank"], c["underdog_rank"], best_of=best_of, tour="ATP", surface=c.get("surface"),
        )
        priceable.append({**c, "price_pre_match": price_pre})

    by_date: dict[str, list[dict[str, Any]]] = {}
    for c in priceable:
        by_date.setdefault(c["date"], []).append(c)

    capital = initial_capital
    busted = False
    trade_returns: list[float] = []
    hits = 0
    equity_curve = [initial_capital]
    skipped_concurrency_cap = 0

    for date in sorted(by_date.keys()):
        if busted:
            break
        day_matches = by_date[date]
        capital_at_day_start = capital
        max_exposure = capital_at_day_start * max_simultaneous_pct
        running_exposure = 0.0
        accepted: list[tuple[float, float, bool]] = []  # (stake, price, hit)

        for c in day_matches:
            stake = capital_at_day_start * stake_pct
            if max_stake_usd is not None:
                stake = min(stake, max_stake_usd)
            if running_exposure + stake > max_exposure:
                skipped_concurrency_cap += 1
                continue
            running_exposure += stake
            accepted.append((stake, c["price_pre_match"], c["hit"]))

        day_pnl = 0.0
        for stake, price, hit in accepted:
            pct = (1 - price) / price if hit else -1.0
            day_pnl += stake * pct
            trade_returns.append(pct)
            if hit:
                hits += 1

        capital += day_pnl
        if capital <= 0:
            capital = 0.0
            busted = True
        equity_curve.append(capital)

    n_trades = len(trade_returns)
    avg_roi = statistics.mean(trade_returns) if trade_returns else None
    sharpe = None
    if n_trades >= 2:
        stdev = statistics.stdev(trade_returns)
        if stdev > 0:
            sharpe = round(avg_roi / stdev, 4)

    return {
        "initial_capital_usdc": initial_capital,
        "final_capital_usdc": round(capital, 2),
        "roi_total_pct": round((capital / initial_capital - 1) * 100, 2),
        "n_trades": n_trades,
        "n_skipped_concurrency_cap": skipped_concurrency_cap,
        "n_skipped_invalid_best_of": skipped,
        "winrate": round(hits / n_trades, 4) if n_trades else None,
        "avg_roi_per_trade_pct": round(avg_roi * 100, 4) if avg_roi is not None else None,
        "max_drawdown_pct": _max_drawdown_pct(equity_curve),
        "sharpe_ratio_per_trade": sharpe,
        "busted": busted,
        "stake_pct_used": stake_pct, "max_simultaneous_pct_used": max_simultaneous_pct,
        "max_stake_usd_used": max_stake_usd,
        "caveat": (
            "Precio real del motor base (proxy de mercado), 'simultáneo' "
            "aproximado por FECHA (TML-Database no trae hora de inicio) "
            "-- todos los partidos del mismo día compiten por el mismo "
            "cupo de exposición del 50%, se liquidan juntos al cierre "
            "del día. Partidos que no entraron por el cupo quedan en "
            "`n_skipped_concurrency_cap`, no cuentan como operación."
        ),
    }


def _yearly_edge_degradation(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Pedido explícito del usuario 2026-08-03: "agreguemos el estudio
    de la degradación promedio" -- ¿el sub-pricing real que encontramos
    en `model_fair_price_calibration` (winrate real por encima del
    precio del motor base) se mantiene parejo año a año, o se degrada/
    desaparece en los años más recientes? Si el edge fuera producto de
    sobreajuste o de una ventana de mercado que ya se corrigió, debería
    verse una tendencia decreciente acá -- si es un sesgo estructural
    real del ranking como proxy, debería mantenerse relativamente
    estable.

    Reporta, por año: n, winrate real, fair_a promedio del motor base,
    edge en puntos porcentuales (winrate - fair_a), y el ROI/operación
    equivalente bajo economía de precio real (misma fórmula que
    `_simulate_capital_paths_priced`: (1-P)/P si gana, -100% si pierde)."""
    by_year: dict[str, dict[str, Any]] = {}
    skipped = 0
    for c in cases:
        best_of = c.get("best_of") or 3
        if best_of not in (3, 5):
            skipped += 1
            continue
        year = c["date"][:4]
        price = pre_match_win_prob(
            c["favorite_rank"], c["underdog_rank"], best_of=best_of, tour="ATP", surface=c.get("surface"),
        )
        hit = c["hit"]
        bucket = by_year.setdefault(year, {"n": 0, "hits": 0, "fair_sum": 0.0, "roi_returns": []})
        bucket["n"] += 1
        bucket["hits"] += 1 if hit else 0
        bucket["fair_sum"] += price
        bucket["roi_returns"].append(((1 - price) / price) if hit else -1.0)

    result = {}
    for year, b in sorted(by_year.items()):
        n = b["n"]
        winrate = round(b["hits"] / n, 4) if n else None
        avg_fair_a = round(b["fair_sum"] / n, 4) if n else None
        avg_roi = statistics.mean(b["roi_returns"]) if b["roi_returns"] else None
        result[year] = {
            "n": n, "winrate": winrate, "avg_model_fair_a": avg_fair_a,
            "edge_pp": round((winrate - avg_fair_a) * 100, 2) if (winrate is not None and avg_fair_a is not None) else None,
            "avg_roi_per_trade_pct": round(avg_roi * 100, 4) if avg_roi is not None else None,
        }
    return {
        "by_year": result, "skipped_invalid_best_of": skipped,
        "caveat": (
            "edge_pp = winrate real - fair_a promedio del motor base, en "
            "puntos porcentuales -- si cae hacia 0 o se vuelve negativo en "
            "los años recientes, el sub-pricing histórico se está "
            "corrigiendo/desapareciendo (mercado más eficiente con el "
            "tiempo); si se mantiene estable, es un sesgo estructural del "
            "modelo de ranking, no una ventana de oportunidad que ya se "
            "cerró."
        ),
    }


_ALL_STRATEGY_KEYS = [
    "1_simple_bet_favorite", "2_bet_only_after_favorite_wins_set1",
    "3_double_down_if_favorite_wins_set1", "4_exit_if_favorite_loses_set1",
    "5_combined_double_down_and_exit", "6_combined_with_hedge",
    "7_hold_if_high_comeback_condition_else_exit",
    "8_exit_double_down_leg_if_favorite_loses_set2",
]


def _simulate_realistic_all_strategies(
    cases: list[dict[str, Any]], initial_capital: float = 50.0,
    stake_pct: float = 0.01, max_simultaneous_pct: float = 0.5,
    max_stake_usd: float | None = None, hedge_stake_pct: float | None = None,
) -> dict[str, Any]:
    """Pedido explícito del usuario 2026-08-03: "saquemos estos
    resultados con todas las variantes de la estrategia" -- generaliza
    `_simulate_algobetting_concurrency_capped` (que solo cubría la
    estrategia 1) a las 5 estrategias de `strategy_comparison`, con el
    mismo cupo de exposición simultánea diaria (50% por defecto) y el
    mismo techo de liquidez por apuesta (`max_stake_usd`, ver esa
    función para el razonamiento completo de ambos supuestos).

    Simplificación deliberada sobre `_simulate_capital_paths_priced`:
    acá TODAS las patas (pre-match y refuerzo post-set1) usan el MISMO
    `stake_pct` (1% parejo, pedido explícito del usuario esta ronda),
    no el esquema 5%/1% de versiones anteriores -- coherente con "usar
    el 1% del capital total por apuesta" del pedido original de esta
    simulación.

    Granularidad diaria (mismo supuesto que la función base): el
    refuerzo post-set1 compite por el MISMO cupo de exposición del día
    que la pata pre-match (no hay forma de saber si ocurren en
    instantes distintos del día sin timestamp real). El retiro
    (estrategias 4/5 cuando el favorito pierde el set 1) NO consume
    cupo nuevo -- es un reprecio de la posición YA aceptada, no una
    apuesta adicional.

    ESTRATEGIA 6 (variante "hedge", pedida explícita 2026-08-03 --
    "efectivamente el limit arb buscaba lograr coberturas... variante
    6 = 5 + la apuesta hedge"): igual que la 5, pero cuando el favorito
    PIERDE el set 1, ADEMÁS del reprecio de la posición pre-match, se
    compra una pata de cobertura real en el underdog al precio
    complementario implícito del modelo (`1 - price_after_set1_loss`),
    neta de fees reales de Kalshi (`outer_apis/kalshi/fees.py::
    leg_fee_fraction`). Mismo espíritu que `limit_arb` (2 patas para
    acotar riesgo direccional) pero SIN precio real de mercado en
    ambos lados (TML-Database no lo tiene) -- por eso esto NO es un
    arbitraje bloqueado/garantizado como el de producción, es una
    cobertura APROXIMADA basada en el propio modelo, que reduce
    varianza pero no elimina el riesgo direccional. Un arb real
    requeriría datos de precio en vivo de ambos lados (ver
    `workers/market_arb_websocket_scanner.py`), no es backtesteable
    con este dataset histórico.

    `hedge_stake_pct` (2026-08-03, pedido explícito tras encontrar que
    el hedge al 1% parejo rendía PEOR que la estrategia 5 sin cobertura
    -- el motor ya está mejor calibrado en el tramo "favorito abajo
    0-1" que en el tramo pre-match, así que ahí casi no queda edge para
    cubrir los fees de Kalshi): tamaño de la pata de cobertura como %
    del capital, INDEPENDIENTE de `stake_pct` (que sigue rigiendo las
    patas pre-match/refuerzo). `None` (default) = usa `stake_pct` para
    todo, comportamiento idéntico a antes de este parámetro.

    ESTRATEGIA 7 (2026-08-03, pedido explícito -- reemplaza el retiro
    incondicional de la 5 por uno CONDICIONAL, ver corrección de ese
    mismo día: "¿se incluyó el doblez?" -- por consistencia con la 6,
    que es "5 + cobertura" y mantiene el refuerzo de la 5, la 7 es
    "5 + retiro condicional", TAMBIÉN mantiene el refuerzo si el
    favorito gana el set 1): igual que la 5 (pre-match + refuerzo si
    gana el set 1), pero el retiro al PERDER el set 1 solo se ejecuta
    si el partido NO cumple el patrón de alta remontada encontrado en
    `favorite_comeback_pattern_analysis.py::find_high_comeback_intersection`
    (favorito top-20, brecha de ranking >=50, edad en {<23, 26-28},
    best-of-5, set1 perdido por 1-2 games -- ver
    `_matches_high_comeback_condition`). Si el patrón SÍ se cumple, se
    sostiene la posición pre-match sin cobertura adicional (el refuerzo
    solo aplica cuando GANA el set 1, no cuando lo pierde -- eso sería
    doblar una posición perdedora, no lo pedido).
    ADVERTENCIA: el patrón se encontró y se mide sobre el MISMO dataset
    (n=69 casos que cumplen la condición completa, sobre 10 años) --
    riesgo real de sobreajuste ex-post, no validado fuera de muestra.

    ESTRATEGIA 8 (2026-08-03, pedido explícito -- usuario evaluó la 5
    como la mejor por "controlar el riesgo cuando aumenta la
    incertidumbre" y pidió agregar una regla más sobre el SEGUNDO set):
    igual que la 5 (pre-match + refuerzo si gana el set 1), pero si el
    favorito gana el set 1 (refuerzo colocado) y LUEGO pierde el set 2
    (partido 1-1), se retira SOLO la pata de refuerzo -- revaluada a
    `price_at_1_1` (precio real del motor en el estado 1-1, no
    breakeven) -- porque es la que más pérdida acumularía si el
    favorito termina perdiendo el partido. La pata pre-match NO se
    toca, sigue sostenida hasta el resultado final igual que en la 5."""
    priceable = []
    skipped = 0
    for c in cases:
        best_of = c.get("best_of") or 3
        if best_of not in (3, 5):
            skipped += 1
            continue
        price_pre, price_win, price_loss = _real_price_legs(
            c["favorite_rank"], c["underdog_rank"], best_of, c.get("surface"),
        )
        price_1_1 = _price_at_1_1(c["favorite_rank"], c["underdog_rank"], best_of, c.get("surface"))
        priceable.append({
            **c, "price_pre_match": price_pre, "price_after_set1_win": price_win,
            "price_after_set1_loss": price_loss, "price_at_1_1": price_1_1,
        })

    by_date: dict[str, list[dict[str, Any]]] = {}
    for c in priceable:
        by_date.setdefault(c["date"], []).append(c)

    capital = {k: initial_capital for k in _ALL_STRATEGY_KEYS}
    busted = {k: False for k in _ALL_STRATEGY_KEYS}
    trade_returns: dict[str, list[float]] = {k: [] for k in _ALL_STRATEGY_KEYS}
    resolve_hits: dict[str, int] = {k: 0 for k in _ALL_STRATEGY_KEYS}
    resolve_n: dict[str, int] = {k: 0 for k in _ALL_STRATEGY_KEYS}
    equity_curves: dict[str, list[float]] = {k: [initial_capital] for k in _ALL_STRATEGY_KEYS}
    skipped_cap: dict[str, int] = {k: 0 for k in _ALL_STRATEGY_KEYS}

    _hedge_pct = hedge_stake_pct if hedge_stake_pct is not None else stake_pct

    def _sized_stake(cap_start: float, pct: float = stake_pct) -> float:
        stake = cap_start * pct
        return min(stake, max_stake_usd) if max_stake_usd is not None else stake

    # 2026-08-03, pedido explícito del usuario tras encontrar el gap:
    # `leg_fee_fraction` (fees REALES de Kalshi) solo se estaba aplicando
    # en la pata de cobertura de la variante 6 (ya descartada) -- las
    # estrategias 1-5, 7 y 8, incluida la elegida como mejor (8), NUNCA
    # restaban fee. Con precio típico ~55-65%, la fee ronda 2.4%-3.2%
    # del stake POR ENTRADA -- contra un ROI/op de ~11%, esto se come
    # una fracción real y no despreciable del edge. Aplicado acá a TODAS
    # las estrategias, no solo a la 6.
    def _entry_fee_frac(price: float) -> float:
        """Fee de Kalshi (taker, sin descuento de maker -- ver
        `outer_apis/kalshi/fees.py`) como fracción del stake, para
        comprar contratos a `price` -- fee_per_contract/price, porque
        1 contrato cuesta `price` pero paga fee sobre el payout de $1."""
        fee_per_contract = leg_fee_fraction(price) or 0.0
        return fee_per_contract / price if price > 0 else 0.0

    def _net_hold_pct(price: float, hit: bool) -> float:
        """Retorno NETO de fee de una posición comprada a `price` y
        SOSTENIDA hasta el resultado final (fee solo en la entrada, el
        settlement automático a $1/$0 no genera una orden nueva)."""
        raw = (1 - price) / price if hit else -1.0
        return raw - _entry_fee_frac(price)

    def _net_reprice_pct(entry_price: float, exit_price: float) -> float:
        """Retorno NETO de fee de CERRAR una posición antes del
        resultado final (reprecio) -- se cobra fee en la entrada Y en
        la salida (2 órdenes reales, criterio conservador -- nunca
        subestimar el costo, mismo espíritu que `outer_apis/kalshi/fees.py`)."""
        raw = (exit_price / entry_price) - 1.0
        return raw - _entry_fee_frac(entry_price) - _entry_fee_frac(exit_price)

    for date in sorted(by_date.keys()):
        day_matches = by_date[date]

        for key in _ALL_STRATEGY_KEYS:
            if busted[key]:
                continue
            cap_start = capital[key]
            max_exposure = cap_start * max_simultaneous_pct
            running_exposure = 0.0
            day_pnl = 0.0
            day_returns: list[float] = []
            day_hits = day_resolves = 0

            for c in day_matches:
                hit, s1 = c["hit"], c["set1_favorite_win"]
                p_pre, p_win, p_loss = c["price_pre_match"], c["price_after_set1_win"], c["price_after_set1_loss"]

                if key == "1_simple_bet_favorite":
                    stake = _sized_stake(cap_start)
                    if running_exposure + stake > max_exposure:
                        skipped_cap[key] += 1
                        continue
                    running_exposure += stake
                    pct = _net_hold_pct(p_pre, hit)
                    day_pnl += stake * pct
                    day_returns.append(pct)
                    day_resolves += 1
                    day_hits += 1 if hit else 0

                elif key == "2_bet_only_after_favorite_wins_set1":
                    if s1 is not True:
                        continue
                    stake = _sized_stake(cap_start)
                    if running_exposure + stake > max_exposure:
                        skipped_cap[key] += 1
                        continue
                    running_exposure += stake
                    pct = (1 - p_win) / p_win if hit else -1.0
                    day_pnl += stake * pct
                    day_returns.append(pct)
                    day_resolves += 1
                    day_hits += 1 if hit else 0

                elif key == "3_double_down_if_favorite_wins_set1":
                    stake = _sized_stake(cap_start)
                    if running_exposure + stake > max_exposure:
                        skipped_cap[key] += 1
                        continue
                    running_exposure += stake
                    pct = _net_hold_pct(p_pre, hit)
                    day_pnl += stake * pct
                    day_returns.append(pct)
                    day_resolves += 1
                    day_hits += 1 if hit else 0
                    if s1 is True:
                        add_stake = _sized_stake(cap_start)
                        if running_exposure + add_stake <= max_exposure:
                            running_exposure += add_stake
                            pct2 = _net_hold_pct(p_win, hit)
                            day_pnl += add_stake * pct2
                            day_returns.append(pct2)
                            day_resolves += 1
                            day_hits += 1 if hit else 0
                        else:
                            skipped_cap[key] += 1

                elif key == "4_exit_if_favorite_loses_set1":
                    stake = _sized_stake(cap_start)
                    if running_exposure + stake > max_exposure:
                        skipped_cap[key] += 1
                        continue
                    running_exposure += stake
                    if s1 is False:
                        pct = _net_reprice_pct(p_pre, p_loss)
                        day_pnl += stake * pct
                        day_returns.append(pct)  # reprecio, no cuenta para winrate
                    else:
                        pct = _net_hold_pct(p_pre, hit)
                        day_pnl += stake * pct
                        day_returns.append(pct)
                        day_resolves += 1
                        day_hits += 1 if hit else 0

                elif key == "5_combined_double_down_and_exit":
                    stake = _sized_stake(cap_start)
                    if running_exposure + stake > max_exposure:
                        skipped_cap[key] += 1
                        continue
                    running_exposure += stake
                    if s1 is False:
                        pct = _net_reprice_pct(p_pre, p_loss)
                        day_pnl += stake * pct
                        day_returns.append(pct)
                    else:
                        pct = _net_hold_pct(p_pre, hit)
                        day_pnl += stake * pct
                        day_returns.append(pct)
                        day_resolves += 1
                        day_hits += 1 if hit else 0
                        if s1 is True:
                            add_stake = _sized_stake(cap_start)
                            if running_exposure + add_stake <= max_exposure:
                                running_exposure += add_stake
                                pct2 = _net_hold_pct(p_win, hit)
                                day_pnl += add_stake * pct2
                                day_returns.append(pct2)
                                day_resolves += 1
                                day_hits += 1 if hit else 0
                            else:
                                skipped_cap[key] += 1

                elif key == "6_combined_with_hedge":  # variante 6, pedida
                    # explícitamente 2026-08-03: igual que la 5, pero
                    # cuando el favorito PIERDE el set 1, en vez de solo
                    # cerrar/revaluar la posición pre-match, se agrega
                    # una pata de COBERTURA real comprando el underdog al
                    # precio complementario implícito del modelo
                    # (1 - price_after_set1_loss), neta de fees reales de
                    # Kalshi (`leg_fee_fraction`) -- mismo espíritu que
                    # limit_arb (2 patas para acotar riesgo), pero sin
                    # precio real de mercado (no disponible en
                    # TML-Database) -- por eso NO es un arb garantizado,
                    # solo reduce varianza vs. la 5.
                    stake = _sized_stake(cap_start)
                    if running_exposure + stake > max_exposure:
                        skipped_cap[key] += 1
                        continue
                    running_exposure += stake
                    if s1 is False:
                        pct = _net_reprice_pct(p_pre, p_loss)
                        day_pnl += stake * pct
                        day_returns.append(pct)

                        hedge_stake = _sized_stake(cap_start, _hedge_pct)
                        hedge_price = 1.0 - p_loss
                        if running_exposure + hedge_stake <= max_exposure:
                            running_exposure += hedge_stake
                            fee_per_contract = leg_fee_fraction(hedge_price) or 0.0
                            contracts = hedge_stake / hedge_price
                            fee_total = contracts * fee_per_contract
                            hedge_pct = (1 - hedge_price) / hedge_price if not hit else -1.0
                            hedge_pnl = hedge_stake * hedge_pct - fee_total
                            day_pnl += hedge_pnl
                            day_returns.append(hedge_pnl / hedge_stake)
                        else:
                            skipped_cap[key] += 1
                    else:
                        pct = _net_hold_pct(p_pre, hit)
                        day_pnl += stake * pct
                        day_returns.append(pct)
                        day_resolves += 1
                        day_hits += 1 if hit else 0
                        if s1 is True:
                            add_stake = _sized_stake(cap_start)
                            if running_exposure + add_stake <= max_exposure:
                                running_exposure += add_stake
                                pct2 = _net_hold_pct(p_win, hit)
                                day_pnl += add_stake * pct2
                                day_returns.append(pct2)
                                day_resolves += 1
                                day_hits += 1 if hit else 0
                            else:
                                skipped_cap[key] += 1

                elif key == "7_hold_if_high_comeback_condition_else_exit":  # --
                    # variante 7, pedida explícita 2026-08-03: "si se
                    # cumplen [las condiciones de alta remontada], se
                    # deja la apuesta... no se retira y no se coloca
                    # cobertura. Si no se cumplen, se retira la
                    # apuesta" -- CORREGIDO 2026-08-03 tras pregunta
                    # explícita del usuario ("¿se incluyó el doblez?"):
                    # por consistencia con la 6 (que es "5 + cobertura",
                    # mantiene el refuerzo de la 5), la 7 es "5 + retiro
                    # CONDICIONAL" (mantiene el refuerzo de la 5),
                    # NO "4 + retiro condicional" (que no dobla) --
                    # ver `_matches_high_comeback_condition` para el
                    # patrón que decide sostener vs. retirar.
                    stake = _sized_stake(cap_start)
                    if running_exposure + stake > max_exposure:
                        skipped_cap[key] += 1
                        continue
                    running_exposure += stake
                    if s1 is False and not _matches_high_comeback_condition(
                        c["favorite_rank"], c["rank_gap"], c.get("favorite_age"),
                        c.get("best_of") or 3, c.get("set1_diff"),
                    ):
                        pct = _net_reprice_pct(p_pre, p_loss)
                        day_pnl += stake * pct
                        day_returns.append(pct)
                    else:
                        # s1 True/desconocido, O s1 False pero la condición
                        # de alta remontada SÍ se cumple -> sostiene.
                        pct = _net_hold_pct(p_pre, hit)
                        day_pnl += stake * pct
                        day_returns.append(pct)
                        day_resolves += 1
                        day_hits += 1 if hit else 0
                        if s1 is True:
                            add_stake = _sized_stake(cap_start)
                            if running_exposure + add_stake <= max_exposure:
                                running_exposure += add_stake
                                pct2 = _net_hold_pct(p_win, hit)
                                day_pnl += add_stake * pct2
                                day_returns.append(pct2)
                                day_resolves += 1
                                day_hits += 1 if hit else 0
                            else:
                                skipped_cap[key] += 1

                else:  # "8_exit_double_down_leg_if_favorite_loses_set2"
                    # -- variante 8, pedida explícita 2026-08-03: "la 5
                    # es la mejor porque controla el riesgo... si el
                    # favorito pierde el segundo set, se retira la
                    # SEGUNDA apuesta (la que dobla), ya que es la que
                    # más pérdidas tendría si el favorito llegase a
                    # perder". Igual que la 5, pero la pata de refuerzo
                    # (NO la pre-match, que sigue sosteniéndose) se
                    # cierra a `price_at_1_1` si el favorito pierde el
                    # set 2 tras haber ganado el 1 -- mismo criterio de
                    # reprecio real que usa el retiro de la 4/5.
                    p_1_1 = c.get("price_at_1_1")
                    s2 = c.get("set2_favorite_win")

                    stake = _sized_stake(cap_start)
                    if running_exposure + stake > max_exposure:
                        skipped_cap[key] += 1
                        continue
                    running_exposure += stake
                    if s1 is False:
                        pct = _net_reprice_pct(p_pre, p_loss)
                        day_pnl += stake * pct
                        day_returns.append(pct)
                    else:
                        pct = _net_hold_pct(p_pre, hit)
                        day_pnl += stake * pct
                        day_returns.append(pct)
                        day_resolves += 1
                        day_hits += 1 if hit else 0
                        if s1 is True:
                            add_stake = _sized_stake(cap_start)
                            if running_exposure + add_stake <= max_exposure:
                                running_exposure += add_stake
                                if s2 is False and p_1_1 is not None:
                                    # se retira la pata de REFUERZO
                                    # (reprecio, no resuelve por hit).
                                    pct2 = (p_1_1 / p_win) - 1.0
                                    day_pnl += add_stake * pct2
                                    day_returns.append(pct2)
                                else:
                                    # s2 True/desconocido -> sostiene
                                    # hasta el final, igual que la 5.
                                    pct2 = _net_hold_pct(p_win, hit)
                                    day_pnl += add_stake * pct2
                                    day_returns.append(pct2)
                                    day_resolves += 1
                                    day_hits += 1 if hit else 0
                            else:
                                skipped_cap[key] += 1

            capital[key] += day_pnl
            trade_returns[key].extend(day_returns)
            resolve_hits[key] += day_hits
            resolve_n[key] += day_resolves
            if capital[key] <= 0:
                capital[key] = 0.0
                busted[key] = True
            equity_curves[key].append(capital[key])

    result = {}
    for key in _ALL_STRATEGY_KEYS:
        cap = capital[key]
        returns = trade_returns[key]
        n_trades = len(returns)
        avg_roi = statistics.mean(returns) if returns else None
        sharpe = None
        if n_trades >= 2:
            stdev = statistics.stdev(returns)
            if stdev > 0:
                sharpe = round(avg_roi / stdev, 4)
        result[key] = {
            "initial_capital_usdc": initial_capital,
            "final_capital_usdc": round(cap, 2),
            "roi_total_pct": round((cap / initial_capital - 1) * 100, 2),
            "n_trades": n_trades,
            "n_skipped_concurrency_cap": skipped_cap[key],
            "winrate": round(resolve_hits[key] / resolve_n[key], 4) if resolve_n[key] else None,
            "avg_roi_per_trade_pct": round(avg_roi * 100, 4) if avg_roi is not None else None,
            "max_drawdown_pct": _max_drawdown_pct(equity_curves[key]),
            "sharpe_ratio_per_trade": sharpe,
            "busted": busted[key],
        }
    return {
        "strategies": result, "skipped_invalid_best_of": skipped,
        "stake_pct_used": stake_pct, "max_simultaneous_pct_used": max_simultaneous_pct,
        "max_stake_usd_used": max_stake_usd, "hedge_stake_pct_used": _hedge_pct,
        "caveat": (
            "Mismos supuestos que `_simulate_algobetting_concurrency_capped` "
            "(precio real del motor base, 'simultáneo' aproximado por "
            "fecha, techo de liquidez por apuesta) extendidos a las 5 "
            "estrategias. El reprecio de retiro (4/5) no cuenta para "
            "`winrate` (no es un resultado ganado/perdido, es una "
            "revaluación de posición)."
        ),
    }


def _simulate_capital_paths_priced(
    cases: list[dict[str, Any]], initial_capital: float = 50.0,
    pre_match_stake_pct: float = _PRE_MATCH_STAKE_PCT, set1_add_stake_pct: float = _SET1_ADD_STAKE_PCT,
) -> dict[str, Any]:
    """Versión "más real" de `_simulate_capital_paths`, pedida
    explícitamente 2026-08-03: en vez de asumir payout 1:1 (neutral pero
    irreal), usa el precio que el motor BASE de `TennisFModel`
    (ranking+superficie, el mismo que `_model_fair_price_calibration`)
    le habría puesto al favorito -- razonamiento del usuario: las casas
    de apuestas/participantes de mercados de predicción en general usan
    modelos simples o intuición, así que esto es lo más cercano a la
    probabilidad pre-match REAL que esos partidos históricos tuvieron
    (el edge real vendría de la desviación entre esto y la opinión de la
    gente/bots, que es justo lo que mide `model_fair_price_calibration`).

    ECONOMÍA de una apuesta binaria a precio P: comprar el favorito a P
    paga $1 si gana -> retorno sobre el stake = (1-P)/P si gana, -100%
    del stake si pierde (se pierde todo, no una fracción -- estándar en
    mercados de predicción, distinto del 1:1 de la versión anterior).

    MEJORA real sobre la versión 1:1 para las estrategias de RETIRO (4 y
    5): en vez de asumir breakeven al retirarse (sin poder valuar la
    salida), acá SÍ hay un precio real de reventa -- `price_after_set1_loss`
    es la probabilidad que el propio motor le asigna al favorito de
    igual ganar el partido estando abajo 0-1, un "mark-to-model" legítimo
    de cuánto valdría la posición en ese momento. La pata pre-match se
    revalúa de `price_pre_match` a `price_after_set1_loss` y esa
    diferencia relativa ES la pérdida/ganancia real de cerrar ahí.

    Fail-closed: partidos con `best_of` inválido no participan (mismo
    criterio que `_model_fair_price_calibration`)."""
    priceable = []
    skipped = 0
    for c in cases:
        best_of = c.get("best_of") or 3
        if best_of not in (3, 5):
            skipped += 1
            continue
        price_pre, price_win, price_loss = _real_price_legs(
            c["favorite_rank"], c["underdog_rank"], best_of, c.get("surface"),
        )
        priceable.append({**c, "price_pre_match": price_pre, "price_after_set1_win": price_win, "price_after_set1_loss": price_loss})

    sorted_cases = sorted(priceable, key=lambda c: c["date"])

    keys = [
        "1_simple_bet_favorite", "2_bet_only_after_favorite_wins_set1",
        "3_double_down_if_favorite_wins_set1", "4_exit_if_favorite_loses_set1",
        "5_combined_double_down_and_exit",
    ]
    capital = {k: initial_capital for k in keys}
    busted = {k: False for k in keys}
    trade_returns: dict[str, list[float]] = {k: [] for k in keys}
    equity_curves: dict[str, list[float]] = {k: [initial_capital] for k in keys}

    def _resolve(key: str, stake: float, price: float, hit: bool) -> None:
        """Liquida una posición COMPRADA a `price` y resuelta por `hit`."""
        if busted[key] or stake <= 0:
            return
        before = capital[key]
        pct = (1 - price) / price if hit else -1.0
        capital[key] += stake * pct
        trade_returns[key].append(pct)
        if capital[key] <= 0:
            capital[key] = 0.0
            busted[key] = True

    def _reprice_exit(key: str, stake: float, entry_price: float, exit_price: float) -> None:
        """Cierra una posición sin esperar el resultado final -- revalúa
        de `entry_price` a `exit_price` (mark-to-model), no un
        supuesto de breakeven."""
        if busted[key] or stake <= 0:
            return
        before = capital[key]
        pct = (exit_price / entry_price) - 1.0
        capital[key] += stake * pct
        trade_returns[key].append(pct)
        if capital[key] <= 0:
            capital[key] = 0.0
            busted[key] = True

    for c in sorted_cases:
        hit, s1 = c["hit"], c["set1_favorite_win"]
        p_pre, p_win, p_loss = c["price_pre_match"], c["price_after_set1_win"], c["price_after_set1_loss"]

        _resolve("1_simple_bet_favorite", capital["1_simple_bet_favorite"] * pre_match_stake_pct, p_pre, hit)

        if s1 is True:
            _resolve(
                "2_bet_only_after_favorite_wins_set1",
                capital["2_bet_only_after_favorite_wins_set1"] * set1_add_stake_pct, p_win, hit,
            )

        cap3 = capital["3_double_down_if_favorite_wins_set1"]
        pre_stake3 = cap3 * pre_match_stake_pct
        _resolve("3_double_down_if_favorite_wins_set1", pre_stake3, p_pre, hit)
        if s1 is True:
            add_stake3 = cap3 * set1_add_stake_pct
            _resolve("3_double_down_if_favorite_wins_set1", add_stake3, p_win, hit)

        pre_stake4 = capital["4_exit_if_favorite_loses_set1"] * pre_match_stake_pct
        if s1 is False:
            _reprice_exit("4_exit_if_favorite_loses_set1", pre_stake4, p_pre, p_loss)
        else:  # True o desconocido -> sostiene hasta el final (supuesto fail-closed, igual que la v1:1)
            _resolve("4_exit_if_favorite_loses_set1", pre_stake4, p_pre, hit)

        cap5 = capital["5_combined_double_down_and_exit"]
        pre_stake5 = cap5 * pre_match_stake_pct
        if s1 is False:
            _reprice_exit("5_combined_double_down_and_exit", pre_stake5, p_pre, p_loss)
        else:
            _resolve("5_combined_double_down_and_exit", pre_stake5, p_pre, hit)
            if s1 is True:
                add_stake5 = cap5 * set1_add_stake_pct
                _resolve("5_combined_double_down_and_exit", add_stake5, p_win, hit)

        for k in keys:
            equity_curves[k].append(capital[k])

    result = {}
    for k in keys:
        cap = capital[k]
        overflowed = cap in (float("inf"), float("-inf"))
        returns = trade_returns[k]
        n_trades = len(returns)
        avg_roi = statistics.mean(returns) if returns else None
        sharpe = None
        if n_trades >= 2:
            stdev = statistics.stdev(returns)
            if stdev > 0:
                sharpe = round(avg_roi / stdev, 4)
        result[k] = {
            "initial_capital_usdc": initial_capital,
            "final_capital_usdc": None if overflowed else round(cap, 2),
            "return_pct": None if overflowed else round((cap / initial_capital - 1) * 100, 2),
            "busted": busted[k],
            "overflowed": overflowed,
            "n_trades": n_trades,
            "avg_roi_per_trade_pct": round(avg_roi * 100, 4) if avg_roi is not None else None,
            "max_drawdown_pct": _max_drawdown_pct(equity_curves[k]),
            "sharpe_ratio_per_trade": sharpe,
        }
    return {
        "strategies": result, "skipped_invalid_best_of": skipped,
        "caveat": (
            "Precios REALES del motor base (ranking+superficie), usado "
            "como proxy de lo que un mercado/casa con modelo simple "
            "hubiera cotizado -- el edge capturado acá es la desviación "
            "REAL entre ese precio y el resultado histórico observado "
            "(ver `model_fair_price_calibration`: el motor base "
            "sub-estima consistentemente, por eso estas estrategias dan "
            "positivo). Sigue siendo un proxy, no el precio real que "
            "Kalshi/una casa hubiera cotizado ese día -- pero es más "
            "realista que el payout 1:1 de `capital_simulation`."
        ),
    }


def _simulate_capital_paths(
    cases: list[dict[str, Any]], initial_capital: float = 50.0,
    pre_match_stake_pct: float = _PRE_MATCH_STAKE_PCT, set1_add_stake_pct: float = _SET1_ADD_STAKE_PCT,
) -> dict[str, Any]:
    """Simula la trayectoria de capital de las 5 estrategias, en orden
    CRONOLÓGICO real de los partidos -- pedido explícito del usuario
    2026-08-03: "hay que simular el camino del capital... 50 USDC".

    SUPUESTOS que hay que tener en cuenta al leer los resultados
    (documentados acá para no fabricar precisión falsa):

    1. PAYOUT 1:1 (odds parejas, "even money"). TML-Database no tiene
       precio de mercado real -- a diferencia de `favorite_strategy_backtest.py`
       (que sí usa precio real de Kalshi pero solo ~2 meses de retención),
       acá NO hay forma de saber a qué precio real se hubiera podido
       entrar en cada partido histórico. Asumir 1:1 es neutral (no
       fabrica una ventaja de precio que no está probada) -- el capital
       resultante ilustra la DINÁMICA de compounding entre estrategias
       (drawdown, efecto del refuerzo/doble-down), NO un P&L real en
       dólares. El P&L real depende del precio de Kalshi al momento de
       cada apuesta.
    2. Sizing como % del capital ACTUAL (compounding), reutilizando los
       tamaños ya decididos por el usuario (ver arriba), no un Kelly
       calculado.
    3. Retiro (estrategias 4 y 5, cuando el favorito pierde el set 1) se
       asume a BREAKEVEN (ni gana ni pierde el stake) -- no hay precio
       real de salida para valuar la pérdida/ganancia parcial de
       cerrar la posición a mitad de partido.
    4. Sin dato de set 1 (score no parseable): fallback a sostener la
       posición pre-match hasta el final (mismo comportamiento que la
       estrategia 1 para ese partido puntual), fail-closed hacia "no
       hay señal para actuar distinto".
    5. ADVERTENCIA METODOLÓGICA real: este dataset tiene ~25,000
       partidos a lo largo de 10 años -- compounding secuencial sobre
       TODOS ellos como si fueran apuestas consecutivas de un mismo
       trader NO es una frecuencia de trading realista (nadie apuesta
       en cada partido ATP jugado en 10 años). Con una ventaja real de
       ~10-20 puntos porcentuales por apuesta, compounding sobre ~25,000
       eventos puede desbordar a `inf` en punto flotante -- si eso pasa
       se reporta explícitamente como `"overflowed": true`, NO se
       inventa un número. Tratar estos resultados como ILUSTRATIVOS de
       la dinámica relativa entre estrategias, no como proyección de
       retorno esperado real.

    MÉTRICAS ADICIONALES pedidas explícitamente 2026-08-03 (usuario
    aceptó que compounding tras cada match esté bien "si en promedio
    el retorno es positivo"), calculadas para blindar contra la
    explosión numérica de arriba -- son ESCALA-INVARIANTES, no dependen
    de la magnitud absurda del capital final:
      - `avg_roi_per_trade_pct`: retorno promedio por OPERACIÓN (no por
        partido -- partidos donde la estrategia no apostó, ej. el
        retiro a breakeven, no cuentan como operación).
      - `max_drawdown_pct`: máxima caída pico-a-valle de la curva de
        equity completa (incluye partidos sin apuesta, capital plano).
      - `sharpe_ratio_per_trade`: media/desvío de los retornos POR
        OPERACIÓN -- NO anualizado (los partidos no están espaciados
        uniformemente en el tiempo, no hay una cadencia real de trading
        fija que permita anualizar de forma honesta)."""
    sorted_cases = sorted(cases, key=lambda c: c["date"])

    keys = [
        "1_simple_bet_favorite", "2_bet_only_after_favorite_wins_set1",
        "3_double_down_if_favorite_wins_set1", "4_exit_if_favorite_loses_set1",
        "5_combined_double_down_and_exit",
    ]
    capital = {k: initial_capital for k in keys}
    busted = {k: False for k in keys}
    trade_returns: dict[str, list[float]] = {k: [] for k in keys}  # pct por OPERACIÓN (con stake>0)
    equity_curves: dict[str, list[float]] = {k: [initial_capital] for k in keys}  # serie completa, para drawdown

    def _settle(key: str, stake: float, hit: bool) -> None:
        if busted[key] or stake <= 0:
            return
        before = capital[key]
        capital[key] += stake if hit else -stake
        trade_returns[key].append((stake if hit else -stake) / before)
        if capital[key] <= 0:
            capital[key] = 0.0
            busted[key] = True

    for c in sorted_cases:
        hit, s1 = c["hit"], c["set1_favorite_win"]

        _settle("1_simple_bet_favorite", capital["1_simple_bet_favorite"] * pre_match_stake_pct, hit)

        if s1 is True:
            _settle(
                "2_bet_only_after_favorite_wins_set1",
                capital["2_bet_only_after_favorite_wins_set1"] * set1_add_stake_pct, hit,
            )

        cap3 = capital["3_double_down_if_favorite_wins_set1"]
        stake3 = cap3 * pre_match_stake_pct + (cap3 * set1_add_stake_pct if s1 is True else 0.0)
        _settle("3_double_down_if_favorite_wins_set1", stake3, hit)

        if s1 is not False:  # True o desconocido -> sostiene (ver supuesto 4)
            _settle(
                "4_exit_if_favorite_loses_set1",
                capital["4_exit_if_favorite_loses_set1"] * pre_match_stake_pct, hit,
            )
        # s1 is False -> retiro a breakeven, capital sin cambios (supuesto 3)

        if s1 is not False:
            cap5 = capital["5_combined_double_down_and_exit"]
            stake5 = cap5 * pre_match_stake_pct + (cap5 * set1_add_stake_pct if s1 is True else 0.0)
            _settle("5_combined_double_down_and_exit", stake5, hit)
        # s1 is False -> retiro a breakeven, capital sin cambios

        for k in keys:  # serie completa, incluye partidos sin apuesta (capital plano) -- necesario para drawdown real
            equity_curves[k].append(capital[k])

    result = {}
    for k in keys:
        cap = capital[k]
        overflowed = cap == float("inf")
        returns = trade_returns[k]
        n_trades = len(returns)
        avg_roi = statistics.mean(returns) if returns else None
        sharpe = None
        if n_trades >= 2:
            stdev = statistics.stdev(returns)
            if stdev > 0:
                sharpe = round(avg_roi / stdev, 4)
        result[k] = {
            "initial_capital_usdc": initial_capital,
            "final_capital_usdc": None if overflowed else round(cap, 2),
            "return_pct": None if overflowed else round((cap / initial_capital - 1) * 100, 2),
            "busted": busted[k],
            "overflowed": overflowed,
            "n_trades": n_trades,
            "avg_roi_per_trade_pct": round(avg_roi * 100, 4) if avg_roi is not None else None,
            "max_drawdown_pct": _max_drawdown_pct(equity_curves[k]),
            "sharpe_ratio_per_trade": sharpe,
        }
    return result


def run_backtest(
    start_year: int = _DEFAULT_START_YEAR, end_year: int = _DEFAULT_END_YEAR,
    initial_capital: float = 50.0,
) -> dict[str, Any]:
    matches = get_matches_range(start_year, end_year, levels=TOUR_LEVELS_250_PLUS)

    cases = []
    excluded_no_rank = 0
    for m in matches:
        if m["winner_rank"] is None or m["loser_rank"] is None:
            excluded_no_rank += 1
            continue
        favorite_rank = min(m["winner_rank"], m["loser_rank"])
        underdog_rank = max(m["winner_rank"], m["loser_rank"])
        favorite_is_winner = m["winner_rank"] < m["loser_rank"]  # mejor ranking real
        hit = favorite_is_winner

        set1_winner = parse_set1_winner(m.get("score"))  # 'winner'|'loser'|None (del PARTIDO)
        set1_favorite_win = None
        if set1_winner is not None:
            # ¿el que ganó el set 1 es el mismo que el favorito por ranking?
            set1_favorite_win = (set1_winner == "winner") == favorite_is_winner

        set2_winner = parse_set2_winner(m.get("score"))  # 2026-08-03, variante 8
        set2_favorite_win = None
        if set2_winner is not None:
            set2_favorite_win = (set2_winner == "winner") == favorite_is_winner

        cases.append({
            "tourney_name": m["tourney_name"], "level": m["level"], "date": m["date"],
            "round": m["round"], "favorite_rank": favorite_rank, "underdog_rank": underdog_rank,
            "rank_gap": underdog_rank - favorite_rank,
            "hit": hit, "set1_favorite_win": set1_favorite_win, "set2_favorite_win": set2_favorite_win,
            "surface": m.get("surface"), "best_of": m.get("best_of"),
            "favorite_age": m.get("winner_age") if favorite_is_winner else m.get("loser_age"),
            "set1_diff": _set1_game_diff(m.get("score")),
        })

    n = len(cases)
    hits = sum(1 for c in cases if c["hit"])

    by_level: dict[str, dict[str, Any]] = {}
    for level in sorted({c["level"] for c in cases}):
        group = [c for c in cases if c["level"] == level]
        g_hits = sum(1 for c in group if c["hit"])
        by_level[level] = {"n": len(group), "hits": g_hits, "hit_rate": round(g_hits / len(group), 4)}

    by_round: dict[str, dict[str, Any]] = {}
    for round_name in sorted({c["round"] for c in cases}):
        group = [c for c in cases if c["round"] == round_name]
        g_hits = sum(1 for c in group if c["hit"])
        by_round[round_name] = {"n": len(group), "hits": g_hits, "hit_rate": round(g_hits / len(group), 4)}

    set1_cases = [c for c in cases if c["set1_favorite_win"] is not None]
    set1_hits = sum(1 for c in set1_cases if c["set1_favorite_win"])
    set1_n = len(set1_cases)

    # Condicional (2026-08-02, pedido explícito del usuario): de los
    # casos donde el FAVORITO se llevó el set 1, ¿en qué % también ganó
    # el partido? (distinto de `overall_hit_rate`, que promedia sobre
    # TODOS los partidos sin condicionar en el resultado del set 1).
    set1_won_by_favorite = [c for c in set1_cases if c["set1_favorite_win"]]
    match_hits_after_set1 = sum(1 for c in set1_won_by_favorite if c["hit"])
    match_after_set1_n = len(set1_won_by_favorite)

    # Categorización adicional (2026-08-02, pedido explícito del
    # usuario): mismo condicional de arriba, pero SOLO para partidos
    # donde el favorito además supera al underdog por >= 20 puestos de
    # ranking real -- ¿la brecha de ranking suma poder predictivo por
    # encima de "ganó el set 1" solo?
    _MIN_RANK_GAP = 20
    big_gap_set1_won_by_favorite = [c for c in set1_won_by_favorite if c["rank_gap"] >= _MIN_RANK_GAP]
    big_gap_hits = sum(1 for c in big_gap_set1_won_by_favorite if c["hit"])
    big_gap_n = len(big_gap_set1_won_by_favorite)

    # Winrate del UNDERDOG cuando pierde el set 1 -- pedido explícito
    # del usuario 2026-08-02, para dimensionar el RIESGO real de apostar
    # al favorito apenas se lleva el set 1 (sizing de posición: cuanto
    # mayor la tasa de remontada del underdog, menor debería ser el
    # tamaño de la apuesta al favorito). Es el complemento exacto de
    # `match_win_rate_after_favorite_wins_set1` (favorito ganó el
    # partido | favorito ganó set1) -- acá se nombra explícitamente
    # desde la perspectiva del underdog para el diseño de estrategia.
    underdog_comeback_hits = match_after_set1_n - match_hits_after_set1
    underdog_comeback_rate = (
        round(underdog_comeback_hits / match_after_set1_n, 4) if match_after_set1_n else None
    )

    # Desglose por brecha de ranking (buckets), para un sizing más fino
    # que un único umbral de 20 -- ¿la tasa de remontada del underdog
    # cae monótonamente a medida que crece la brecha de ranking real?
    _RANK_GAP_BUCKETS = [(0, 20), (20, 50), (50, 100), (100, None)]
    comeback_by_rank_gap: dict[str, dict[str, Any]] = {}
    for low, high in _RANK_GAP_BUCKETS:
        label = f"{low}-{high}" if high is not None else f"{low}+"
        bucket = [
            c for c in set1_won_by_favorite
            if c["rank_gap"] >= low and (high is None or c["rank_gap"] < high)
        ]
        b_n = len(bucket)
        b_underdog_wins = sum(1 for c in bucket if not c["hit"])
        comeback_by_rank_gap[label] = {
            "n": b_n, "underdog_comeback_hits": b_underdog_wins,
            "underdog_comeback_rate": round(b_underdog_wins / b_n, 4) if b_n else None,
        }

    # Caso simétrico (2026-08-02, pedido explícito del usuario): cuando
    # el UNDERDOG se lleva el set 1, ¿cuántas veces remonta el FAVORITO
    # y termina ganando el partido igual? Distinto de la remontada del
    # underdog de arriba -- acá el favorito arranca perdiendo.
    set1_won_by_underdog = [c for c in set1_cases if not c["set1_favorite_win"]]
    favorite_comeback_hits = sum(1 for c in set1_won_by_underdog if c["hit"])
    favorite_comeback_n = len(set1_won_by_underdog)
    favorite_comeback_rate = (
        round(favorite_comeback_hits / favorite_comeback_n, 4) if favorite_comeback_n else None
    )

    favorite_comeback_by_rank_gap: dict[str, dict[str, Any]] = {}
    for low, high in _RANK_GAP_BUCKETS:
        label = f"{low}-{high}" if high is not None else f"{low}+"
        bucket = [
            c for c in set1_won_by_underdog
            if c["rank_gap"] >= low and (high is None or c["rank_gap"] < high)
        ]
        b_n = len(bucket)
        b_favorite_wins = sum(1 for c in bucket if c["hit"])
        favorite_comeback_by_rank_gap[label] = {
            "n": b_n, "favorite_comeback_hits": b_favorite_wins,
            "favorite_comeback_rate": round(b_favorite_wins / b_n, 4) if b_n else None,
        }

    return {
        "years": f"{start_year}-{end_year}", "total_matches_fetched": len(matches),
        "excluded_no_rank": excluded_no_rank, "evaluable_cases": n, "hits": hits,
        "overall_hit_rate": round(hits / n, 4) if n else None,
        "set1_evaluable_cases": set1_n, "set1_hits": set1_hits,
        "set1_favorite_win_rate": round(set1_hits / set1_n, 4) if set1_n else None,
        "match_win_rate_after_favorite_wins_set1_evaluable_cases": match_after_set1_n,
        "match_win_rate_after_favorite_wins_set1_hits": match_hits_after_set1,
        "match_win_rate_after_favorite_wins_set1": (
            round(match_hits_after_set1 / match_after_set1_n, 4) if match_after_set1_n else None
        ),
        "match_win_rate_big_rank_gap_and_won_set1_evaluable_cases": big_gap_n,
        "match_win_rate_big_rank_gap_and_won_set1_hits": big_gap_hits,
        "match_win_rate_big_rank_gap_and_won_set1": round(big_gap_hits / big_gap_n, 4) if big_gap_n else None,
        "min_rank_gap_used": _MIN_RANK_GAP,
        "underdog_comeback_rate_after_losing_set1_evaluable_cases": match_after_set1_n,
        "underdog_comeback_rate_after_losing_set1_hits": underdog_comeback_hits,
        "underdog_comeback_rate_after_losing_set1": underdog_comeback_rate,
        "underdog_comeback_rate_by_rank_gap": comeback_by_rank_gap,
        "favorite_comeback_rate_after_losing_set1_evaluable_cases": favorite_comeback_n,
        "favorite_comeback_rate_after_losing_set1_hits": favorite_comeback_hits,
        "favorite_comeback_rate_after_losing_set1": favorite_comeback_rate,
        "favorite_comeback_rate_by_rank_gap": favorite_comeback_by_rank_gap,
        "by_level": by_level, "by_round": by_round,
        "strategy_comparison": _build_strategy_comparison(
            hits, n, match_hits_after_set1, match_after_set1_n,
            favorite_comeback_hits, favorite_comeback_n,
        ),
        "model_fair_price_calibration": _model_fair_price_calibration(cases),
        "capital_simulation": _simulate_capital_paths(cases, initial_capital=initial_capital),
        "capital_simulation_priced": _simulate_capital_paths_priced(cases, initial_capital=initial_capital),
        "algobetting_concurrency_capped": _simulate_algobetting_concurrency_capped(cases, initial_capital=initial_capital),
        "yearly_edge_degradation": _yearly_edge_degradation(cases),
        "realistic_all_strategies": _simulate_realistic_all_strategies(cases, initial_capital=initial_capital),
        "capital_simulation_caveat": (
            "`final_capital_usdc`/`return_pct` son ILUSTRATIVOS, no una "
            "proyección real de retorno -- payout asumido 1:1 (sin precio "
            "real de mercado en TML-Database), sizing 5% pre-match / 1% "
            "al confirmar set 1 (mismos tamaños ya decididos por el "
            "usuario, no un Kelly calculado), retiro a breakeven (sin "
            "precio real de salida), y compounding secuencial sobre los "
            f"{n} partidos del dataset -- frecuencia de apuesta NO "
            "realista para un trader real (nadie apuesta en cada partido "
            "ATP jugado en 10 años), por eso el capital final llega a "
            "magnitudes absurdas. `avg_roi_per_trade_pct`, "
            "`max_drawdown_pct` y `sharpe_ratio_per_trade` SÍ son "
            "confiables pese a eso -- son escala-invariantes (no dependen "
            "de la magnitud del capital final). Ver docstring de "
            "`_simulate_capital_paths` para el detalle completo de "
            "supuestos."
        ),
        "caveat": (
            "SOLO ATP -- sin equivalente WTA del mismo autor. Ranking REAL "
            "al momento del partido, no aproximado. set1_favorite_win_rate: "
            "% de partidos donde el favorito por ranking se llevó el "
            "PRIMER SET (no necesariamente el partido). "
            "underdog_comeback_rate_after_losing_set1: % de partidos donde "
            "el underdog, tras perder el set 1 contra el favorito, IGUAL "
            "ganó el partido -- riesgo real de apostar al favorito solo "
            "porque se llevó el set 1, para sizing de posición. "
            "favorite_comeback_rate_after_losing_set1: caso simétrico -- % "
            "de partidos donde el favorito, tras PERDER el set 1 contra el "
            "underdog, remonta y gana el partido igual. "
            "match_win_rate_after_favorite_wins_set1: CONDICIONAL -- de los "
            "partidos donde el favorito se llevó el set 1, % en que además "
            "ganó el partido completo. "
            "match_win_rate_big_rank_gap_and_won_set1: mismo condicional, "
            f"restringido a partidos con brecha de ranking real >= "
            f"{_MIN_RANK_GAP} puestos."
        ),
    }


if __name__ == "__main__":
    print(json.dumps(run_backtest(), indent=2, ensure_ascii=False))
