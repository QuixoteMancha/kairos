"""Veto de edad + fatiga acumulada -- 2026-09-05, pedido explícito del
usuario ("añadir como regla si el jugador/a tiene más de 30 años y tiene
fatiga acumulada considerable no se apuesta, sin importar la diferencia
de ranking") tras el análisis histórico real de esta misma sesión.

EVIDENCIA (27,049 casos reales de `MatchHistory` -- favoritos por
ranking con fatiga acumulada en el torneo COMPLETA conocida, ver sesión
2026-09-05): el WR cae con la fatiga en los 4 grupos de edad medidos,
pero la caída es MÁS PRONUNCIADA en el grupo 30+ años:

  edad       WR fresco (0-90min)   WR muy fatigado (270+min)   caída
  <23        62.8%                 55.7%                       -7.1pp
  23-27      65.5%                 60.0%                       -5.5pp
  27-30      65.3%                 56.4%                       -8.9pp
  30+        67.1%                 57.5%                       -9.6pp  <- la más grande

El umbral de "fatiga considerable" (270+ minutos acumulados en el
torneo) es justo el tramo donde esa caída del grupo 30+ se mide -- no
un número arbitrario.

Standalone y reusable a propósito: esta función NO dispara ninguna
apuesta ni bloquea nada por sí sola -- Kairos no ejecuta órdenes reales
(línea roja del proyecto). Cualquier estrategia/backtest que quiera
aplicar esta regla debe importarla y consultarla explícitamente antes
de generar una señal."""

from __future__ import annotations

_MIN_AGE_YEARS = 30
_MIN_FATIGUE_MINUTES = 270


def age_fatigue_veto(age_years: float | None, cum_minutes: float | None) -> bool:
    """True si NO se debe apostar por este jugador/a: 30+ años Y fatiga
    acumulada >= 270 minutos en el torneo -- se aplica sin importar el
    gap de ranking (pedido explícito del usuario), a diferencia del
    resto de los filtros de favorito que sí lo consideran.

    Fail-closed por falta de dato: si no se conoce la edad o la fatiga
    acumulada, esta función NO puede evaluar la regla -> devuelve
    `False` (no vetar por un dato faltante; el resto de los filtros de
    la estrategia que la use deciden)."""
    if age_years is None or cum_minutes is None:
        return False
    return age_years >= _MIN_AGE_YEARS and cum_minutes >= _MIN_FATIGUE_MINUTES
