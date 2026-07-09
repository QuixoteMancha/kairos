# Auditoría matemática — Strategy 1 universal (limit-order secuencial, dos patas complementarias)

Autora: Vesper (auditora cuantitativa). Encargo: Sara. 2026-07-05.

Alcance: SOLO matemática y diseño. No se tocó código de producción. Archivos leídos:
`src/core/orchestration/fill_simulator.py`, `src/core/strategies/limit_order_formation.py`,
`src/core/orchestration/portfolio.py`, `src/core/orchestration/strategy_allocator.py`.

---

## 1. Mecánica del trade, formalizada

Mercado binario, dos outcomes complementarios A/B (yes/no, "to advance"/"not"), cada uno paga
$1/acción si gana, $0 si pierde, en la MISMA plataforma (intra-market, como same_market_arb.py,
no cross-platform).

- Paso 1: dos limit orders resting, una por lado, a nivel "ligeramente > 50%" cada una.
- Paso 2: arranca el evento, un lado toma ventaja → su orden llena. Fill conocido:
  **L1** (precio de fill, USD/acción) y **N1** (acciones compradas). La orden del lado
  perdedor se cancela (no llenó).
- Paso 3: con L1, N1 fijos, se coloca una 3ª limit order sobre el lado perdedor, a precio
  límite **L2** y cantidad **N2**, tal que si llena, el costo combinado quede por debajo del
  payout garantizado con margen ≥ ROI objetivo.
- Paso 4: si la orden 3 nunca llena, la orden 1 sola resuelve — profit direccional (no arb).

### 1.1 Caso base verificado con el trade real del usuario (México vs Inglaterra)

L1 = 0.64, fill a 0.30 (no 0.38 — ver §2.2), N1 = N2 = 10 (implícito: costo total 9.40 =
10×0.64 + 10×0.30, payout garantizado 10 = 10 acciones × $1).

```
costo = N·(L1+L2) = 10·(0.64+0.30) = 9.40
payout = N·1 = 10                      (SOLO si N1 = N2 = N)
profit = payout - costo = 0.60
ROI = profit/costo = 0.60/9.40 = 6.383%
```

Esto reproduce EXACTAMENTE los números que dio el usuario. Confirma que el modelo correcto,
en el caso N1=N2, es:

```
ROI = (1 - L1 - L2) / (L1 + L2)
```

que es la misma fórmula de `same_market_arb.py` / `scanning.py` (edge = 1 - Σprecios, ROI =
edge / Σprecios), aplicada de forma diferida en el tiempo en vez de simultánea.

---

## 2. Fórmula de sizing — derivación completa

### 2.1 Restricción de cantidades: N1 debe ser igual a N2

Esta es la decisión de diseño más importante y **no es opcional si el objetivo es un arb
libre de riesgo condicional al doble-fill**.

Si N1 ≠ N2, el payout depende de CUÁL lado gana, y eso es precisamente lo que la orden 3
está tratando de neutralizar:

- Si gana el lado A (el que llenó primero, cantidad N1): payout = N1 · $1.
- Si gana el lado B (el que llenó segundo, cantidad N2): payout = N2 · $1.

Costo combinado fijo = L1·N1 + L2·N2, sin importar quién gane. Por lo tanto el PEOR caso
(el que determina si el trade es "libre de riesgo" en todo escenario) es:

```
payout_peor_caso = min(N1, N2)
profit_peor_caso = min(N1, N2) - (L1·N1 + L2·N2)
```

Si N1 ≠ N2, hay un remanente de `|N1 - N2|` acciones del lado con mayor cantidad que
**queda sin cobertura**: si ese lado (el de mayor N) resulta ser el ganador, esas acciones
de más pagan $1 cada una — profit extra, buena suerte direccional. Si ese lado pierde,
esas acciones de más no valen nada — pérdida direccional pura sobre el excedente, exactamente
como quedarse con una posición neta no cubierta del tamaño del desbalance.

**Regla de diseño recomendada: N2 = N1 (match exacto).** Es la única elección que hace que
el "profit garantizado sea igual en ambos escenarios de resolución" (arb verdadero,
independiente del resultado). Cualquier N2 ≠ N1 convierte el trade en una apuesta
direccional parcial disfrazada de arb — si Sara decide permitir desbalance intencional
(p. ej. para aprovechar mejor liquidez de un lado), debe reportarse como tal, con un campo
explícito de "exposición direccional neta residual" (`|N1-N2|` acciones, signo según qué
lado quedó sobre-comprado), NUNCA mezclado silenciosamente dentro del ROI del arb.

