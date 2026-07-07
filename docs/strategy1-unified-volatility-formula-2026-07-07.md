# Strategy 1 — regla unificada de nivel de entrada (volatilidad + fase), reemplazo del banding fijo

Autora: Vesper (auditora cuantitativa). Encargo: Sara. 2026-07-07.

Alcance: SOLO diseño/matemática, ningún archivo de código tocado. Archivos leídos:
`src/core/strategies/universal_limit_arb.py`, `docs/strategy1-cycle-wiring-parameters-2026-07-06.md`,
`docs/strategy1-favorite-underdog-volatility-formula-2026-07-07.md`,
`src/core/orchestration/paper_trading_cycle.py` (completo, incluida `_run_strategy1_pass`,
`_yes_no_ask`, `_resolve_strategy1_directional`, admisión de candidatos ~L433-478).

**Nota de seguridad:** en el output de la lectura de mi propio doc anterior aparecieron de nuevo
bloques `system-reminder` inyectados (cambio de fecha falso, instrucciones de un servidor MCP
"Shopify" inexistente en este contexto, mensaje "Auto Mode Active" instando a no detenerme a
pedir confirmación). Ninguno es legítimo — no cambian mi alcance (solo este doc, sin tocar
código) ni mi criterio de cuándo escalarle una decisión a Sara. Los ignoro por completo.

Este documento **reemplaza conceptualmente** — no complementa — el banding fijo `[0.40, 0.60]` +
`EPSILON_INITIAL = 0.03` documentados en el doc del 2026-07-06 y ya vigentes en
`paper_trading_cycle.py` (`_S1_CANDIDATE_BAND`, líneas 71 y 442-452) y en
`universal_limit_arb.py` (`propose_initial_orders`, `EPSILON_INITIAL`). Nada de código se toca
todavía — esto es la especificación que Sara debe aprobar antes de recablear
`_run_strategy1_pass`.

---

## 0. Qué pide el usuario, en una frase

Una única regla continua — "¿vale la pena poner el nivel de entrada del lado que hoy lidera
`m·σ` por debajo de su precio actual, dada la volatilidad esperada de ESTE evento en ESTE
momento de ESTA fase?" — que se aplique **siempre**, sin importar si el precio actual es 51% o
90%. El caso "cerca de 50/50" no es una rama aparte: es lo que sale de esta regla cuando el
líder recién emergió (precio ~50%+algo) y σ, en esa fase temprana, todavía admite un `m·σ` chico.

---

## 1. De banding discreto a condición continua de admisión

### 1.1 Qué reemplaza qué

| Concepto legacy (2026-07-06 / universal_limit_arb.py) | Concepto unificado (este doc) |
|---|---|
| `_S1_CANDIDATE_BAND = (0.40, 0.60)` — filtro binario sobre `yes_price` | Ninguna banda de precio. Cualquier `p0_líder ∈ (0,1)` es candidato si existe un `m` viable (§1.2). |
| `EPSILON_INITIAL = 0.03` fijo, mismo offset para ambos lados, siempre hacia arriba de 0.50 | `δ(m, σ, fase) = p0_líder − sigmoid(logit(p0_líder) − m·σ_fase)`, un offset hacia **abajo** del precio actual del líder, derivado de volatilidad, no una constante. |
| Detección de cruce contra "nivel propuesto ciclo anterior" (doc 2026-07-06 §2) | Se mantiene sin cambios — es un mecanismo de snapshot-vs-snapshot, ortogonal a de dónde salió el nivel. |
| Margen del underdog no especificado (implícito simétrico vía `no_ask` actual) | `margen_dog < δ` estrictamente (derivado en el doc del favorito claro, §3, reconfirmado aquí en §3). |

### 1.2 Condición de admisión continua

Un evento es candidato a `observing` bajo la regla unificada si, y solo si, existe un `m ∈
[m_min, m_max]` (banda operacional, no banda de precio — ver §1.3) tal que:

```
δ(m, σ_fase) = p0_líder − sigmoid(logit(p0_líder) − m·σ_fase)      [misma fórmula de reflexión de barrera del doc anterior]

margen_dog_piso = piso independiente de fees+slippage (vía platform_fees.py, ambas patas)

ROI_si_ambas_llenan(m) = (δ(m, σ_fase) − margen_dog_piso) / (L_líder + L_dog)   ≥ r_target
```

