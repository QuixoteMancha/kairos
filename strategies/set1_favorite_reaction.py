"""Estrategia de sizing por reacción al primer set -- pedido explícito
del usuario 2026-08-02 (caso real que la motivó: Pegula vs Eala,
Washington Final WTA, `KXWTAMATCH-26AUG02PEGEAL` -- el usuario compró
Pegula manualmente 5% pre-match por infravaloración del modelo, y pidió
codificar una regla de 1% adicional para cuando el favorito se lleva el
set 1: "de todas formas es solo prueba... añadamos esta estrategia para
que use el 1% para comprar luego de que el favorite gane el primer set").

FUENTE DE PROBABILIDAD: `scripts/favorite_by_historical_rank_backtest.py`
corrido contra TML-Database (ATP, 2016-2026, torneos 250+ -- 250/500/
Masters/Slam/Finals, ranking histórico REAL de cada jugador al momento
del partido). Valores hardcodeados abajo son el resultado de la corrida
real del 2026-08-02 (ver memoria
kairos-match-charting-project-import-2026-08-02.md, sección "Cuadro
completo de las 4 combinaciones"). SOLO ATP -- la extrapolación a WTA
(como el caso real de Pegula/Eala que motivó esto) es CUALITATIVA, no
validada con datos WTA propios (no existe fuente equivalente a
TML-Database para WTA, investigado).

ALCANCE ACTUAL -- IMPORTANTE, LEER ANTES DE USAR: Kairos NO tiene un
feed de score en vivo durante el partido (ni point-by-point ni
set-by-set) -- ningún worker detecta automáticamente "el favorito ganó
el set 1". Este módulo define la REGLA de sizing dado ese resultado,
NO un disparador automático -- hoy `favorite_won_set1` se pasa a mano
(el usuario confirma el resultado real del set 1 mirando la transmisión/
Kalshi, como hizo con Pegula/Eala). Automatizar la detección requeriría
un scraper nuevo de score en vivo (ej. Flashscore), no construido acá.

1% es un tamaño de PRUEBA fijo pedido EXPLÍCITAMENTE por el usuario --
deliberadamente MÁS CHICO que el Kelly fraccional con tope que se
discutió antes en la misma conversación (que daba 10%+ para p~86%,
sujeto al tope de equity ya usado en `strategies/paper_trading.py`).
Este módulo NO calcula Kelly -- respeta el 1% fijo pedido, documentado
como prueba inicial conservadora, no como el sizing óptimo calculado.

SIN EJECUCIÓN REAL DE ÓRDENES (línea roja del proyecto, sigue vigente)
-- esto es una recomendación de tamaño, nunca coloca nada."""

from __future__ import annotations

from typing import Any

SET1_ENTRY_SIZE_PCT = 0.01  # 1% del equity, pedido explícito del usuario 2026-08-02

# Resultado real del backtest 2016-2026 (ATP, TML-Database, torneos
# 250+), corrida real 2026-08-02 -- ver
# scripts/favorite_by_historical_rank_backtest.py y memoria
# kairos-match-charting-project-import-2026-08-02.md.
FAVORITE_WIN_RATE_AFTER_SET1_OVERALL = 0.8600
FAVORITE_WIN_RATE_AFTER_SET1_BY_RANK_GAP = {
    "0-20": round(1 - 0.1744, 4),
    "20-50": round(1 - 0.1457, 4),
    "50-100": round(1 - 0.1227, 4),
    "100+": round(1 - 0.1111, 4),
}


def _rank_gap_bucket(rank_gap: int) -> str:
    if rank_gap < 20:
        return "0-20"
    if rank_gap < 50:
        return "20-50"
    if rank_gap < 100:
        return "50-100"
    return "100+"


def get_favorite_win_rate_after_set1(rank_gap: int | None) -> float:
    """Probabilidad real (ATP, TML-Database, 2016-2026) de que el
    favorito cierre el partido dado que ya ganó el set 1, según la
    brecha de ranking real (underdog_rank - favorite_rank). Fail-closed
    a la tasa GENERAL (no a un bucket adivinado) si no hay `rank_gap`
    real disponible."""
    if rank_gap is None:
        return FAVORITE_WIN_RATE_AFTER_SET1_OVERALL
    bucket = _rank_gap_bucket(rank_gap)
    return FAVORITE_WIN_RATE_AFTER_SET1_BY_RANK_GAP[bucket]


def recommend_set1_entry(favorite_won_set1: bool, rank_gap: int | None = None) -> dict[str, Any]:
    """Recomendación de tamaño de posición tras conocerse el resultado
    REAL del set 1 (confirmado externamente, ver docstring del módulo
    -- este módulo no lo detecta solo) -- pedido explícito del usuario:
    1% fijo si el FAVORITO se llevó el set 1, sin entrada bajo esta
    regla si no.

    Fail-closed: no recomienda entrada si el favorito no ganó el set 1
    bajo ESTA regla puntual (el caso underdog-gana-set1 tiene su propia
    probabilidad real, ver `favorite_comeback_rate_after_losing_set1`
    en el backtest, pero esta función solo cubre lo pedido explícitamente:
    reaccionar cuando el favorito confirma el set 1)."""
    if not favorite_won_set1:
        return {
            "size_pct": 0.0, "side": None,
            "reason": (
                "El favorito no ganó el set 1 -- esta regla no dispara "
                "entrada (pedido explícito: solo 1% tras ganar el "
                "favorito el set 1)."
            ),
        }

    win_rate = get_favorite_win_rate_after_set1(rank_gap)
    return {
        "size_pct": SET1_ENTRY_SIZE_PCT, "side": "favorite",
        "reason": (
            f"Favorito ganó el set 1 -- probabilidad histórica real "
            f"(ATP, TML-Database) de cerrar el partido: {win_rate * 100:.1f}%."
        ),
        "favorite_win_rate_basis": win_rate,
    }