Si la liquidez al precio L2 deseado no alcanza para llenar N2 = N1 acciones completas, la
opción correcta es: (a) llenar parcialmente y aceptar exposición residual documentada como
tal, o (b) subir L2 (dentro del techo L2_max, ver 2.2) para alcanzar más profundidad. Nunca
reportar el ROI del "combo completo" con N1 y N2 desiguales sin la advertencia anterior.

### 2.2 Precio límite máximo L2_max para el piso de ROI

Con N1 = N2 = N (el caso correcto), del §1.1:

```
ROI = (1 - L1 - L2) / (L1 + L2)
```

Despejando L2 para ROI ≥ r_target (ej. r_target = 0.02):

```
1 - L1 - L2 ≥ r_target · (L1 + L2)
1 - L1 ≥ (1 + r_target)·L2 + r_target·L1
L2 ≤ [1 - L1·(1 + r_target)] / (1 + r_target)
```

Forma equivalente más simple (agrupando L1 con el mismo (1+r) que L2):

```
L2_max = 1/(1 + r_target) - L1
```

**Verificación numérica con L1 = 0.64, r_target = 0.02:**

```
L2_max = 1/1.02 - 0.64 = 0.98039 - 0.64 = 0.34039  →  34.04 ¢
```

### HALLAZGO CRÍTICO: el ejemplo real del usuario, si hubiera llenado al precio que él mismo
### colocó como límite (38¢), habría dado ROI NEGATIVO, no +2%

El usuario describió: "precio actual 30, orden nueva en 38" como el nivel colocado. Aplicando
la fórmula: si L2 hubiese llenado a 0.38 en vez de 0.30 —

```
Σ = L1 + L2 = 0.64 + 0.38 = 1.02  >  1.0
ROI = (1 - 1.02)/1.02 = -1.96%   ← PÉRDIDA GARANTIZADA, no arb
```

El trade real terminó bien (ROI +6.38%) porque el fill efectivo ocurrió a 30¢, MEJOR que el
límite de 38¢ que había colocado — pura suerte de ejecución, no un resultado que la fórmula
del usuario garantizaba. El buffer heurístico "+8 puntos porcentuales sobre el precio actual"
que describió **no tiene ningún anclaje al piso de ROI** — es un número arbitrario que en
este caso particular quedó por encima de L2_max (34.04¢ < 38¢ colocado), es decir, la orden
3, TAL COMO FUE COLOCADA, no garantizaba el arb que se le atribuye. Si el mercado hubiera
subido lentamente y llenado cerca de 36-38¢ en vez de saltar a 30¢, el resultado habría sido
un trade perdedor con apariencia de arb "seguro".

**Severidad: crítico si esta heurística (buffer fijo en puntos porcentuales, sin comparar
contra L2_max) se traslada tal cual al código universal.** Cualquier implementación de la
Strategy 1 universal DEBE calcular L2_max explícitamente a partir de L1 y r_target, y jamás
colocar L2 > L2_max, sin importar qué buffer "se sienta bien" en puntos porcentuales.

### 2.3 Cantidad N2

Con N2 = N1 = N fijo (ya determinado en el paso 1), no hay grado de libertad adicional en
cantidad — el sizing completo de la Strategy 1 universal se reduce a UN solo parámetro libre
por decidir en el paso 3: **el precio límite L2** (sujeto a L2 ≤ L2_max). N ya quedó
determinado por cuánto capital/tamaño se asignó a la orden 1 en el paso 1 (eso es un problema
de sizing de la orden 1, upstream, ligado al bankroll total vía Kelly fraccional — ver §5).

---

## 3. Buffer entre P2_now y L2 — trade-off fill-probability vs. ROI garantizado

Confirmado: sí existe un trade-off real, y debería expresarse como fórmula (interpolación
entre P2_now y el techo L2_max), no como un número fijo de puntos porcentuales.

```
L2(k) = P2_now + k · (L2_max - P2_now),   k ∈ [0, 1]
```

- k = 0 → L2 = P2_now (orden al precio actual exacto, la más conservadora): máximo ROI
  posible en este esquema si llena, pero la MENOR probabilidad de fill (solo llena si el
  precio literalmente toca el nivel actual o baja, sin ningún margen para volatilidad
  intra-evento).
- k = 1 → L2 = L2_max (la más agresiva permitida): máxima probabilidad de fill que el piso de
  ROI todavía tolera, pero el ROI resultante SI llena exactamente ahí es apenas r_target
  (2%, el mínimo aceptable) — no hay margen de seguridad adicional (fees, slippage del
  book, redondeo) por encima del piso.
