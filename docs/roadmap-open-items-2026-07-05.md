# Roadmap — Open Items (2026-07-05)

Autora: Nora (planificadora de roadmap). Reporta a Sara.

## 0. Alcance y supuestos

**Investigado con evidencia real:**
- `docs/time-oscillation-limit-arb-plan.md` (documento completo, 189 líneas) para el
  resumen de Strategy 4 en la sección 2.
- Listado de `docs/*.md` existentes.
- `grep -rn "TODO|FIXME"` en todo el árbol de `src/` y `tests/`, excluyendo
  explícitamente wallet/ledger/rebalance/kalshi_client/matcher (zonas cubiertas por
  otros 3 agentes en paralelo hoy: Vesper, Priya, y el agente de re-verificación
  CRITICAL).
- `docs/strategies_models.md` para TODOs de modelos de dominio.

**Asumido por falta de acceso/tiempo (marcado explícitamente donde aplica):**
- No se re-ejecutó el matcher ni se leyó código de wallet/rebalance/kalshi_client —
  fuera de scope por instrucción explícita, para no duplicar el trabajo de Vesper/
  Priya/el agente CRITICAL corriendo en paralelo.
- No se midió en vivo el tiempo de `_fetch_prediction_market_events()` (frente 4) —
  se toma el número 102.7s documentado en memoria como dato válido, sin
  re-verificación propia.
- No se investigaron APIs externas (Polymarket Liquidity Rewards, endpoints de
  price-history de Kalshi/Manifold) más allá de lo ya documentado en memoria —
  quedan como estaban: "investigado previamente, no reconfirmado hoy".

**Explícitamente fuera de este documento:**
- Cualquier cambio a wallet.py, ledger, rebalance.py, kalshi_client.py, o el
  matcher cross-platform — corresponde a los otros 3 agentes en curso hoy.
- Implementación de código o tests — este documento es puramente el mapa, no
  ejecuta nada.
- Ejecución de órdenes reales — línea roja del proyecto, ningún ítem de este
  roadmap la asume ni la propone.

---

## 1. Fases de trabajo

### FASE 1 — Decisiones que bloquean todo lo demás (ningún trabajo de ingeniería debería arrancar antes de esto)

Esta fase no tiene "archivo a construir" — es puramente decisiones. Se detalla en
la sección 2 (Puntos de decisión de Sara). Ítems que bloquean:

- **[D1] Strategy 4 — 5 decisiones documentadas en
  `docs/time-oscillation-limit-arb-plan.md` sección 5** (líneas 167-189).
  Bloquea TODO el trabajo de Fase 2/3 de Strategy 4 (ninguna línea de código de
  `src/core/strategies/oscillation/` debería escribirse antes de esto).
- **[D2] Frente 6 — destino de pinnacle/odds_api scrapers** (mover a `forgotten/`
  o no). Bloquea una limpieza de bajo riesgo, pero decisión de alcance del
  proyecto, no técnica.
- **[D3] Frente 8 — football/tennis/weather dwell-time huérfanos**: ¿cablear una
  fuente real de eventos de fútbol, o aceptar el estado huérfano permanentemente?
  Bloquea si vale la pena invertir en frente 8 en absoluto antes de tocarlo.
- **[D4] Frente 7 — Polymarket Liquidity Rewards**: ¿cuál de las 3 estrategias
  candidatas (no detalladas aquí, ver memoria `kairos-polymarket-liquidity-
  rewards-research`) se persigue, si alguna? Bloquea si este frente se convierte
  en una Fase 2/3 de este roadmap o se descarta.

**Criterio de "terminado" de Fase 1:** Sara/usuario responde cada `[DECISIÓN
SARA]` de la sección 2 por escrito (no hace falta que sea unánime "sí" — un "no,
descartado" también cierra el punto). No es paralelizable por naturaleza (son
decisiones de una sola persona), pero SÍ es rápida: no requiere lectura de código
adicional más allá de lo ya hecho aquí.

---

