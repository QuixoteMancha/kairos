"""Métricas de riesgo-ajustado GENÉRICAS (no atadas a un deporte) --
2026-08-05, extraídas de `scripts/tennis/favorite_by_historical_rank_backtest.py`
(que las tenía hardcodeadas a `_TRADING_DAYS_PER_YEAR_ATP`) para poder
reusarlas en el backtest de MLB sin duplicar la lógica -- pedido
explícito del usuario: "hagamos un backtest de una estrategia parecida
a la seleccionada en tenis... para esta y otra sesiones" (implica
reusar el mismo criterio de métricas en futuros deportes, no solo
copiar y pegar).

Mismo criterio ya documentado en la sesión de tenis (ver Obsidian
"Backtest Favorito ATP y Estrategia Set1 - Kairos.md", sección 6.16):
el Sharpe/Sortino por OPERACIÓN individual no es comparable con
benchmarks estándar (no está anualizado, mezcla operaciones
correlacionadas del mismo día como si fueran independientes) -- estas
funciones trabajan sobre la CURVA DE EQUITY DIARIA en cambio.

`trading_days_per_year` es un parámetro explícito acá (a diferencia de
la versión original de tenis, que lo hardcodeaba a un módulo-constante)
-- cada deporte tiene su propio calendario real de días de trading
(ATP ~36.7 días/año medido; MLB tiene su propio número real, distinto,
ver `favorite_elo_homeaway_filter_backtest.py`), nunca asumir el mismo
valor entre deportes."""

from __future__ import annotations


def max_drawdown_pct(equity_curve: list[float]) -> float | None:
    """Máxima caída pico-a-valle de la curva de equity, en % del pico
    (escala-invariante)."""
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


def daily_returns(equity_curve: list[float], initial_capital: float) -> list[float]:
    """Retorno % día a día de la curva de equity (1 valor por día de
    trading real, no por operación) -- incluye el primer día contra el
    capital inicial."""
    if not equity_curve:
        return []
    prev = initial_capital
    out = []
    for v in equity_curve:
        if prev > 0:
            out.append((v - prev) / prev)
        prev = v
    return out


def annualized_sharpe(
    equity_curve: list[float], initial_capital: float, trading_days_per_year: float,
) -> float | None:
    """Sharpe anualizado real -- media/desvío de los retornos DIARIOS,
    escalado por sqrt(días de trading reales/año). Sin tasa libre de
    riesgo (asumida 0)."""
    returns = daily_returns(equity_curve, initial_capital)
    if len(returns) < 2:
        return None
    import statistics
    stdev = statistics.stdev(returns)
    if stdev == 0:
        return None
    avg = statistics.mean(returns)
    return round((avg / stdev) * (trading_days_per_year ** 0.5), 4)


def annualized_sortino(
    equity_curve: list[float], initial_capital: float, trading_days_per_year: float,
) -> float | None:
    """Como el Sharpe anualizado, pero solo penaliza la desviación
    NEGATIVA -- más apropiado para retornos asimétricos."""
    returns = daily_returns(equity_curve, initial_capital)
    if len(returns) < 2:
        return None
    downside = [r for r in returns if r < 0]
    if not downside:
        return None
    downside_stdev = (sum(r ** 2 for r in downside) / len(returns)) ** 0.5
    if downside_stdev == 0:
        return None
    import statistics
    avg = statistics.mean(returns)
    return round((avg / downside_stdev) * (trading_days_per_year ** 0.5), 4)


def cagr_pct(equity_curve: list[float], initial_capital: float, trading_days_per_year: float) -> float | None:
    """Tasa de crecimiento anual compuesta real, derivada de la curva
    de equity diaria y el número real de años de trading cubiertos."""
    if not equity_curve or initial_capital <= 0:
        return None
    final = equity_curve[-1]
    if final <= 0:
        return None
    years = len(equity_curve) / trading_days_per_year
    if years <= 0:
        return None
    return round(((final / initial_capital) ** (1 / years) - 1) * 100, 2)


def calmar_ratio(cagr_pct_value: float | None, max_drawdown_pct_value: float | None) -> float | None:
    """CAGR / drawdown máximo. Benchmark absoluto conocido: >1.0 se
    considera bueno, Renaissance Technologies reporta >2.0."""
    if cagr_pct_value is None or max_drawdown_pct_value is None or max_drawdown_pct_value == 0:
        return None
    return round(cagr_pct_value / max_drawdown_pct_value, 4)


def cvar_95_pct(returns: list[float]) -> float | None:
    """Conditional Value at Risk al 95% -- promedio del peor 5% de las
    operaciones. Más honesto que el desvío estándar para una
    distribución con cola pesada real."""
    if not returns:
        return None
    sorted_returns = sorted(returns)
    tail_n = max(1, round(len(sorted_returns) * 0.05))
    tail = sorted_returns[:tail_n]
    return round((sum(tail) / len(tail)) * 100, 4)