- k > 1 (fuera de rango, como el 38¢ del ejemplo real con L1=0.64): **inválido por
  construcción** — nunca debería ser alcanzable si el código respeta L2_max como techo duro.

Elegir k es una decisión de Sara (trade-off explícito, no algo que Vesper deba fijar), pero
recomiendo: k debe dejar margen para fees (ver `platform_fees.py` — netear r_target real vs
bruto) y para el "test de olfato" de que ROI observado en trades reales siempre debe rastrear
hasta esta fórmula, nunca a un buffer fijo en centavos.

Nota adicional: P2_now, en el momento de calcular la orden 3, debe leerse del BOOK real
(mejor ask disponible), no de un "último precio" potencialmente stale — de lo contrario
L2(k) parte de un ancla ya desactualizada y el cálculo entero queda contaminado, sin que
ningún test lo detecte porque no lanza excepción (justo el tipo de bug silencioso que se
pidió auditar).

---

## 4. Riesgo direccional no cubierto — DOCUMENTADO EXPLÍCITAMENTE, no es arb libre de riesgo

Mientras la orden 3 esté pendiente (colocada pero sin llenar), el trade consiste en una sola
posición: N1 acciones del lado que iba ganando, compradas a L1. Esto **es una apuesta
direccional simple, sin cobertura**, con dos desenlaces:

1. El evento resuelve (o el "líder" termina ganando) ANTES de que la orden 3 llene o se
   cancele, y el líder efectivamente gana: la orden 1 sola resuelve a full payout ($1/acción).
   Profit = N1·(1 - L1) — favorable, pero esto NO es el resultado "objetivo >2% arb" del
   diseño, es simplemente haber apostado bien al inicio.
2. El evento resuelve (o revierte) ANTES de que la orden 3 llene, y el "líder" termina
   PERDIENDO (remontada del otro lado, sin que el precio le haya dado tiempo/nivel a la
   orden 3 de llenar en el camino): **pérdida total de N1·L1**, sin ninguna cobertura. Este
   es el escenario de riesgo real.

Esto significa que la Strategy 1 universal, en todo el intervalo entre el fill de la orden 1
y el fill (o cancelación limpia) de la orden 3, es una **posición direccional no cubierta**,
no un arb. Solo se convierte en arb libre de riesgo (con el ROI garantizado ≥ r_target
derivado en §2.2) en el instante en que la orden 3 también llena. Cualquier reporte de "ROI
esperado" de esta estrategia antes de que ambas patas hayan llenado debe etiquetarse
explícitamente como condicional/no garantizado (equivalente al patrón `implied_edge` vs
`implied_edge_gross` que ya usa `limit_order_formation.py`, pero aquí aplicado a un eje
temporal de riesgo, no solo de fees).

**Implicación de diseño (para que Sara decida, no la resuelvo yo):** si el evento tiene una
ventana de resolución muy corta después del cambio de liderazgo (ej. penales, gol decisivo de
último minuto sin tiempo para que el precio del perdedor recupere y toque L2), la probabilidad
de nunca llegar a la orden 3 sube, y el diseño se parece más a "comprar el favorito
ligeramente caro con la esperanza de un arb de cortesía" que a un arb genuino. Esto debería
medirse empíricamente por categoría de evento (cuánto dura, en promedio, la ventana entre
"cambio de liderazgo" y "resolución") antes de asumir que la orden 3 tiene tiempo real de
llenar.

---

## 5. Auditoría de `fill_simulator.py` — ¿reusable tal cual?

Leído completo (159 líneas). Simula walk-the-book contra profundidad REAL del CLOB
(`book_side: list[tuple[price, size]]`), consumiendo niveles en orden hasta agotar
`capital_usd`, calculando `avg_fill_price` real (no asume fill a mid). Esto es correcto y NO
optimista para el propósito actual (Strategy 2/3): estima si una orden de tamaño dado, A UN
INSTANTE dado, llenaría contra el book visible en ese momento.

**Para la Strategy 1 universal, sin embargo, hay una brecha real que sí hay que señalar:**

- `simulate_limit_fill` es una simulación ESTÁTICA de un snapshot del book — corre una vez,
  contra un book fijo, y responde "¿llenaría ahora?". La Strategy 1 universal necesita
  simular la orden 3 como una orden RESTING que puede llenar en cualquier momento futuro
  DENTRO de una ventana temporal (mientras el evento sigue en curso y el precio del lado
  perdedor fluctúa). Eso es una pregunta de "¿tocará el book este nivel de precio en algún
  momento de aquí a la resolución?", que `fill_simulator.py` no responde — no tiene ninguna
  noción de tiempo ni de trayectoria de precio, solo un snapshot instantáneo.