### FASE 2 — Construcción paralelizable (pura ejecución técnica, sin decisiones pendientes)

Estos ítems no requieren esperar Fase 1 porque ya tienen decisión tomada o son
mejoras acotadas sin ambigüedad de diseño. Paralelizables entre sí (agentes
distintos, sin dependencia de archivos compartidos entre ellos).

**2.A — Performance: `_fetch_prediction_market_events()` cuello de botella (frente 4)**
- **Qué se construye:** perfilar y optimizar `_fetch_prediction_market_events()` en
  `src/core/orchestration/scanning.py` (memoria dice 102.7s en frío; no
  reconfirmado en esta sesión — primer paso de esta fase debe ser medir antes de
  optimizar). Candidatos típicos: cache warm-up, paralelización de fetch por
  plataforma, reducción de payload.
- **Terminado cuando:** medición repetible del tiempo de scan en frío antes/después,
  con umbral objetivo acordado con Sara antes de cerrar (no está definido en este
  documento — flag ligero, no bloqueante, dado que la mejora en sí no depende de
  ninguna decisión de negocio).
- **Paralelizable:** sí, con 2.B, 2.C y 2.D — no toca ningún archivo que ellos tocan.

**2.B — Código muerto: `find_dwell_time_liquidity_overlaps()` (frente 5)**
- **Qué se construye:** decisión técnica simple — o se elimina (si de verdad nunca
  se usará standalone, dado que Strategy 4 Fase 4 de su propio plan ya prevé
  generalizarla en `scanning.py`), o se deja documentada como "solo test, a
  generalizar cuando Strategy 4 llegue a Fase 4". Recomiendo NO borrar todavía —
  Strategy 4 Fase 4 la necesita como base a generalizar, borrarla ahora crea
  trabajo doble. **Esto es una recomendación de Nora, no una decisión tomada.**
- **Terminado cuando:** o bien la función se borra + su test se borra + `pytest`
  sigue en verde, o bien se agrega un comentario en el código apuntando a este
  roadmap explicando por qué se mantiene sin uso productivo hoy.
- **Paralelizable:** sí, con 2.A/2.C/2.D.

**2.C — Frontend: badge faltante para `football_1x2` (frente 3)**
- **Qué se construye:** agregar el badge de tipo de arb `football_1x2` en el
  frontend (no se identificó el archivo exacto en esta pasada — requiere que el
  agente ejecutor haga `grep -rn "dwell_time\|cross_platform" src/web/static/` o
  equivalente para encontrar dónde están los badges existentes de los otros tipos
  de arb y replicar el patrón; no se investigó el path concreto hoy porque no
  toca ninguna de las 4 zonas prohibidas, pero tampoco se profundizó por foco de
  tiempo — **supuesto marcado**: existe un componente/lista de badges por tipo,
  análogo entre estrategias).
- **Terminado cuando:** un arb real de tipo `football_1x2` (aunque huérfano hoy,
  ver frente 8/D3 — puede simularse con datos de test) se muestra con badge
  visualmente distinto al resto en el dashboard.
- **Paralelizable:** sí, con 2.A/2.B/2.D. Nota: si D3 resuelve "descartar football
  huérfano permanentemente", este ítem pierde prioridad pero no se invalida (el
  arb type ya existe en el motor, el badge es deuda visual independiente).

**2.D — Calibración: `MIN_ROI_PCT`/`MIN_NET_ROI_PCT`/`MIN_LEG_LIQUIDITY` del arb
3-way de fútbol (frente 2)**
- Esta es la única de las 4 que en rigor pertenece más a Fase 3 (calibración con
  datos reales) que a Fase 2 (construcción), porque "terminado" depende de datos
  observados, no de código escrito. Se detalla en Fase 3 más abajo para evitar
  duplicarla.

---

### FASE 3 — Calibración con datos reales (depende de que 2.A-2.C ya estén corriendo o de decisiones de Fase 1, según el ítem)