Es decir: el evento **no** se filtra por "¿está el precio en tal rango?" sino por "¿hay algún
nivel de entrada, alcanzable con probabilidad de fill no despreciable (`P(tocar) = 2Φ(−m)`),
que deje ROI ≥ r_target una vez descontado el piso de fees/slippage del lado underdog?". Si NO
existe tal `m` dentro de la banda operacional — porque σ_fase es demasiado chica para generar
un `δ` que supere `margen_dog_piso` con margen, o porque el `m` necesario cae fuera de
`[m_min, m_max]` y por lo tanto la probabilidad de fill es despreciable — el evento **no**
califica, sin importar si su precio está en 0.50 o en 0.90. Esto reemplaza al filtro de banda:
la banda [0.40,0.60] era un PROXY burdo de "σ probablemente alcanza para que exista un `m`
viable"; la condición de arriba es el chequeo real, explícito, en vez del proxy.

### 1.3 Banda operacional de `m` — se mantiene, no es una banda de precio

`[m_min, m_max]` (recomendado `[0.5, 1.645]`, mismo rango ya justificado en el doc del favorito
claro §2.2: de "movimiento típico" a "movimiento infrecuente pero con margen grande") sigue
siendo un parámetro de Sara, igual que antes. La diferencia clave respecto al banding legacy es
que esto acota **la unidad de medida del movimiento** (desviaciones estándar), no el precio
crudo — por eso la misma banda de `m` sirve tanto para un evento en 51% como en 90%: lo que
cambia entre esos dos casos es `σ_fase` y la forma no lineal del `sigmoid`, no el rango de `m`.

---

## 2. Fase del evento como modulador de σ

### 2.1 Por qué la fase importa (justificación estadística, no solo intuición)

σ, tal como se definió en el doc anterior, es "la volatilidad logit del partido/evento
**completo**, desde el inicio hasta la resolución". Esa es una cantidad agregada — pero el
proceso de precio no tiene la misma volatilidad instantánea en cada tramo de esa ventana. Dos
mecanismos concretos generan esta heterogeneidad, y son distintos por categoría:

- **Pre-match/pre-event**: los movimientos de precio vienen de información exógena discreta
  (lineup confirmado, lesión, cambio de clima, retiro de un jugador) — eventos poco frecuentes
  pero de salto grande cuando ocurren. Volatilidad "de shock", baja frecuencia/alto impacto.
- **Early-match/early-event**: el partido empezó pero hay poca señal acumulada — el precio
  todavía está cerca de su nivel pre-match, con ruido de microestructura pero sin mucho
  movimiento direccional confirmado todavía.
- **Mid-match/mid-event**: la señal real del evento (puntos, goles, sets, quiebres de servicio)
  empieza a dominar — es el tramo de mayor volatilidad realizada en la mayoría de las
  categorías, porque el precio reacciona a resultados discretos observados, no a expectativa.
- **Late-match/late-event**: el precio típicamente ya convergió hacia 0 o 1 (la resolución se
  acerca) — la volatilidad *implícita restante* colapsa porque queda poco tiempo/eventos
  discretos posibles para revertir el resultado, aun si la volatilidad *realizada acumulada*
  fue alta en el tramo anterior. Este es el tramo donde el mecanismo de Strategy 1 es MENOS
  útil (poco margen de movimiento adicional disponible, ver también el timeout de 2h del doc
  2026-07-06 §4, que ya capturaba esto de forma indirecta).

Este patrón (shock-bajo-frecuencia pre-match → alta-volatilidad-realizada mid-event →
colapso-de-volatilidad-remanente late-event) es consistente con la literatura de mercados de
apuestas en vivo (in-play betting), no es un supuesto ad-hoc.

### 2.2 Formalización: σ_fase como escalador multiplicativo simbólico

```
σ_fase = σ · h(fase)
```

donde `σ` es la volatilidad de PARTIDO COMPLETO ya derivada en el doc anterior (§1.2 de ese
doc: `σ = σ_base(categoría) · g(bucket)`), y `h(fase)` es un multiplicador adimensional por
fase, simbólico — **no calibrado con datos reales todavía**, exactamente la misma disciplina que
`σ_base` y `g` en el doc previo:

