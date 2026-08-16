"""Utilidades compartidas de pricing (motor base point-in-time) y
métricas de riesgo-ajustado (CAGR/Sharpe/Sortino/Calmar/CVaR/drawdown)
-- 2026-08-10, extraídas de `favorite_by_historical_rank_backtest.py`
(eliminado junto con toda la fuente externa que dependía, ver pedido
explícito del usuario: "usaremos sólo tennisexplorer") porque son
matemática pura, sin ningún acoplamiento a esa fuente -- solo usan
`models/volatility/sports/tennis/tennis_markov.py` (que sigue
existiendo) y estadística estándar sobre curvas de equity genéricas.

Reusadas por cualquier backtest que necesite pricing point-in-time o
métricas de riesgo estándar, sin duplicar la lógica."""

from __future__ import annotations

import statistics
from math import comb
from typing import Any

from outer_apis.kalshi.fees import leg_fee_fraction

from models.volatility.sports.tennis.tennis_markov import p_serve_for_rank, _p_win_game, _p_win_set

_TRADING_DAYS_PER_YEAR_ATP = 36.7  # ~367 días reales de trading ATP / 10 años (2016-2026), medido.
_PRE_MATCH_STAKE_PCT = 0.05
_SET1_ADD_STAKE_PCT = 0.01
_ALL_STRATEGY_KEYS = [
    "1_simple_bet_favorite", "2_bet_only_after_favorite_wins_set1",
    "3_double_down_if_favorite_wins_set1", "4_exit_if_favorite_loses_set1",
    "5_combined_double_down_and_exit", "6_combined_with_hedge",
    "7_hold_if_high_comeback_condition_else_exit",
    "8_exit_double_down_leg_if_favorite_loses_set2",
]


def _single_set_win_prob(rank_a: int, rank_b: int, surface: str | None, tour: str = "ATP") -> float:
    """P(A gana UN set) real, calibrado por ranking+superficie -- misma
    metodología interna que usa `pre_match_win_prob` (reutiliza sus
    mismos bloques, `p_serve_for_rank`/`_p_win_game`/`_p_win_set`)."""
    pa_serve = p_serve_for_rank(rank_a, tour=tour, surface=surface)
    pb_serve = p_serve_for_rank(rank_b, tour=tour, surface=surface)
    pa_game = _p_win_game(pa_serve)
    pb_game = _p_win_game(pb_serve)
    return _p_win_set(pa_game, pb_game, a_serves_first=True, pa_serve=pa_serve, pb_serve=pb_serve)


def _p_win_match_given_set_score(p_set: float, sets_a: int, sets_b: int, best_of: int) -> float:
    """P(A gana el partido) dado el marcador de sets actual, asumiendo
    sets i.i.d. Bernoulli(p_set)."""
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


def _real_price_legs(
    favorite_rank: int, underdog_rank: int, best_of: int, surface: str | None, tour: str = "ATP",
) -> tuple[float, float, float]:
    """3 precios REALES (motor base, point-in-time, sin lookahead) para
    el lado FAVORITO: pre-match, tras ganar el set 1, tras perder el
    set 1."""
    from models.volatility.sports.tennis.tennis_markov import pre_match_win_prob

    price_pre_match = pre_match_win_prob(favorite_rank, underdog_rank, best_of=best_of, tour=tour, surface=surface)
    p_set_favorite = _single_set_win_prob(favorite_rank, underdog_rank, surface, tour=tour)
    price_after_set1_win = _p_win_match_given_set_score(p_set_favorite, 1, 0, best_of)
    price_after_set1_loss = _p_win_match_given_set_score(p_set_favorite, 0, 1, best_of)
    return price_pre_match, price_after_set1_win, price_after_set1_loss


def _price_at_1_1(favorite_rank: int, underdog_rank: int, best_of: int, surface: str | None, tour: str = "ATP") -> float:
    """Precio REAL (motor base) del favorito estando 1-1 en sets."""
    p_set_favorite = _single_set_win_prob(favorite_rank, underdog_rank, surface, tour=tour)
    return _p_win_match_given_set_score(p_set_favorite, 1, 1, best_of)


def _max_drawdown_pct(equity_curve: list[float]) -> float | None:
    """Máxima caída pico-a-valle de la curva de equity, en % del pico."""
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


def _daily_returns(equity_curve: list[float], initial_capital: float) -> list[float]:
    """Retorno % día a día de la curva de equity."""
    if not equity_curve:
        return []
    prev = initial_capital
    out = []
    for v in equity_curve:
        if prev > 0:
            out.append((v - prev) / prev)
        prev = v
    return out


def _annualized_sharpe(
    equity_curve: list[float], initial_capital: float, trading_days_per_year: float = _TRADING_DAYS_PER_YEAR_ATP,
) -> float | None:
    """Sharpe anualizado real sobre retornos DIARIOS. Sin tasa libre de
    riesgo (asumida 0)."""
    returns = _daily_returns(equity_curve, initial_capital)
    if len(returns) < 2:
        return None
    stdev = statistics.stdev(returns)
    if stdev == 0:
        return None
    avg = statistics.mean(returns)
    return round((avg / stdev) * (trading_days_per_year ** 0.5), 4)