**3.A — Calibración empírica de umbrales fútbol 3-way (frente 2, antes 2.D)**
- **Qué se construye:** NO es código nuevo — es un proceso de observación: correr
  el scanner de fútbol 3-way (`football_3way_arb`, commit `154068e`) contra datos
  reales durante una ventana (ej. 1-2 semanas de eventos reales) registrando
  cuántas oportunidades se detectan/descartan en cada umbral, y ajustar
  `MIN_ROI_PCT=1.5` / `MIN_NET_ROI_PCT=0.5` / `MIN_LEG_LIQUIDITY=100.0` según esa
  evidencia. Archivo: donde estén definidas estas constantes (no confirmado en
  esta pasada — el commit `154068e` es la referencia, un agente ejecutor debe
  ubicar el archivo exacto vía `git show 154068e --stat`).
- **Terminado cuando:** Vesper (el auditor cuantitativo) confirma que los umbrales
  ajustados no generan ni falsos positivos evidentes ni descarte excesivo de
  oportunidades reales, con datos de al menos una ventana observada.
- **Depende de:** nada bloqueante de Fase 1 — puede arrancar ya, en paralelo con
  Fase 2, PERO debe coordinarse con Vesper (que hoy audita correctness de las 4
  estrategias) para no pisar el mismo análisis dos veces el mismo día.
- **No paralelizable con el trabajo de Vesper de hoy** — es la misma superficie
  (Strategy 3/football 3-way), coordinar turno.

**3.B — Strategy 4 completa (Phases 1-5 de `time-oscillation-limit-arb-plan.md`)**
- Bloqueada por completo en Fase 1 [D1]. Una vez resueltas las 5 decisiones, el
  propio documento ya trae su fase interna detallada (Phase 1 volatility
  estimator → Phase 2 level formation → Phase 3 fillability/worker → Phase 4
  persistence/overlap → Phase 5 tests/audit), con dependencias y
  paralelización ya especificadas ahí mismo — no se duplica aquí, se referencia.
- **Terminado cuando:** las 5 fases internas del documento de Strategy 4 cierran
  sus propios criterios (detallados en ese documento), y en particular la Fase 4
  de ESE documento (sistema de claim de capital) está operativa ANTES de que
  cualquiera de las 4 estrategias reciba autorización de capital real.

---

## 2. Puntos de decisión de Sara / usuario

`[DECISIÓN SARA]` — 4 puntos marcados en total en este documento:

1. **[DECISIÓN SARA — D1]** Las 5 decisiones de Strategy 4 (resumen abajo con
   recomendación de Nora sobre la más crítica, la 5ª).
2. **[DECISIÓN SARA — D2]** ¿Mover pinnacle/odds_api scrapers a `forgotten/`?
   (sofascore/flashscore quedan activos igual, esto no los afecta).
3. **[DECISIÓN SARA — D3]** ¿Cablear fuente real de eventos de fútbol para sacar
   football/tennis/weather dwell-time de su estado huérfano, o aceptarlo
   permanentemente huérfano? Impacta directamente la prioridad real de 2.C
   (badge) y determina si vale la pena seguir manteniendo esos 3 modelos vivos.
4. **[DECISIÓN SARA — D4]** ¿Cuál (si alguna) de las 3 estrategias candidatas de
   Polymarket Liquidity Rewards se persigue?

### Resumen y recomendación sobre las 5 decisiones de Strategy 4

Del documento `docs/time-oscillation-limit-arb-plan.md` sección 5:

1. Confirmar alcance read-only/proposal-only (sin ejecución real) antes de Phase 1.
2. Confirmar Strategy 4 como estrategia separada (no extensión de dwell-time) —
   el documento ya recomienda "separada" con justificación técnica.
3. Ventana de lookback / régimen de volatilidad — decisión de calibración, no de
   arquitectura, ruteada a Vesper antes de confiar en el output.
4. Soporte de price-history en Kalshi/Manifold — sin confirmar; hasta que Priya
   lo confirme, alcance Polymarket-only.