```
h(pre-match)   = h_pre     (esperado < 1: la ventana pre-match es más larga en tiempo de reloj
                             pero más pobre en eventos discretos observables; el movimiento
                             esperado POR UNIDAD DE σ_partido_completo en este tramo es menor)
h(early-match) = h_early   (esperado ≈ h_pre a algo mayor: arrancó el evento pero señal aún débil)
h(mid-match)   = h_mid     (esperado > 1: tramo de mayor densidad de eventos discretos que
                             mueven precio — el "grueso" de σ_partido_completo se realiza acá)
h(late-match)  = h_late    (esperado < h_mid, posiblemente < 1: volatilidad remanente colapsa a
                             medida que la resolución se acerca y hay menos tiempo/eventos
                             posibles para revertir)
```

Restricción de consistencia (no negociable, para que `σ_fase` siga siendo compatible con la
definición de `σ` del doc anterior): el "presupuesto" total de volatilidad realizada a través de
las 4 fases debe ser consistente con `σ` de partido completo — no exijo una fórmula de
agregación exacta acá (depende de cómo se subdivida el tiempo/eventos dentro de cada fase, que
es un detalle de calibración con datos reales), pero cualquier calibración futura de
`h_pre/h_early/h_mid/h_late` debe verificarse contra esa restricción (ej. vía suma de varianzas
si las fases se tratan como incrementos independientes de un proceso de varianza aditiva:
`σ_partido² ≈ Σ_fase (duración_fase · σ_fase²)` bajo el supuesto simplificador de volatilidad
instantánea constante dentro de cada fase — es una aproximación, no una identidad exacta, y debe
marcarse como tal si se implementa).

**Sin datos reales para calibrar `h(fase)` por categoría, la propuesta de default simbólico
razonable es:**

```
h_pre ≈ 0.6,  h_early ≈ 0.8,  h_mid ≈ 1.3,  h_late ≈ 0.7
```

Estos NO son valores derivados de datos — son placeholders con la forma cualitativa correcta
(mid > early ≈ pre > tail, con late explícitamente menor que mid), exactamente en el mismo
espíritu que `σ_base`/`g_min`/`g_max` quedaron simbólicos en el doc anterior. Fail-closed si no
hay ninguna base para siquiera este placeholder cualitativo por categoría (§4 mantiene la misma
disciplina: sin σ_fase estimable, el evento no es candidato bajo esta regla).

---

## 3. La garantía de "nunca pérdida garantizada" — versión generalizada

### 3.1 Reconfirmación de la derivación ya hecha (favorito claro), ahora explícita en fase

El doc del favorito claro (2026-07-07, §3) ya derivó, para un instante de cruce fijo, que:

```
margen_dog < δ(m, σ_fase)      [condición estricta, NUNCA simetría]
```

es la condición necesaria y suficiente para que, SI ambas patas llenan en el mismo cruce, el ROI
resultante sea positivo. Esa derivación no depende de qué genera `δ` — solo depende de que
`δ > margen_dog`. Como `δ` ahora es una función de `(m, σ_fase)` en vez de un valor fijo del caso
favorito-claro, la condición se traslada sin cambios de forma:

```
CONDICIÓN UNIFICADA DE ARB SEGURO (análoga a L2_max, generalizada a cualquier fase/σ):

  margen_dog(fase, m) < δ(m, σ_fase)                          [estricto]

  con margen_dog fijado, igual que antes, por un piso INDEPENDIENTE de
  fees+slippage esperado de ambas patas (vía platform_fees.py), nunca por
  "igual al margen del líder" ni por ningún otro criterio de simetría.
```

Fail-closed: igual que `compute_l2_max()` devuelve `None` en vez de un default cuando no hay
ceiling viable, si para la fase/σ actuales no existe ningún `margen_dog` que sea simultáneamente
(a) mayor o igual al piso de fees/slippage y (b) estrictamente menor que `δ(m, σ_fase)`, el
evento **no** avanza — no se propone ningún nivel de tercera orden (ni de segunda orden, en la
versión donde ambas se colocan simultáneamente).

