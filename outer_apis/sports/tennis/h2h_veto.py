"""Veto por head-to-head directo -- 2026-09-07, pedido explícito del
usuario tras validar contra 174,875 partidos reales que cuando el
historial directo entre ambos jugadores NO favorece al favorito por
ranking, su WR real cae muy por debajo del propio ranking:

    H2H favorable/parejo (>=50%): 67.0% WR  (n=1,405)
    H2H parejo en contra (33-50%): 54.1% WR  (n=519)
    H2H en contra fuerte (<33%):   52.8% WR  (n=290)

Fisher exact (favorable vs en_contra_fuerte): p=0.000006. Robusto frente
al umbral de muestra mínima elegido (confirmado igual de significativo
con MIN_H2H=3 y MIN_H2H=8, no es un artefacto del corte de 5).

Mismo criterio que `age_fatigue_veto`: ambos buckets "en contra" caen por
debajo del 60% que el usuario fijó como línea de "moneda al aire" -- si
se cumple, no se apuesta sin importar la diferencia de ranking.
"""

from __future__ import annotations

MIN_H2H = 5


def has_sufficient_h2h(prior_meetings: int) -> bool:
    """Si el historial directo alcanza el mínimo validado estadísticamente."""
    return prior_meetings >= MIN_H2H


def h2h_veto(fav_h2h_wr: float | None) -> bool:
    """True si el historial directo no favorece al favorito por ranking.

    El caller debe excluir el partido cuando ``has_sufficient_h2h`` es
    falso: la ausencia de muestra no es evidencia a favor de apostar.
    ``None`` se conserva como defensa para callers legacy."""
    if fav_h2h_wr is None:
        return False
    return fav_h2h_wr < 0.50