5. **Sistema de "claim" de capital compartido entre las 4 estrategias — la más
   crítica y la única que bloquea arquitectura, no solo alcance.** Hoy ninguna de
   las 4 estrategias reserva capital real (todas son proposal-only), así que el
   riesgo A del documento (doble-conteo de la misma leg entre 4 estrategias) es
   tolerable HOY, pero se vuelve obligatorio resolver ANTES de que cualquier
   estrategia reciba autorización de ejecución real.

   **[DECISIÓN SARA — D1, sub-punto 5, recomendación de Nora, NO decisión
   tomada]:** sugiero extender el patrón ya probado de
   `_dedupe_arbs_by_shared_leg()` (ROI-ordered claim, confirmado funcionando y
   verificado en memoria `kairos-arb-dedup-shared-leg-fix`) a ser
   *strategy-aware* en vez de diseñar un sistema nuevo desde cero: mismo
   principio (ordenar candidatos por ROI neto, el primero que reclama una leg
   "gana", los demás se descartan/marcan como no ejecutables), pero la clave de
   agrupamiento pasa de `(book, external_id)` a incluir también qué estrategia
   originó la propuesta, para que el detector de Fase 4 de Strategy 4 pueda
   alimentar el mismo mecanismo sin reinventarlo. Ventaja: reutiliza código ya
   auditado y en producción; desventaja: puede no escalar limpio si en el futuro
   se necesita priorización por algo distinto de ROI puro (ej. urgencia por
   cierre de ventana) — si eso importa, es una razón para que Sara decida
   diseñar un sistema nuevo en vez de extender el existente.

---

## 3. Riesgos conocidos

- **Riesgo de solapamiento temporal:** Vesper, Priya y el agente CRITICAL están
  trabajando en simultáneo sobre superficies adyacentes (Strategy 3 football
  3-way, matcher, wallet/rebalance). La Fase 3.A de este roadmap (calibración de
  umbrales fútbol 3-way) toca la MISMA superficie que audita Vesper hoy — no se
  debería lanzar un agente ejecutor sobre 3.A hasta confirmar que Vesper terminó
  su pasada de hoy, para evitar hallazgos contradictorios o commits en conflicto.
- **Gap de información — 2.C (badge frontend):** no se identificó el archivo/
  componente exacto del frontend donde viven los badges de tipo de arb en esta
  pasada (limitación de tiempo/foco, no de acceso). El agente ejecutor de 2.C
  debe hacer ese descubrimiento como primer paso, no asumir un path.
- **Gap de información — 3.A (ubicación de constantes):** el archivo exacto
  donde viven `MIN_ROI_PCT`/`MIN_NET_ROI_PCT`/`MIN_LEG_LIQUIDITY` para fútbol
  3-way no se confirmó línea por línea — solo se referencia el commit `154068e`
  como punto de partida.
- **Riesgo de negocio, no técnico:** si D3 se resuelve como "cablear fuente real
  de fútbol", eso reabre la pregunta de si vale la pena el esfuerzo dado que
  Kalshi/Manifold excluyen deportes por diseño (`football-arb-orphaned-by-
  design`, confirmado arquitectónico) — cablear una fuente nueva podría requerir
  un scraper adicional fuera del alcance de "solo prediction markets", lo cual
  podría chocar con el pivote irreversible ya confirmado (frente descartar
  bookies deportivos). Este documento no resuelve esa tensión, solo la señala.
- **No se investigó si hay otros TODOs relevantes fuera de las 4 zonas
  prohibidas más allá de los encontrados por grep** — el grep fue exhaustivo
  sobre `src/`+`tests/` pero no cubrió comentarios en `docs/*.md` restantes
  (`ANALYTICS.md`, `AUTH_FLOW.md`, `DOCKER.md`, `ARCHITECTURE.md`,
  `betting_settlement_rules.md`, `POLYMARKET_REAL_TRADING_PLAN.md`) más allá de
  una búsqueda rápida en 2 de ellos — posible gap menor, bajo riesgo dado que son
  documentos de infraestructura/auth, no de lógica de arbitraje.