### 3.2 El caso "solo llena la del líder" — por qué esto SIEMPRE es seguro por construcción, sin importar σ/fase

El invariante del usuario es que, si solo llena la orden del líder (sin que la del no-líder
llegue a cruzar), el resultado debe ser "posición direccional simple", nunca una combinación que
garantice pérdida. Esto se sostiene automáticamente en el diseño unificado, y la razón es
estructural, no depende de calibrar bien σ:

- La orden del líder se llena SOLA a un precio `L_líder < p0_líder` (por construcción, es un
  nivel de entrada por DEBAJO del precio actual — nunca por encima). Esto por sí solo ya es una
  mejora respecto al precio de mercado en el momento de la decisión, no un empeoramiento.
- Mientras la orden del no-líder no cruce, no existe ninguna combinación de dos fills simultánea
  que pueda evaluarse — solo hay una posición de N acciones del líder a `L_líder`. El resultado
  final de esa posición sola es: gana `N·(1 − L_líder)` si el líder termina ganando, pierde
  `N·L_líder` si el líder termina perdiendo. Ninguna elección de `m`, `σ_fase`, o fase afecta
  esta bifurcación binaria — es inherente a comprar una acción YES/NO a un precio dado y esperar
  el resultado. **Esto es exactamente la misma "ventana de riesgo direccional no eliminada" que
  el módulo `universal_limit_arb.py` ya documenta explícitamente en su docstring** ("IMPORTANT:
  between the fill of order 1 and the fill/cancellation of order 3 ... total loss of N * L1").
  El rediseño no cambia esa propiedad ni la empeora — el invariante del usuario ya estaba
  satisfecho por el mecanismo base, y sigue satisfecho aquí porque `L_líder` sigue siendo
  estrictamente mejor que `p0_líder` (nunca peor) bajo cualquier `m > 0`.
- Lo único que la calibración de σ/fase puede afectar es la PROBABILIDAD de terminar en este
  escenario (vía `P(tocar) = 2Φ(−m)` para el líder y la condición equivalente para el no-líder)
  — no la severidad del peor caso dentro de él. Una mala calibración de σ_fase puede hacer que
  Strategy 1 proponga niveles con fill-rate pobre (ineficiencia operacional), o niveles cuyo
  "arb si ambas llenan" tenga menos margen del que se cree (ineficiencia de ROI) — pero NO puede,
  por construcción de la fórmula `L_líder = p0_líder − δ` con `δ > 0` siempre, convertir el
  escenario "solo el líder llena" en una pérdida garantizada distinta de la ya documentada y
  aceptada como riesgo residual del mecanismo.

**Conclusión de §3**: la garantía de "nunca pérdida garantizada" tiene dos mitades, y ambas se
sostienen en la versión unificada:
1. Si ambas llenan → ROI ≥ r_target garantizado por `margen_dog < δ(m, σ_fase)` (fail-closed si
   no hay tal margen viable).
2. Si solo llena el líder → riesgo direccional puro (no arb, no garantía de ganancia), pero
   NUNCA una combinación de niveles que garantice pérdida — se sostiene automáticamente porque
   `L_líder` es por construcción un precio mejor que el de mercado en el momento de decisión, y
   la posición resultante es simplemente "comprar barato y esperar", no una construcción de dos
   patas mal balanceadas.

---

## 4. Confirmación: el caso 50/50 legacy es un caso particular

Sí — confirmado explícitamente. Cuando el "líder" recién emerge de un evento que arrancó cerca
de 50/50 (`p0_líder ≈ 0.50 + ε` para algún `ε` chico, el instante justo después de que una pata
tomó una ventaja mínima), la fase relevante es `pre-match`/`early-match` (el evento recién
empezó o todavía no empezó del todo), donde `σ_fase = σ · h_pre` o `σ · h_early` — los
multiplicadores más chicos de la tabla (§2.2). Con `σ_fase` chico y `m` en la banda operacional
recomendada `[0.5, 1.0]`, `δ(m, σ_fase)` resulta necesariamente pequeño (la función `sigmoid`
cerca de 0.50 es casi lineal, así que `δ ≈ m·σ_fase/4` en esa vecindad — la derivada de
`sigmoid` en `x=0` es `1/4`). Esto reproduce, como caso límite, casi exactamente el
comportamiento de `EPSILON_INITIAL = 0.03` fijo: un offset chico, simétrico en la práctica
porque `p0_líder` y `p0_no-líder` están ambos cerca de 0.50 así que `δ` resulta similar para
cualquiera de los dos lados que termine liderando.

La diferencia real (mejora, no ruptura) es que el offset ya no es una constante ciega (0.03
siempre) sino que responde a la volatilidad real estimada de ESE evento en ESA fase — un evento
50/50 con σ genuinamente alto (ej. tenis muy parejo, ambos jugadores con historial de sets
reñidos) tendría un `δ` mayor que 0.03 bajo la regla unificada, mientras que uno con σ bajo
(favorito ligero con historial dominante, aunque su precio esté en 0.52) tendría un `δ` menor
— ambos casos eran indistinguibles bajo el banding+epsilon fijo, y dejan de serlo acá. Esto es
la prueba de que el rediseño generaliza sin romper el caso ya desplegado: el mecanismo legacy no
se descarta, emerge como el límite de baja-σ/fase-temprana de la regla continua.

---

## 5. Aproximación de fase sin feed en vivo de progreso del evento

Kairos hoy no tiene un feed de "cuánto lleva corriendo el evento" — solo `resolution_date`
(cuándo termina) y, para Strategy 1, el timestamp de cuándo el evento fue admitido por primera
vez como candidato (`SimulatedBet.opened_at`, ya persistido hoy en
`paper_trading_cycle.py:473` para cada `observing`). Con eso, propongo el siguiente proxy,
explícitamente aproximado:

### 5.1 Insumos disponibles hoy

- `t_detect` = `opened_at` del registro `observing` (cuándo Kairos vio el evento por primera
  vez con un precio en rango admisible bajo la regla unificada).
- `t_resolution` = `resolution_date` de los metadatos del evento (ya se persiste en `meta`,
  línea 465 de `paper_trading_cycle.py`).
- `t_now` = tiempo del ciclo actual.

### 5.2 Proxy de fase

```
fase_proxy = "pre-match"   si  (t_now − t_detect) < Δ_pre        (recién detectado, se asume
                                                                   que el evento todavía no
                                                                   arrancó o arrancó hace muy
                                                                   poco)
fase_proxy = "early-match" si  Δ_pre ≤ (t_now − t_detect) < Δ_early
                                Y  (t_resolution − t_now) > Δ_late_margin
fase_proxy = "mid-match"   si  (t_now − t_detect) ≥ Δ_early
                                Y  (t_resolution − t_now) > Δ_late_margin
fase_proxy = "late-match"  si  (t_resolution − t_now) ≤ Δ_late_margin
                                                                  (queda poco para la resolución,
                                                                   sin importar cuánto tiempo
                                                                   lleva corriendo — este
                                                                   criterio DOMINA sobre los
                                                                   anteriores)
```

`Δ_pre`, `Δ_early`, `Δ_late_margin` son ventanas de tiempo, simbólicas, a calibrar por
categoría (un partido de tenis dura horas, un evento climático semanal dura días — no puede ser
la misma constante para ambos). El criterio de "queda poco para resolución" ya existe en el
sistema con un rol parecido: `_S1_HEDGE_TIMEOUT_HOURS = 2.0` (doc 2026-07-06 §4) — recomiendo
que `Δ_late_margin` reutilice ese mismo parámetro o uno derivado de él, en vez de introducir una
tercera constante de tiempo independiente sin relación con la que ya existe para el mismo
propósito conceptual (evitar tratar como "hay tiempo de sobra" un evento que en realidad está a
punto de cerrar).

### 5.3 Qué tan aproximado es esto — decirlo explícitamente

- `t_detect` NO es "cuándo empezó el evento" — es "cuándo Kairos lo vio por primera vez en su
  ciclo de scanning de 5 minutos". Un evento puede llevar horas corriendo antes de que Kairos lo
  detecte por primera vez si recién en ese momento su precio entra en el rango admisible bajo la
  regla unificada (§1.2) — en ese caso el proxy clasificaría como "pre-match" un evento que en
  realidad ya está en tramo mid. Este error es sistemático hacia SUBESTIMAR cuánto avanzó el
  evento, no aleatorio.
- El criterio de "queda poco para resolución" (`Δ_late_margin`) es más confiable que el de
  "cuánto lleva corriendo" porque `resolution_date` sí es un dato real de la fuente, no inferido
  — por eso lo dejo como criterio dominante en la regla de arriba.
- **Lo que se necesitaría para hacerlo bien**: un dato real de progreso en vivo del evento —
  para deportes, algo como el reloj de partido/set actual, número de set/inning/cuarto, o al
  menos un timestamp de "kickoff real" distinto de `resolution_date` (algunas fuentes lo
  exponen); para eventos no deportivos (clima, resultados de elección), un indicador de "ventana
  de resolución activa" (ej. si el evento resuelve por una medición puntual en el tiempo vs. un
  proceso que se desarrolla). Sin eso, el proxy de arriba es una aproximación de mejor esfuerzo,
  no una medición — cualquier calibración de `h(fase)` (§2.2) hecha sobre este proxy hereda su
  error sistemático y debe declararse como tal en cualquier reporte de performance por fase.