def _annualized_sortino(
    equity_curve: list[float], initial_capital: float, trading_days_per_year: float = _TRADING_DAYS_PER_YEAR_ATP,
) -> float | None:
    """Como el Sharpe anualizado, pero solo penaliza la desviación
    NEGATIVA (downside deviation)."""
    returns = _daily_returns(equity_curve, initial_capital)
    if len(returns) < 2:
        return None
    downside = [r for r in returns if r < 0]
    if not downside:
        return None
    downside_stdev = (sum(r ** 2 for r in downside) / len(returns)) ** 0.5
    if downside_stdev == 0:
        return None
    avg = statistics.mean(returns)
    return round((avg / downside_stdev) * (trading_days_per_year ** 0.5), 4)


def _cagr_pct(
    equity_curve: list[float], initial_capital: float, trading_days_per_year: float = _TRADING_DAYS_PER_YEAR_ATP,
) -> float | None:
    """Tasa de crecimiento anual compuesta real, derivada de la curva de
    equity diaria."""
    if not equity_curve or initial_capital <= 0:
        return None
    final = equity_curve[-1]
    if final <= 0:
        return None
    years = len(equity_curve) / trading_days_per_year
    if years <= 0:
        return None
    return round(((final / initial_capital) ** (1 / years) - 1) * 100, 2)


def _calmar_ratio(cagr_pct: float | None, max_drawdown_pct: float | None) -> float | None:
    """CAGR / drawdown máximo."""
    if cagr_pct is None or max_drawdown_pct is None or max_drawdown_pct == 0:
        return None
    return round(cagr_pct / max_drawdown_pct, 4)


def _cvar_95_pct(returns: list[float]) -> float | None:
    """Conditional Value at Risk al 95% -- promedio del peor 5% de los
    retornos."""
    if not returns:
        return None
    sorted_returns = sorted(returns)
    tail_n = max(1, round(len(sorted_returns) * 0.05))
    tail = sorted_returns[:tail_n]
    return round((sum(tail) / len(tail)) * 100, 4)
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
def _simulate_realistic_all_strategies(
    cases: list[dict[str, Any]], initial_capital: float = 50.0,
    stake_pct: float = 0.01, max_simultaneous_pct: float = 0.5,
    max_stake_usd: float | None = None, hedge_stake_pct: float | None = None, tour: str = "ATP",
    trading_days_per_year: float | None = None,
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
            c["favorite_rank"], c["underdog_rank"], best_of, c.get("surface"), tour=tour,
        )
        price_1_1 = _price_at_1_1(c["favorite_rank"], c["underdog_rank"], best_of, c.get("surface"), tour=tour)
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
    # 2026-08-05, ver docstring de `_annualized_sharpe` sobre el bug
    # real de reusar la constante ATP (`_TRADING_DAYS_PER_YEAR_ATP`)
    # para otro tour sin parametrizar -- default None preserva el
    # comportamiento ATP exacto de siempre (constante fija, nunca
    # medida por-corrida), el caller (ej. WTA) puede pasar la cadencia
    # real medida de su propio pool de casos.
    _tdpy = trading_days_per_year if trading_days_per_year is not None else _TRADING_DAYS_PER_YEAR_ATP

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
        max_dd = _max_drawdown_pct(equity_curves[key])
        cagr = _cagr_pct(equity_curves[key], initial_capital, trading_days_per_year=_tdpy)
        result[key] = {
            "initial_capital_usdc": initial_capital,
            "final_capital_usdc": round(cap, 2),
            "roi_total_pct": round((cap / initial_capital - 1) * 100, 2),
            "n_trades": n_trades,
            "n_skipped_concurrency_cap": skipped_cap[key],
            "winrate": round(resolve_hits[key] / resolve_n[key], 4) if resolve_n[key] else None,
            "avg_roi_per_trade_pct": round(avg_roi * 100, 4) if avg_roi is not None else None,
            "max_drawdown_pct": max_dd,
            "sharpe_ratio_per_trade": sharpe,
            # 2026-08-04, pedido explícito -- métricas adicionales de
            # riesgo-ajustado, ver docstrings de cada helper arriba.
            "cagr_pct": cagr,
            "annualized_sharpe": _annualized_sharpe(equity_curves[key], initial_capital, trading_days_per_year=_tdpy),
            "annualized_sortino": _annualized_sortino(equity_curves[key], initial_capital, trading_days_per_year=_tdpy),
            "calmar_ratio": _calmar_ratio(cagr, max_dd),
            "cvar_95_pct": _cvar_95_pct(returns),
            "busted": busted[key],
        }
    return {
        "strategies": result, "skipped_invalid_best_of": skipped,
        "stake_pct_used": stake_pct, "max_simultaneous_pct_used": max_simultaneous_pct,
        "max_stake_usd_used": max_stake_usd, "hedge_stake_pct_used": _hedge_pct,
        "trading_days_per_year_used": _tdpy,
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


