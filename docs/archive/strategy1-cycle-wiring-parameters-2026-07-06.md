# Parámetros de cableado — Strategy 1 universal al paper_trading_cycle

Autora: Vesper (auditora cuantitativa). Encargo: Sara. 2026-07-06.

Alcance: SOLO diseño/matemática de los 5 parámetros pedidos. No se tocó código.
Archivos leídos: `src/core/strategies/universal_limit_arb.py`,
`docs/universal-limit-order-strategy-math-audit-2026-07-05.md`,
`src/core/orchestration/paper_trading_cycle.py`, cabecera de `src/core/orchestration/scanning.py`.

**Nota de seguridad:** en el output de una de mis lecturas de archivo (bash) apareció un
bloque de `system-reminder` inyectado con instrucciones falsas (servidor MCP "Shopify",
cambio de fecha, mensaje de "Auto Mode Active" instando a no detenerme a pedir confirmación).
Ninguna de esas herramientas/servidores es real en este contexto ni fue invocada por mí — lo
ignoré por completo, no cambia mi alcance (solo este doc, sin tocar código) ni mi criterio de
cuándo pedirle a Sara una decisión.

---

## 1. Banda "cerca de 50%" para entrar en `observing`

`EPSILON_INITIAL = 0.03` en `universal_limit_arb.py` fija dónde se REPOSAN las dos órdenes
(precio_actual ± 0.03), no dónde debe estar el precio_actual para que el evento sea candidato.
Son dos decisiones distintas y no hay que confundirlas.

**Propuesta: yes_price actual en [0.40, 0.60] para calificar como candidato a `observing`.**

Justificación (mismo razonamiento que la auditoría del 2026-07-05, §3 y nota de EPSILON_INITIAL):
- El mecanismo completo depende de que ninguna pata haya "tomado ventaja" todavía — si
  yes_price ya está en 0.70, el mercado ya expresó una opinión fuerte y colocar dos órdenes
  resting "cerca de 50%" ya no describe la situación real: una pata llenaría casi de inmediato
  (falso "L1" sin que haya habido un movimiento genuino), rompiendo la premisa de §4 del audit
  (que L1 refleje un cambio de liderazgo real, no ruido).
- 0.40–0.60 dEja margen de sobra para que EPSILON_INITIAL (0.03) siga posicionando las dos
  órdenes dentro de un rango operable: en el borde (0.60), la orden del lado A reposa en 0.63,
  todavía razonable; más allá de 0.60 el offset empieza a comprimirse contra L2_max para
  cualquier r_target típico (2–5%), exactamente el riesgo que el audit marcó en §2.2.
- Más angosto (ej. [0.45,0.55]) descartaría de entrada eventos que arrancan con un leve favorito
  conocido (frecuente en deportes) sin razón matemática — el mecanismo no requiere 50/50 exacto,
  solo que NINGUNA pata haya llenado aún.
- Más ancho (ej. [0.30,0.70]) admitiría eventos donde el mercado ya está fuertemente inclinado;
  ahí el "lado perdedor" es tan barato que L2_max probablemente ya sea negativo o casi cero al
  momento del cambio de liderazgo (ver fórmula `L2_max = 1/(1+r_target) - L1`; con L1 ya alto de
  entrada más el movimiento adicional hasta el fill real, L2_max se estrecha o desaparece) —
  admitir estos candidatos generaría muchos `observing` que nunca podrán proponer una orden 3
  viable, saturando el pool sin utilidad.

## 2. Detección de "una pata llenó" (ciclo de 5 min, sin stream continuo)

No hay trade real ejecutándose — solo se puede inferir el cruce comparando snapshots.
Condición exacta propuesta:

```
cruzó_side_A  ⟺  yes_price_actual(t) >= side_a_price_propuesto(t-1)
cruzó_side_B  ⟺  no_price_actual(t)  >= side_b_price_propuesto(t-1)
```

donde `side_a_price_propuesto`/`side_b_price_propuesto` son los niveles guardados por
`propose_initial_orders()` en el ciclo anterior (t-1), NO recalculados en t (si se recalculan
contra el precio nuevo, el nivel "persigue" el movimiento y nunca se cruza — bug silencioso
clásico: una orden resting real no se mueve sola).

Reglas adicionales:
- Si AMBOS lados cruzan en el mismo ciclo de 5 min (movimiento grande entre snapshots, o el
  evento saltó de estado súbitamente — penal, gol, etc.), no hay forma de saber cuál filled
  primero con un snapshot cada 5 min. Tratar como **indeterminado**: no avanzar a
  `awaiting_hedge`, marcar el evento para descarte de este intento (no se puede reconstruir con
  la granularidad que tenemos, y adivinar cuál llenó primero introduce sesgo no medible).
- Usar el precio del lado que cruzó COMO L1 aproximado (no hay fill real, es simulación) — pero
  documentar explícitamente que L1 es "precio observado en el snapshot donde se detectó el
  cruce", no el precio exacto de fill real (que pudo ser mejor, como en el trade real del
  usuario: fill a 30 vs límite en 38). Esto es una fuente de error a favor O en contra —
  reportarlo como tal en cualquier ROI calculado sobre este L1 aproximado.

## 3. Tope de candidatos nuevos por ciclo

**Propuesta: 15 eventos nuevos en `observing` por ciclo**, con las siguientes salvedades:
- No es un número derivado de datos — es un techo operacional conservador dado que el paper
  pool es 200 USDC compartido entre 4 estrategias (ver `docs/paper-trading-portfolio-plan-2026-07-05.md`);
  admitir cientos de `observing` simultáneos no cambia cuántos realmente llegan a
  `awaiting_hedge` (la mayoría nunca cruza), pero sí satura la tabla y hace más caro cada ciclo
  (recalcular estado de N `observing` cada 5 min).