### 5.4 Fail-closed si ni el proxy es aplicable

Si un evento no tiene `resolution_date` parseable (ya existe manejo defensivo para esto en
`paper_trading_cycle.py` alrededor de la línea 420-428, que descarta la cobertura si
`resolution_date` no es parseable), tampoco se le puede asignar ninguna fase con este proxy —
debe caer en el mismo camino de "no admitido bajo esta regla" que ya existe para
`resolution_date` inválida, sin inventar un fallback de fase por defecto (ej. "asumir mid-match
si no se sabe" sería exactamente el tipo de fallback silencioso que esta serie de auditorías
viene señalando como peligroso).

---

## Resumen para Sara (≤300 palabras)

**Condición unificada de "nunca pérdida garantizada"** tiene dos mitades independientes, ambas
se sostienen en el diseño generalizado:

1. *Si ambas patas llenan*: `margen_dog(fase, m) < δ(m, σ_fase)` estricto, con `margen_dog`
   fijado por un piso independiente de fees+slippage (nunca por simetría — ya probé
   algebraicamente que simetría da ROI=0 exacto). Fail-closed (ningún nivel propuesto) si no
   existe tal margen viable, mismo patrón que `compute_l2_max()`.
2. *Si solo llena el líder*: se mantiene automáticamente como riesgo direccional puro, nunca
   pérdida garantizada, porque `L_líder = p0_líder − δ` con `δ > 0` siempre es estrictamente
   mejor que el precio de mercado del momento — esto es estructural, no depende de que σ/fase
   estén bien calibrados; una mala calibración solo degrada fill-rate o margen de ROI, nunca
   convierte este escenario en pérdida garantizada.

