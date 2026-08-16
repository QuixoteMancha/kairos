"""Motor de Elo propio para tenis -- 2026-08-11, pedido explícito del
usuario tras descartar depender de una fuente externa de ranking
histórico ATP (la página oficial de ATP tiene el parámetro de fecha
roto/ignorado, verificado en vivo) o de un scraping indirecto vía
Wayback Machine/Wikipedia: "sigamos con el Elo propio en base al
historial de matches".

Fórmula ESTÁNDAR de Elo (Arpad Elo, dominio público, la misma base que
ajedrez) -- deliberadamente SIMPLE, no la variante mejorada de
FiveThirtyEight (que ajusta K por margen de victoria/superficie/edad
del jugador) -- esa complejidad no se pidió, se puede sumar después si
hace falta. Acá:

    expected_a = 1 / (1 + 10 ** ((elo_b - elo_a) / 400))
    new_elo_a = elo_a + K * (score_a - expected_a)

`score_a` es 1.0 si A ganó, 0.0 si perdió (tenis no tiene empates).

IMPORTANTE, honesto: esto es una medida de FUERZA relativa calculada
por nosotros mismos a partir de resultados reales -- NO reproduce ni
se parece numéricamente al ranking oficial ATP/WTA (que es un sistema
de PUNTOS acumulados por torneo/ronda en una ventana de 52 semanas,
matemáticamente no relacionado con Elo, ver conversación 2026-08-11).
Sirve como reemplazo conceptual del filtro "gap de fuerza entre
jugadores" en los backtests, con umbrales que hay que recalibrar en
escala Elo, no en puestos de ranking.

Elo separado por TOUR (ATP/WTA nunca se comparan entre sí -- no jugaron
partidos cruzados reales que permitan calibrar una escala común)."""

from __future__ import annotations

DEFAULT_INITIAL_ELO = 1500.0
DEFAULT_K_FACTOR = 32.0


def expected_score(elo_a: float, elo_b: float) -> float:
    """Probabilidad esperada de que A le gane a B, dado el Elo actual de
    ambos -- fórmula logística estándar. Devuelve un valor en (0, 1)."""
    return 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400.0))


def update_elo(
    elo_a: float, elo_b: float, a_won: bool, k: float = DEFAULT_K_FACTOR,
) -> tuple[float, float]:
    """Devuelve (nuevo_elo_a, nuevo_elo_b) tras un partido real entre A y
    B -- `a_won`: True si A ganó, False si ganó B (tenis no tiene
    empates, no hay tercer caso). Suma cero: lo que gana uno lo pierde
    el otro exactamente (K * delta), propiedad estándar de Elo."""
    exp_a = expected_score(elo_a, elo_b)
    score_a = 1.0 if a_won else 0.0
    delta = k * (score_a - exp_a)
    return elo_a + delta, elo_b - delta
