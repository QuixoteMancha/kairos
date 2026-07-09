# Plan: Selector de Estrategias por Sharpe Ratio con Piso Diario de 2%

Autora: Nora (planificadora de roadmap). Reporta a Sara.
Estado: DRAFT — ningún ítem de este documento debe empezar a construirse antes
de que los puntos `[DECISIÓN SARA]` marcados abajo se respondan por escrito.
No hay ejecución real de órdenes propuesta en ningún punto de este documento
(línea roja del proyecto, sin excepción).

## 1. Alcance y supuestos

**Investigado con evidencia real (lectura de código, no memoria):**
- `src/core/orchestration/paper_trading_cycle.py` (999 líneas): confirma que
  Strategy 1 (`_run_strategy1_pass`, línea 613) corre como máquina de estados
  universal ya wireada, y que Strategy 2/3/football se tratan como
  "SETTLING IMMEDIATELY once both legs fill" (comentario línea 44) — es
  decir, varianza ≈0 una vez llenadas ambas patas, consistente con lo que Sara
  ya había confirmado.
- `src/core/strategies/dwell_time/` (7 archivos: `base.py`, `registry.py`,
  `tennis_markov.py`, `football_poisson.py`, `basketball_winprob.py`,
  `baseball_winexp.py`, `weather_climatology.py`): existen y tienen forma de
  modelo (`DwellBand`, `DwellTimeModel` protocol per `base.py`), pero no hay
  ningún import de este paquete dentro de `paper_trading_cycle.py` — confirma
  el estado huérfano ya reportado.
- `grep -rln "crypto"` en `src/core/orchestration/` y `src/scrapers/` y
  `find src -iname "*crypto*"`: no existe ningún módulo `src/core/strategies/
  crypto*` ni archivo con "crypto" en el nombre — los scanners 5m/15m viven
  dentro de la lógica genérica de `scanning.py`/`polymarket_scan.py` sin
  ningún submódulo de modelo de probabilidad direccional dedicado. Confirma:
  no hay ni un esqueleto de modelo de probabilidad crypto, se construye desde
  cero.
- `src/database/models/paper_wallet.py`: `SimulatedWallet` tiene `balance` y
  `initial_balance` como campos planos — no existe hoy un concepto de
  "capital disponible para asignar" separado de "capital total"; cualquier
  distinción entre ambos para el piso del 2% es diseño nuevo, no algo que ya
  exista parcialmente.
- `docs/time-oscillation-limit-arb-plan.md` y
  `docs/roadmap-open-items-2026-07-05.md` (completos): leídos para la sección
  5 de este documento.

**Asumido por falta de acceso/tiempo (marcado explícitamente):**
- No se leyó el contenido matemático interno de cada modelo dwell-time
  (`tennis_markov.py`, `football_poisson.py`, etc.) línea por línea — eso es
  correctamente scope de Vesper (despachada en paralelo por Sara para esa
  auditoría matemática), no duplicado aquí.
- No se investigaron proveedores externos de datos climáticos/crypto
  (ej. si hay una API de clima ya integrada, o qué fuente de precios crypto
  alimenta hoy los scanners 5m/15m más allá de "detecta arbs de precio") —
  este plan asume que Vesper o un agente ejecutor de Fase 3 (crypto) deberá
  hacer ese descubrimiento como primer paso, no se asume una fuente aquí.
- No se corrió pytest ni se midió el volumen real de historial paper
  acumulado (cuántos ciclos/bets ya registrados) — el punto de "cuánto
  historial mínimo hace falta antes de confiar en el ranking" (sección 4) se
  deja como pregunta abierta con un piso conservador recomendado, no un
  número confirmado con datos reales.

**Explícitamente fuera de este documento:**
- La fórmula matemática exacta del Sharpe (despachada a Vesper en paralelo,
  no se duplica).
- Implementación de código o tests.
- Cualquier forma de ejecución real de órdenes.
- Cambios a wallet.py/ledger/rebalance.py (fuera de zona a menos que un
  `[DECISIÓN SARA]` explícito lo autorice; ver sección 3).

---