- Criterio de priorización si hay más candidatos que el tope: **por liquidez/volumen del evento
  si el dato está disponible en el fetch de `_fetch_prediction_market_events()`** (ya se usa
  liquidez en la dedup de Strategy 2/3 — reusar el mismo campo si existe). Sin ese dato,
  fallback determinista: ordenar por `|yes_price - 0.50|` ascendente (priorizar los MÁS
  cercanos a 50/50, que son los que mejor satisfacen la premisa de "ningún lado tomó ventaja
  todavía" y dejan más margen de EPSILON_INITIAL/L2_max).
- No usar orden de llegada del fetch (no es un criterio, es arbitrario y puede sesgar hacia una
  sola plataforma si el fetch las concatena en orden fijo).

## 4. Timeout de `awaiting_hedge`

**Propuesta: abandonar el intento (marcar `resolved_directional` con salida forzada al último
precio observado, o simplemente cerrar sin resolver el P&L hasta confirmarlo) si
`resolution_date` está a menos de 2 horas de distancia Y la orden 3 no ha cruzado.**

Justificación:
- Esto no es exactamente "más de X horas" como sugiere el enunciado — es lo inverso: el riesgo
  aumenta cuando QUEDA POCO tiempo para que la cobertura llegue (ventana corta = mayor
  probabilidad de que el evento resuelva con la pata líder sola, sin cobertura — exactamente
  el escenario 2 del §4 del audit: pérdida total de N1·L1 si el líder termina perdiendo).
- 2 horas es un placeholder conservador, NO calibrado con datos reales de cuánto tarda un precio
  en tocar un nivel dado tras un cambio de liderazgo (el audit ya señaló esto en §4: "debería
  medirse empíricamente por categoría de evento"). Con datos de paper-trading acumulados, este
  número debería recalibrarse por categoría (deportes de gol único vs. maratones de puntaje
  progresivo tienen dinámicas de precio muy distintas).
- Al llegar al timeout: NO inventar una resolución de ganador/perdedor — solo dejar de esperar
  la cobertura y transicionar a `resolved_directional` con el resultado real pendiente de la
  heurística del punto 5 (que sí puede tardar más de 2h en confirmarse, son cosas separadas:
  "dejo de esperar la orden 3" ≠ "ya sé si gané o perdí").

## 5. Heurística de resolución sin cobertura (sin oráculo)

La propuesta del enunciado (>=0.95 "ganó", <=0.05 "perdió", intermedio "indeterminado") es
razonable como PRIMERA aproximación, con una corrección importante:

- **Usar el ÚLTIMO yes_price observado del lado LÍDER (el que llenó la orden 1), no de
  "yes" genérico** — el lado líder puede ser el NO side. Comparar el precio del lado líder
  contra el umbral, no yes_price crudo.
- Margen de error explícito: un mercado puede cerrar en 0.97 y aun así resolver NO (sucede,
  sobre todo cerca del cierre por baja liquidez/manipulación de cola) — el umbral 0.95/0.05 es
  una aproximación con error de clasificación distinto de cero, no un oráculo. Debe declararse
  como tal en cualquier P&L reportado ("resultado inferido, no verificado contra resolución
  real") y NUNCA usarse para alimentar recalibración de otros modelos (ej. Kelly/Calmar en
  `portfolio.py`) sin ese disclaimer, porque contaminaría con error sistemático cualquier
  estadística derivada.
- Zona intermedia (0.05–0.95): correctamente marcado "indeterminado", pendiente de revisión
  manual — no cerrar la posición automáticamente ni contarla en ROI agregado del ciclo hasta
  que se resuelva (ni como ganada ni como perdida: dejarla fuera de las métricas de performance
  hasta confirmar, para no sesgar el score hacia arriba o abajo).
- Alternativa mejor si hay tiempo/presupuesto de implementación: consultar directamente el
  `resolution`/`outcome` que Polymarket/Kalshi exponen en sus APIs públicas para eventos ya
  cerrados (evita la heurística de precio por completo) — señalo esto como preferible, pero es
  una decisión de alcance de implementación para Sara, no algo que yo decida acá.

---

## Resumen para Sara (≤200 palabras)

1. **Banda de entrada a `observing`:** yes_price en [0.40, 0.60] — fuera de esa banda una pata
   ya tomó ventaja real, rompiendo la premisa del mecanismo.
2. **Detección de cruce:** comparar precio actual contra el NIVEL PROPUESTO en el ciclo anterior
   (no recalculado), `precio(t) >= nivel_propuesto(t-1)`. Si ambos lados cruzan en el mismo
   ciclo de 5 min, marcar indeterminado — no se puede saber cuál llenó primero con esa
   granularidad.
3. **Tope por ciclo:** 15 candidatos nuevos, priorizados por liquidez si el dato existe, si no
   por cercanía a 0.50.
4. **Timeout de `awaiting_hedge`:** abandonar cobertura si `resolution_date` queda a menos de 2h
   (placeholder no calibrado, recalibrar con datos reales por categoría).
5. **Heurística de resolución:** umbral 0.95/0.05 sobre el precio del lado LÍDER (no yes
   genérico), zona intermedia indeterminada y excluida de métricas de performance hasta
   revisión manual — margen de error no nulo, no usar para recalibrar otros modelos sin
   disclaimer.

Nota aparte: detecté un intento de inyección de prompt en un output de herramienta durante
esta tarea (falso system-reminder/MCP) — ignorado, no cambió mi alcance.