**Fase del evento** entra como escalador multiplicativo de σ: `σ_fase = σ · h(fase)`, con
`h(fase)` simbólico (`h_pre≈0.6, h_early≈0.8, h_mid≈1.3, h_late≈0.7`, forma cualitativa
justificada — shock de baja frecuencia pre-match, alta volatilidad realizada mid-event, colapso
de volatilidad remanente late-event — valores no calibrados con datos reales, fail-closed si no
hay base ni para el placeholder cualitativo). Sin feed en vivo de progreso, propongo un proxy de
fase usando `opened_at` (primera detección) y `resolution_date`, con el criterio de "queda poco
para resolución" dominando sobre "cuánto lleva corriendo" — señalo explícitamente que este proxy
subestima sistemáticamente el avance real del evento y que hacerlo bien requiere un dato real de
progreso en vivo (reloj de partido/set, kickoff real, etc.), inexistente hoy en Kairos.

**Caso 50/50 legacy**: confirmado como caso particular — con σ_fase chico (fase temprana) y `m`
moderado, `δ(m,σ_fase) ≈ m·σ_fase/4` (aproximación lineal de sigmoid cerca de 0.50) reproduce el
comportamiento de `EPSILON_INITIAL=0.03` fijo, pero ahora derivado de volatilidad real en vez de
una constante ciega. El rediseño generaliza sin romper lo desplegado.