## 2. Fases de trabajo

### FASE 0 — Decisiones que bloquean todo (sin excepción)

No es una fase de ingeniería. Ver sección 3 completa. **Criterio de
"terminado":** cada `[DECISIÓN SARA]` respondido por escrito. No
paralelizable (una sola persona decide), pero es rápida — no requiere
lectura de código adicional a la ya hecha aquí y por Vesper.

---

### FASE 1 — Ya se puede construir sin esperar a Vesper (arb puros, varianza≈0)

**1.A — Sharpe trivial para Strategy 2/3/football (arbs puros)**
- **Qué se construye:** nuevo módulo `src/core/orchestration/sharpe_selector.py`
  con una función `compute_pure_arb_sharpe(opp: dict) -> float` que, dado que
  la varianza de estas 3 estrategias es ≈0 una vez ambas patas llenan
  (confirmado, ver Alcance), reduce el Sharpe a esencialmente
  `retorno_neto_esperado / epsilon_riesgo_residual` (el término de riesgo
  residual real — riesgo de no-llenado de una pata, no de varianza de
  resultado — es la parte que Vesper debe definir formalmente; este módulo
  puede scaffoldearse con un placeholder documentado y quedar bloqueado en
  ese único input).
- **Reutiliza:** `_dedupe_arbs_by_shared_leg()` (patrón de ranking ROI-first
  ya en producción, ver memoria `kairos-arb-dedup-shared-leg-fix`) como
  precedente de diseño para el ranking, no como código a importar
  directamente.
- **Terminado cuando:** dado un set de opps de test con ROI netos conocidos,
  la función devuelve un ranking determinístico y estable, y Vesper confirma
  que la fórmula de riesgo residual (no-fill de una pata) está correctamente
  reflejada, no solo el retorno.
- **Paralelizable:** sí, con 1.B y con el trabajo de Vesper sobre dwell-time
  (no toca los mismos archivos).
- **Depende de:** el input de riesgo residual de Vesper (puede arrancar en
  paralelo con un placeholder, pero no puede darse por "cerrado" sin ese
  input).

