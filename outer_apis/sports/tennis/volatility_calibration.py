"""Consulta ponderada de volatilidad OBSERVADA REAL (desviación estándar
del camino de precio tick-by-tick de partidos ya resueltos), combinando
la DB activa ("data nueva", volumen de la mini-pc) con la DB de archivo
("data histórica", disco externo) -- pedido explícito del usuario
2026-07-31, punto 3: más peso a la data reciente.

PROPÓSITO: esto es un INFORME/consulta, no está wireado al fair-price ni
a la simulación Monte Carlo de `tennis_markov.py` todavía -- reemplazar
`simulate_win_prob_volatility` (simulado) por esta volatilidad empírica
real sería un cambio de diseño mayor que necesita su propia decisión
explícita, no algo que se cuela como efecto secundario de construir la
consulta. Por ahora esto sirve para COMPARAR: ¿la volatilidad que
simulamos se parece a la que realmente se observa en Kalshi, por
superficie/tour?

Fail-closed en cada capa: sin partidos resueltos suficientes -> None,
disco externo no montado -> se ignora esa mitad y se usa solo la
disponible (nunca se fabrica un promedio con una sola fuente fingiendo
que están ambas)."""

from __future__ import annotations

from typing import Any

from database.db import init_db_historical
from database.historical.tennis.tennisscannedmatch import TennisScannedMatch
from database.historical.tennis.tennismatchprobabilitytick import TennisMatchProbabilityTick
from database.historical.tennis.archive_db import (
    ArchivedTennisScannedMatch, ArchivedTennisMatchProbabilityTick,
    ArchiveNotMountedError, _ensure_archive_db_initialized,
)

MIN_MATCHES_FOR_STAT = 5  # fail-closed: menos partidos resueltos que esto -> sin estadística confiable
DEFAULT_RECENT_WEIGHT = 0.8  # pedido explícito: más peso a la data reciente (activa) que a la archivada


def _match_volatility(prices: list[float]) -> float | None:
    """Desviación estándar poblacional del camino de precio real de UN
    partido -- mismo criterio de reducción que
    `tennis_markov.simulate_win_prob_volatility` (stddev, no IQR), para
    que sea directamente comparable contra el número simulado."""
    if len(prices) < 2:
        return None
    mean = sum(prices) / len(prices)
    variance = sum((p - mean) ** 2 for p in prices) / len(prices)
    return variance ** 0.5


def _mean_observed_volatility(match_model, tick_model, surface: str | None, tour: str | None) -> dict[str, Any] | None:
    query = match_model.select().where(match_model.winner.is_null(False))  # solo partidos ya resueltos
    if surface:
        query = query.where(match_model.surface == surface)
    if tour:
        query = query.where(match_model.tour == tour)

    volatilities: list[float] = []
    for match in query:
        prices = [
            t.market_price_a for t in
            tick_model.select().where(tick_model.match == match)
            if t.market_price_a is not None
        ]
        vol = _match_volatility(prices)
        if vol is not None:
            volatilities.append(vol)

    if len(volatilities) < MIN_MATCHES_FOR_STAT:
        return None

    return {
        "mean_volatility": round(sum(volatilities) / len(volatilities), 4),
        "n_matches": len(volatilities),
    }


def get_recent_observed_volatility(surface: str | None = None, tour: str | None = None) -> dict[str, Any] | None:
    """Volatilidad observada real, SOLO de la DB activa ("data nueva")."""
    init_db_historical()
    return _mean_observed_volatility(TennisScannedMatch, TennisMatchProbabilityTick, surface, tour)


def get_archived_observed_volatility(surface: str | None = None, tour: str | None = None) -> dict[str, Any] | None:
    """Volatilidad observada real, SOLO de la DB de archivo ("data
    histórica"). Fail-closed: None si el disco externo no está montado."""
    try:
        _ensure_archive_db_initialized()
    except ArchiveNotMountedError:
        return None
    return _mean_observed_volatility(ArchivedTennisScannedMatch, ArchivedTennisMatchProbabilityTick, surface, tour)


def get_weighted_observed_volatility(
    surface: str | None = None, tour: str | None = None, recent_weight: float = DEFAULT_RECENT_WEIGHT,
) -> dict[str, Any] | None:
    """Combina data reciente (activa) + histórica (archivada), dándole
    más peso a la reciente (`recent_weight`, default 0.8 -- NO es un
    promedio ponderado por tamaño de muestra, es un peso EXPLÍCITO fijo
    a favor de lo reciente, tal como se pidió: si la data histórica
    archivada tuviera 10x más partidos que la reciente, un promedio
    ponderado por muestra la haría dominar -- acá se evita eso a
    propósito).

    Fail-closed: None si NINGUNA de las 2 fuentes tiene muestra
    suficiente. Si solo una fuente tiene datos, se devuelve esa sola
    (documentado en `sources_used`), sin fabricar una combinación con una
    fuente inexistente."""
    recent = get_recent_observed_volatility(surface, tour)
    archived = get_archived_observed_volatility(surface, tour)

    if recent is None and archived is None:
        return None
    if archived is None:
        return {**recent, "sources_used": "recent_only"}
    if recent is None:
        return {**archived, "sources_used": "archived_only"}

    combined = recent_weight * recent["mean_volatility"] + (1 - recent_weight) * archived["mean_volatility"]
    return {
        "mean_volatility": round(combined, 4),
        "n_matches_recent": recent["n_matches"],
        "n_matches_archived": archived["n_matches"],
        "recent_weight": recent_weight,
        "sources_used": "both",
    }