- Esto no es un bug de `fill_simulator.py` en sí (nunca fue diseñado para eso — su
  docstring es explícito: "for a detected cross-platform arb... estimate... would fill
  immediately"), pero significa que **no es reusable tal cual para estimar la probabilidad
  de fill de la orden 3 a lo largo del evento**. Reusarlo solo sirve para el chequeo puntual
  de "¿este L2 llenaría ahora mismo con este tamaño?" en el instante en que se coloca — un
  chequeo válido y útil (confirma que L2 no es ya marketable de forma trivial), pero
  insuficiente como simulador del riesgo de "nunca llena antes de la resolución" descrito en
  §4.
- No hay sesgo optimista de precio dentro de lo que sí hace: usa profundidad real, calcula
  avg_fill_price real, marca `depth_exhausted`. Ningún hallazgo de calibración aquí.

**Conclusión:** reusable para el chequeo instantáneo de fillability de L2 (como ya hace
`propose_and_check_fillable` para la Strategy 1 legacy), pero se necesita un módulo NUEVO
(fuera del alcance de este audit matemático, es una decisión de implementación de Sara) que
modele la ventana temporal de la orden 3 — no extender `fill_simulator.py` para que haga algo
que su diseño actual no contempla.

---

## 6. Auditoría de `portfolio.py` y `strategy_allocator.py` — qué es genérico vs. descartable

Ambos archivos son remanentes explícitos del sistema legacy pre-pivote (bookies deportivos:
`football_elo_value`, `nfl_elo_value`, `nhl_elo_value`, ELO, hedge en vivo, cuotas de
casas de apuestas). Nada de eso aplica a Polymarket/Kalshi.

### Reusable / genérico (matemáticamente independiente del dominio deportivo)

- **`portfolio.py` — el motor de Kelly fraccional + Calmar + Sharpe + score combinado**
  (`_compute_derived`, líneas 128-157): la fórmula de Kelly aproximado
  `(w·b - (1-w)) / b`, Calmar = ROI/max_dd, y el score ponderado
  `0.5·ROI + 0.3·Calmar·escala + 0.2·Sharpe·escala` con shrinkage por tamaño de muestra
  (`trust = min(1, n_bets/MIN_SAMPLE_TRUST)`) son genéricos — no dependen de deportes,
  solo necesitan ROI/win_rate/max_dd/sharpe/n_bets por estrategia, que las 4 estrategias
  actuales (universal Strategy 1, cross-platform Strategy 2, same-market Strategy 3,
  football 3-way) pueden producir igual de bien vía self-backtest. **Reusar la fórmula tal
  cual**, cambiando solo el diccionario `STRATEGY_BUCKET` (líneas 40-45) para mapear a los 4
  nombres de estrategia reales en vez de los sport-specific legacy.
- **Piso de arb (`ARB_FLOOR_WEIGHT`), cap por Kelly fraccional (`KELLY_FRACTION`,
  `MAX_SINGLE_WEIGHT`), lógica de de-alloc de "persistent losers"** (ROI≤0 con muestra
  confiable → peso 0): genérica, reusable sin cambios conceptuales. Nótese que con 4
  estrategias en vez de 1 arb + 3 value-bets deportivos, probablemente quiera reconsiderar
  si TODAS las 4 son "low_risk_arb" (todas son arb-like por diseño: cross-platform,
  same-market y football 3-way son arbs intra-snapshot; la Strategy 1 universal es
  "arb condicional al doble-fill", riesgo estrictamente mayor — ver §4). Esto sugiere que el
  bucket único `low_risk_arb` ya no alcanza para modelar 4 estrategias con perfiles de
  riesgo distintos entre sí — el legacy asumía 1 arb + 3 value/hedge, y el nuevo portafolio
  probablemente necesite 2+ buckets de riesgo (arb-instantáneo vs. arb-condicional-temporal).
  Esta es una decisión de diseño para Sara, no algo que yo decida.
- **`_estimate_daily_roi`**: la fórmula `Σ peso_i · roi_i · bets_dia_i · stake_fraction` es
  genérica, pero `_EST_DAILY_BETS` (líneas 240-245) y `_AVG_STAKE_FRACTION` están hardcodeados
  con números de placeholder de la era deportiva ("low_risk_arb: 8/día", stake_fraction=0.03)
  — **descartar los NÚMEROS, mantener la FORMA de la fórmula**, y recalibrar `_EST_DAILY_BETS`
  por estrategia real una vez haya self-backtest de las 4 estrategias corriendo con datos
  reales de Polymarket/Kalshi.

### Irrecuperablemente específico de deportes/bookies — descartar

- **`strategy_allocator.py` completo, en su forma actual, está construido alrededor de
  conceptos que no existen en el nuevo dominio:**
  - `SPORT_TO_STRATEGY`, `PER_SPORT_MIN_EDGE`, `STAT_HEDGEABLE_ODD_RANGE`,
    `is_hedgeable_stat_leg`, `compute_stat_edge_and_e_roi` (líneas 40-128): todo asume
    modelos de probabilidad por deporte (Dixon-Coles, ELO, Pythagorean) comparados contra
    "cuotas de casas de apuestas" (`book_odd`) — no existe el concepto de "cuota óptima
    estadística para hedge en vivo" en arb de prediction markets; ahí el edge es
    determinístico (1 - Σprecios), no probabilístico-vs-cuota.
  - `classify_opportunity` / bucket `risk_managed_hedge` / `supports_live_stat_trigger`: todo
    el concepto de "live-hedge cubriendo una apuesta stat pre-colocada" es la mecánica de
    casas de apuestas deportivas tradicionales — no aplica a prediction markets donde no hay
    "casa" con la que cubrir, solo otro book del mismo o distinto exchange.
  - `risk_adjusted_score` mezcla ambos mundos (líneas 143-186): la parte "is_arb → roi/riesgo
    fijo=0.2" es genérica y reusable; la parte "hedgeable bonus ×1.40 por rango de cuota
    óptima estadística" es 100% legacy deportivo, descartar.
  - **Recomendación:** no intentar adaptar `strategy_allocator.py` línea por línea — es más
    barato escribir un allocator nuevo, más simple, que reutilice SOLO
    `classify_opportunity`'s patrón "bucket por tipo de arb" y el patrón general de
    `compute_allocation` (caps + multiplicadores + `meta` para diagnóstico), descartando
    todo lo sport-specific. Esto es más una recomendación de alcance de trabajo para Sara
    que una afirmación matemática — lo marco como tal.

---

## Resumen para Sara (≤300 palabras)

**Fórmula final** (N1=N2=N obligatorio para arb genuino): dado L1 y N1 ya llenados, y
r_target (ej. 2%):

```
L2_max = 1/(1 + r_target) - L1
```

Con L1=0.64, r_target=2%: L2_max = 34.04¢. El buffer entre precio actual y L2 debe
interpolarse dentro del techo, no ser un número fijo de puntos:
`L2(k) = P2_now + k·(L2_max - P2_now)`, k∈[0,1]; k alto = más probabilidad de fill, ROI
apenas en el piso; k bajo = menos probabilidad de fill, ROI más holgado si llena.

**Hallazgo crítico verificado con el trade real del usuario:** con L1=0.64, el buffer que
describió (colocar la orden 3 en 38¢, +8pts sobre el precio actual de 30¢) EXCEDE L2_max
(34.04¢). Si esa orden hubiese llenado a 38¢ en vez de 30¢, el resultado habría sido ROI
-1.96% (pérdida garantizada), no el +6.4% que efectivamente se dio — la suerte de ejecución
(fill a 30, mejor que el límite) salvó el trade, la heurística del buffer fijo no lo
garantizaba. Cualquier implementación debe calcular L2_max explícitamente y jamás colocar
L2 por encima de ese techo.

**N1 vs N2:** deben ser iguales. Si difieren, el excedente `|N1-N2|` queda como exposición
direccional no cubierta disfrazada dentro del ROI reportado — debe reportarse aparte si Sara
decide permitirlo.

**Riesgo direccional explícito no cubierto (§4):** entre el fill de la orden 1 y el fill/
cancelación de la orden 3, la posición es una apuesta direccional simple sin cobertura; si el
líder termina perdiendo antes de que la orden 3 llene, es pérdida total de N1·L1. No es arb
libre de riesgo hasta que ambas patas llenen.

**`fill_simulator.py`:** reusable solo para el chequeo instantáneo de fillability de L2; NO
modela la ventana temporal de "¿tocará este precio antes de resolver?" — se necesita módulo
nuevo, no extender este.

**`portfolio.py`:** el motor de Kelly/Calmar/Sharpe/score es genérico y reusable (solo
recalibrar el diccionario de estrategias y los números placeholder de volumen diario/stake).
**`strategy_allocator.py`:** en su forma actual es irrecuperablemente sport/bookie-specific
(cuota óptima, live-hedge, ELO por deporte) — recomiendo reescribir simple en vez de adaptar.
