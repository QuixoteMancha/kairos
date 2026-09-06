"""Veto de edad + fatiga acumulada -- 2026-09-05, pedido explícito del
usuario, ampliado en una segunda ronda a "todas las condicionantes que
juntas dan WR menor a 60%" (no solo el par 30+/270min inicial).

EVIDENCIA (27,049 casos reales de `MatchHistory` -- favoritos por
ranking con fatiga acumulada en el torneo COMPLETA conocida, sesión
2026-09-05). WR real medido por celda (edad x minutos acumulados):

               [0-90)   [90-180)  [180-270)  [270+)
  <23          62.8%    59.8%     58.1%      55.7%
  23-27        65.5%    61.8%     61.3%      60.0%
  27-30        65.3%    61.6%     60.8%      56.4%
  30+          67.1%    62.5%     61.0%      57.5%

Celdas con WR < 60% (la regla del usuario: por debajo de eso, apostar
al favorito es -- en la práctica -- una moneda al aire, no una ventaja
real):
  - <23 años,   >=90 minutos acumulados  (59.8% / 58.1% / 55.7%)
  - 27-30 años, >=270 minutos acumulados (56.4%)
  - 30+ años,   >=270 minutos acumulados (57.5%)

El grupo 23-27 NUNCA cae por debajo de 60% en ningún tramo medido (el
mínimo es exactamente 60.0%, no menor) -- por eso, a diferencia de los
otros 3 grupos, este veto NO se aplica nunca a jugadores de 23 a 27
años, sin importar cuánta fatiga acumulen.

Esto es una TABLA de celdas medidas, no una fórmula continua/
interpolada -- fabricar una curva suave entre puntos que no se
verificaron sería inventar precisión que los datos no dan. Un caso
fuera de estos 4 buckets de edad x 4 de fatiga (no debería pasar dado
que los buckets cubren [0, inf) en ambos ejes) o con algún dato
faltante, fail-closed: no se veta.

Standalone y reusable a propósito, común a CUALQUIER estrategia
(pedido explícito: "sin importar la estrategia") -- esta función NO
dispara ninguna apuesta ni bloquea nada por sí sola (Kairos no ejecuta
órdenes reales, línea roja del proyecto). Cualquier estrategia/backtest
que quiera aplicar esta regla debe importarla y consultarla
explícitamente antes de generar una señal."""

from __future__ import annotations

# (edad_min, edad_max_exclusiva, fatiga_min_minutos) -- vetar si el
# jugador cae en CUALQUIERA de estas 3 celdas medidas con WR<60%. El
# límite superior de fatiga es siempre abierto (miente>=): el tramo
# real medido era "270+" (sin techo), no "270-algo".
_VETO_CELLS = (
    (0, 23, 90),      # <23 años, WR 59.8%/58.1%/55.7% desde 90min
    (27, 30, 270),    # 27-30 años, WR 56.4% desde 270min
    (30, float("inf"), 270),  # 30+ años, WR 57.5% desde 270min
)


def age_fatigue_veto(age_years: float | None, cum_minutes: float | None) -> bool:
    """True si NO se debe apostar por este jugador/a, sin importar la
    estrategia ni el gap de ranking: cae en alguna de las celdas reales
    de edad x fatiga acumulada donde el WR histórico medido es <60%
    (ver docstring del módulo para la tabla completa).

    Fail-closed por falta de dato: si no se conoce la edad o la fatiga
    acumulada, esta función NO puede evaluar la regla -> devuelve
    `False` (no vetar por un dato faltante)."""
    if age_years is None or cum_minutes is None:
        return False
    return any(
        age_min <= age_years < age_max and cum_minutes >= fatigue_min
        for age_min, age_max, fatigue_min in _VETO_CELLS
    )