**1.B — Esqueleto del selector diario + piso del 2% (sin modelos aún wireados)**
- **Qué se construye:** nuevo módulo `src/core/orchestration/daily_floor_selector.py`
  que, dado un capital de referencia (ver `[DECISIÓN SARA]` #1 en sección 3) y
  una lista de candidatos con Sharpe ya calculado, selecciona el subconjunto
  que maximiza probabilidad de alcanzar el piso de 2% diario, y **se abstiene
  de operar el día completo si ningún subconjunto lo garantiza** (comportamiento
  explícito pedido por el usuario — no forzar el número asumiendo más riesgo).
  Este esqueleto puede construirse y testearse con datos sintéticos/mock de
  candidatos ANTES de que Strategy 1/crypto tengan modelo real, siempre que la
  interfaz de entrada (`list[{"strategy": str, "expected_return": float,
  "sharpe": float, "capital_required": float}]`) quede congelada primero.
- **Terminado cuando:** con un set de candidatos mock (algunos que sí
  garantizan el piso, algunos que no), el selector produce la decisión
  correcta en ambos casos, incluyendo el caso "abstenerse todo el día".
- **Paralelizable:** sí, con 1.A — ambos escriben a módulos nuevos distintos
  y solo comparten la interfaz de entrada (acordar la interfaz primero, un
  intercambio corto entre agentes, no bloqueante).
- **Depende de:** `[DECISIÓN SARA]` #1 (definición de capital de referencia)
  y #3 (frecuencia de corrida del selector) de la sección 3 — la interfaz y
  la lógica de "abstenerse" cambian según esas respuestas.

---

### FASE 2 — Depende de la auditoría de Vesper sobre dwell-time (Strategy 1)

**2.A — Wireado de un modelo dwell-time real a Strategy 1 (solo si Vesper aprueba al menos uno)**
- **Qué se construye:** dependiendo de qué modelo(s) Vesper confirme como
  reusable/confiable hoy, modificar `src/core/orchestration/paper_trading_cycle.py`
  (`_run_strategy1_pass`, línea 613) para que, en lugar de decidir solo por
  cruces de precio, consulte el modelo aprobado de
  `src/core/strategies/dwell_time/registry.py` para las categorías que ese
  modelo cubre, y calcule un Sharpe real (retorno esperado del modelo vs. su
  propia varianza de acierto) por orden candidata de Strategy 1.
- **Terminado cuando:** existe al menos una categoría (ej. tenis) donde
  Strategy 1 en modo paper consulta el modelo Markov real y el Sharpe
  calculado para esa orden usa la probabilidad del modelo, no un heurístico
  de precio — verificable comparando logs de la orden antes/después del
  wireado.
- **Depende estrictamente de:** el veredicto de Vesper (qué modelo(s) son
  reusables sin recalibración, cuáles necesitan recalibración antes de
  confiar en su output). **No tiene sentido construir esto antes de tener
  ese veredicto** — sería wireado ciego a un modelo potencialmente mal
  calibrado, y el usuario pidió explícitamente evitar forzar el piso
  asumiendo riesgo no medido.
- **No paralelizable con 1.A/1.B** en el sentido de "cerrar" — puede
  scaffoldearse en paralelo (dejar el hook de llamada al modelo como stub),
  pero no puede considerarse terminado ni alimentar el selector real de Fase
  3 hasta que Vesper confirme.

**2.B — Categorías sin modelo aprobado (fútbol/basketball/baseball/clima si Vesper los rechaza)**
- **Qué se construye:** nada de código nuevo — una decisión de alcance: si
  Vesper rechaza un modelo por no-confiable, esa categoría queda fuera del
  cálculo de Sharpe de Strategy 1 hasta recalibración, lo cual entrelaza
  directamente con `[DECISIÓN SARA — D3]` ya abierta en
  `docs/roadmap-open-items-2026-07-05.md` (¿vale la pena invertir en sacar a
  fútbol/tenis/clima de su estado huérfano?). Este documento no la duplica,
  la referencia.
- **Terminado cuando:** Sara confirma qué categorías quedan dentro/fuera del
  selector para el primer release, documentado en el mismo lugar que 2.A.

---

### FASE 3 — Modelo de probabilidad crypto (construcción desde cero, no hay nada que reusar)

**3.A — Investigación de fuente de probabilidad direccional para crypto**
- **Qué se construye:** ningún código todavía — investigación (WebSearch/
  WebFetch + lectura de qué datos ya llegan a los scanners 5m/15m) de qué
  señal usar como probabilidad direccional calibrada: opciones típicas son
  volatilidad implícita de opciones (si hay alguna fuente pública), modelos
  de microestructura de order flow, o un modelo puramente estadístico
  (GARCH/random-walk con drift) sobre el propio historial de precio del
  activo subyacente (BTC/ETH spot, no el mercado de predicción en sí). Esto
  es un frente nuevo, no una extensión de dwell-time — confirmar con Vesper
  si el patrón de `DwellBand`/`DwellTimeModel` (`base.py`) sirve como
  contrato de salida igual (misma lógica que Strategy 4 reusa el contrato de
  `dwell_time/base.py`, ver `docs/time-oscillation-limit-arb-plan.md` sección
  2), aunque la estimación interna sea 100% nueva.
- **Terminado cuando:** existe un documento corto (no este) con la fuente de
  datos elegida, su costo/límites de rate, y una estimación de si el modelo
  es calibrable con los datos disponibles hoy — antes de escribir una línea
  de modelo.
- **Paralelizable con:** Fase 1 y Fase 2 completas (no comparte archivos).
  **No paralelizable con:** nada dentro de sí misma — es secuencial por
  naturaleza (investigación → diseño → implementación).

**3.B — Implementación del modelo crypto + wireado**
- **Qué se construye:** nuevo paquete `src/core/strategies/crypto_prob/`
  (nombre tentativo) con el modelo elegido en 3.A, wireado a los scanners
  5m/15m existentes (ubicación exacta no confirmada en esta pasada — un
  agente ejecutor debe correr `grep -rn "5m\|15m" src/core/orchestration/
  scanning.py src/core/orchestration/polymarket_scan.py` como primer paso).
- **Terminado cuando:** una orden crypto candidata tiene un Sharpe calculado
  con probabilidad de modelo real, no solo detección de arb de precio —
  mismo criterio de verificación que 2.A pero para crypto.
- **Depende de:** 3.A completa (no tiene sentido construir sobre una fuente
  de datos no validada). **No depende de** Fase 2 (Strategy 1/dwell-time) —
  son modelos de dominios distintos, pueden progresar en paralelo una vez
  ambas Fases 2 y 3.A estén desbloqueadas por sus respectivas decisiones.

---

### FASE 4 — El Selector real (requiere AL MENOS UN modelo de probabilidad confiable wireado)

**4.A — Integración completa: selector + piso de 2% + al menos 1 modelo real**
- **Qué se construye:** conectar `daily_floor_selector.py` (Fase 1.B) con
  Sharpe reales — de 1.A (arb puro) más de AL MENOS uno entre 2.A (Strategy 1
  con modelo real) o 3.B (crypto con modelo real). **No tiene sentido activar
  el selector en modo "producción" (aunque sea paper) antes de que exista
  al menos un modelo de probabilidad confiable wireado** — si el selector
  solo tuviera arb puro (Fase 1.A) para elegir, no habría nada que
  "seleccionar" realmente (sería simplemente "toma todo lo que hay", sin el
  trade-off riesgo/retorno que el usuario pidió explícitamente evaluar).
- **Terminado cuando:** en un ciclo paper real, el selector recibe candidatos
  reales (no mock) de al menos 2 fuentes de estrategia distintas, calcula
  Sharpe para cada una, decide el subconjunto (o decide abstenerse el día
  completo), y esa decisión es auditable en logs/DB.
- **No paralelizable:** depende estrictamente de que 1.A, 1.B, y al menos uno
  de {2.A, 3.B} estén cerrados.

**4.B — Persistencia de la decisión diaria + historial para backtesting del propio selector**
- **Qué se construye:** nueva tabla (ej. `DailySelectorDecision` en
  `src/database/models/domain.py`, siguiendo el patrón ya existente de
  `DwellTimeProposal`) que registre, por día, qué estrategias fueron
  seleccionadas, el Sharpe de cada una, si se decidió abstenerse, y el
  resultado real vs. el piso de 2% — esto es lo que permite, más adelante,
  auditar si el selector realmente cumple el piso o solo lo estima bien en
  papel.
- **Terminado cuando:** al menos un ciclo completo (real, no test) deja un
  registro persistido y consultable de la decisión y su justificación.
- **Paralelizable con:** 4.A parcialmente (el esquema de tabla puede
  diseñarse en paralelo con la integración, pero escribir a la tabla depende
  de que 4.A exista).

---

## 3. Puntos de decisión de Sara / usuario

`[DECISIÓN SARA — S1]` **¿El piso de 2% diario se mide sobre capital total o
sobre capital "disponible para asignar"?** Hoy `SimulatedWallet.balance` no
distingue entre ambos (confirmado leyendo `paper_wallet.py`) — si la
respuesta es "disponible para asignar", hace falta definir primero qué
resta del total (capital ya comprometido en posiciones abiertas de Strategy 1
directional, por ejemplo) antes de que Fase 1.B pueda cerrar su interfaz de
entrada real.

`[DECISIÓN SARA — S2]` **¿Qué hace Strategy 1 mientras no haya modelo de
probabilidad confiable wireado (antes de que Fase 2 cierre)?** Dos opciones
concretas, sin recomendación unilateral de mi parte porque toca tolerancia de
riesgo del proyecto:
  (a) Se pausa por completo del selector (no participa en el piso de 2% hasta
      tener modelo real).
  (b) Se le pone un tope chico de % de capital como salvaguarda temporal,
      usando el heurístico de precio actual como aproximación de bajo riesgo.
  Esta decisión determina si Fase 1.B puede considerar a Strategy 1 "elegible
  con tope" desde el día 1, o si debe excluirla por completo hasta Fase 2.

`[DECISIÓN SARA — S3]` **¿El selector corre por ciclo, por día, o
continuo?** El pedido original dice "piso de 2% diario", lo cual sugiere
granularidad diaria, pero el motor corre en ciclos (`run_paper_trading_cycle`,
`paper_trading_cycle.py` línea 821) — si corre por ciclo, hace falta definir
cómo se acumula/reconcilia el progreso intra-día hacia el piso; si corre una
vez al día, hace falta definir en qué momento del día se "cierra" la
decisión y qué pasa con oportunidades que aparecen después de esa corrida
(¿se pierden, o hay una re-evaluación intra-día si el piso ya no es
alcanzable con lo ya operado?).

`[DECISIÓN SARA — S4]` **¿Qué pasa si el piso de 2% se garantiza en la
mañana pero las condiciones de mercado cambian a mitad de día (ej. una
oportunidad seleccionada deja de ser fillable)?** Relacionado con S3 pero
distinto: ¿el selector re-evalúa intra-día o la decisión de la mañana es
definitiva hasta el cierre?

`[DECISIÓN SARA — S5]` **Alcance de categorías dwell-time para el primer
release del selector** (referencia directa a `[DECISIÓN SARA — D3]` ya
abierta en `docs/roadmap-open-items-2026-07-05.md`): si Vesper aprueba, por
ejemplo, solo tenis y rechaza fútbol/basketball/baseball/clima por
recalibración pendiente, ¿el selector lanza con solo esa cobertura parcial de
Strategy 1, o se espera a tener más categorías para que Strategy 1 aporte
Sharpe en un rango más amplio de eventos? Determina el timing real de Fase
4.A (que solo necesita "al menos un modelo real", pero el usuario podría
preferir más cobertura antes de considerar el selector "listo para confiar").

`[DECISIÓN SARA — S6]` **¿Se persigue el modelo crypto (Fase 3) en paralelo
con Fase 2 (dwell-time), o se prioriza uno sobre otro?** Ambos son
independientes técnicamente (no comparten archivos ni decisiones), pero
consumen atención de agentes/tiempo del usuario en paralelo — es una
decisión de priorización de producto, no técnica.

---

## 4. Riesgos conocidos

- **Sobreajuste del ranking Sharpe con poco historial paper.** El selector
  (Fase 4) va a rankear estrategias por Sharpe calculado sobre datos
  observados en paper-trading. Con pocos ciclos acumulados, un Sharpe
  "alto" puede ser ruido, no señal real — el sistema podría seleccionar
  sistemáticamente la estrategia que tuvo suerte en una ventana corta.
  **No se determinó en esta pasada cuántos ciclos/días de historial paper ya
  existen** (no se corrió una consulta a la DB de producción). Recomendación
  (no decisión tomada): no confiar en el ranking del selector hasta tener un
  mínimo de historial que Vesper debería fijar formalmente (norma típica en
  finanzas cuantitativas: mínimo ~30 observaciones independientes por
  estrategia antes de un Sharpe confiable, pero esto es una recomendación de
  orden de magnitud, no un número validado con los datos reales de Kairos —
  flag para que Vesper lo confirme o ajuste con los datos reales que sí
  tiene acceso a auditar).
- **El piso de 2% podría inducir presión a "forzar" candidatos marginales.**
  El usuario fue explícito en que si no se puede garantizar el piso, el
  sistema debe abstenerse — pero esto depende de que Fase 1.B se implemente
  fielmente a esa regla y no se relaje bajo presión de "mostrar resultados".
  Riesgo de diseño, no de código: cualquier futura modificación al selector
  debe re-confirmar este comportamiento explícitamente antes de deploy.
- **Dependencia cruzada con Strategy 4 (time-oscillation) sin resolver.**
  Ver sección 5 — si Strategy 4 se construye en paralelo sin que exista
  todavía un sistema de "claim" de capital compartido (bloqueado en
  `docs/time-oscillation-limit-arb-plan.md` decisión #5), el selector de
  Sharpe de este documento podría terminar compitiendo por el mismo capital
  que Strategy 4 sin coordinación, incluso en modo paper (dos "decisiones
  de asignación" simultáneas sobre el mismo wallet).
- **Gap de información — fuente de datos crypto no confirmada.** Fase 3.A es
  investigación pura precisamente porque no se confirmó en esta pasada qué
  fuente de precio subyacente alimenta hoy los scanners 5m/15m ni si hay
  acceso a volatilidad implícita real. Podría no existir una fuente gratuita
  suficientemente buena, lo cual bloquearía Fase 3 completa hasta que Priya
  (u otro agente con foco en integraciones externas) confirme viabilidad.
- **Riesgo de re-litigar el mismo bug de doble-conteo de capital que Strategy
  4 ya identificó.** El selector de Fase 4 introduce una nueva capa de
  "decisión de asignación diaria" que se suma a la falta actual de un
  sistema de claim de capital entre las 4 estrategias existentes — no es un
  riesgo nuevo, es el mismo riesgo A de `time-oscillation-limit-arb-plan.md`
  visto desde un ángulo distinto (ver sección 5).

---

## 5. Interacción con trabajo existente

**Con `docs/time-oscillation-limit-arb-plan.md` (Strategy 4, bloqueada en
diseño de "claim" de capital compartido):**
Este selector de Sharpe y Strategy 4 tocan la misma tensión de fondo —
asignación de un capital compartido entre múltiples estrategias que hoy no
se coordinan entre sí. Recomendación (no decisión tomada, marcar para
Sara): **el sistema de "claim" de capital compartido (decisión #5 de ese
documento) debería resolverse ANTES o JUNTO CON el selector de Sharpe de
este documento, no después.** Razón concreta: el selector de Fase 4 de este
plan literalmente decide "cuánto capital asignar a cada estrategia hoy" —
si ese capital no tiene un mecanismo de reserva/claim real (hoy ninguna
estrategia reserva capital, todas son proposal-only o settle-inmediato), el
selector podría calcular una asignación óptima sobre un capital que otra
estrategia (incluyendo una futura Strategy 4) simultáneamente asume
disponible. Construir el selector sin resolver esto primero replicaría, en
una capa nueva, el mismo bug de doble-conteo que ya se encontró y arregló
reactivamente una vez (`_dedupe_arbs_by_shared_leg`, ver memoria
`kairos-arb-dedup-shared-leg-fix`) — mejor resolverlo de forma proactiva
esta vez, dado que ya hay precedente de que el patrón se repite.

**Con `docs/roadmap-open-items-2026-07-05.md`:**
- `[D1]` (Strategy 4, 5 decisiones) y `[S1]`-`[S6]` de este documento son
  decisiones independientes entre sí pero con el punto de contacto de
  capital compartido arriba — pueden responderse en la misma sesión de
  decisiones de Sara, no hay necesidad de secuenciarlas artificialmente.
- `[D3]` (fútbol/tenis/clima huérfanos — ¿cablear fuente real o aceptar
  huérfano?) es un prerequisito directo de mi Fase 2.B — si Sara ya decidió
  D3 en el ínterin, esa respuesta debería copiarse/referenciarse aquí en vez
  de re-abrirse.
- Fase 3.A de ese roadmap (calibración empírica de umbrales fútbol 3-way,
  coordinada con Vesper) y la auditoría de Vesper sobre dwell-time
  (despachada para este trabajo) tocan superficie adyacente pero no
  idéntica — recomiendo confirmar con Vesper que no está evaluando el mismo
  archivo/lógica dos veces el mismo día, mismo riesgo de solapamiento ya
  señalado en ese roadmap para otros pares de tareas.

**Conclusión de priorización (recomendación, no decisión):** el selector de
Sharpe de este documento NO debería esperar a que Strategy 4 esté
completamente resuelta (son mayormente independientes en código), pero SÍ
debería esperar a que la decisión de diseño del sistema de claim de capital
compartido (común a ambos) se tome una sola vez, de forma que ambos
consumidores (Strategy 4 Fase 4 y este selector Fase 4) lo usen igual en vez
de que cada uno invente su propia variante del mismo mecanismo.
