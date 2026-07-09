# Plan: Paper-Trading Compuesto (200 USDC simulados) + Portfolio Allocator para las 4 estrategias de arb

Autora: Nora (planificadora de roadmap). Reporta a: Sara.
Fecha: 2026-07-05.
Este documento es SOLO PLAN — no contiene código ni edita archivos existentes.

---

## 0. Alcance y supuestos

**Investigado con evidencia real (Read, esta sesión):**
- `src/database/models/wallet.py` — modelos reales `Wallet`/`WalletBet`/`WalletBetLeg`/`WalletLedger`/`WalletDeposit`/`WalletWithdraw` (peewee, sqlite vía `db` en `src/database/database.py`). Confirmado: `Wallet.platform` default `'stake'` (el landmine documentado en memoria — cualquier tabla nueva de paper-trading NO debe repetir este patrón de default implícito).
- `src/core/orchestration/portfolio.py` (316 líneas, completo) — `build_portfolio()`, `StrategyProfile`, Kelly fraccional (`KELLY_FRACTION=0.25`), `STRATEGY_BUCKET` mapea a `football_elo_value`/`nfl_elo_value`/`nhl_elo_value` (deportes, no prediction markets), `feasible_2pct`, persistencia vía `AppConfig` (JSON, key `strategy_portfolio_v1`).
- `src/core/orchestration/strategy_allocator.py` (527 líneas, completo) — `StrategyAllocator.allocate()`, buckets `low_risk_arb`/`medium_value_stat`/`risk_managed_hedge`/`other`, `classify_opportunity()` clasifica por substring en `o["tipo"]` (`"arb"`, `"stat"`/`"model"`/`"value"`, `"hedge"`), EWMA de calidad realizada por bucket (`update_realized`), `SPORT_TO_STRATEGY` (deportes → estrategia de backtest), `_apply_portfolio()` filtra/pondera value-bets vía pesos del portfolio.
- `src/core/orchestration/fill_simulator.py` (159 líneas, completo) — **acoplado a Polymarket únicamente**: `simulate_arb_legs()` filtra `leg.get("book") == "polymarket" and leg.get("polymarket_token_id")`, descarta explícitamente patas Kalshi/Manifold ("no known public order-book-depth endpoint wired into their clients yet — documented limitation"). `simulate_limit_fill()` (la función núcleo de walk-the-book) SÍ es genérica: recibe `book_side: list[tuple[price, size]]`, `target_price`, `capital_usd`, `side` — no conoce de estrategias ni de plataforma. Es reutilizable para las 4 estrategias siempre que se le pase el book real de cualquier plataforma en ese formato.
- `src/web/blueprints/wallet.py` — rutas reales del wallet (`/api/wallet`, `/api/wallet/total`, `/api/wallet/stats`, `/api/wallet/platforms`, `/api/wallet/set-platform`) como referencia de patrón de blueprint a replicar para paper-trading.
- `src/web/blueprints/prediction_markets_bp.py` — patrón de blueprint ya usado para endpoints de prediction markets (`/api/scan/prediction-markets/*`).
- `frontend/src/pages/` — solo 3 páginas hoy (`DashboardPage.vue`, `PlayerPage.vue`, `AuditPage.vue`); `frontend/src/components/wallet/` tiene `WalletCard.vue`, `DepositModal.vue`, `WithdrawModal.vue` como referencia de componente a extender/clonar, no un `PortfolioPage` existente.
- Confirmado por Read: no existe hoy ningún módulo `simulated_wallet.py`/`paper_trading.py` en el repo (grep sin resultados relevantes fuera de docs).

**Asumido por falta de acceso/tiempo (marcado explícitamente, no verificado con código):**
- Que Kalshi y Manifold exponen profundidad de book pública utilizable para fills simulados de Strategy 2/3/football — la memoria del proyecto (`kairos-strategy4-kalshi-manifold-data-research`) dice que existen endpoints de trade tick-level sin auth, pero no confirmé aquí si dan **profundidad de order book** (bids/asks con tamaño) o solo trades ejecutados. Esto es un riesgo de Fase 2, ver más abajo.
- Que la fórmula de sizing de Vesper (`docs/universal-limit-order-strategy-math-audit-2026-07-05.md`) definirá qué campos de fill necesita la Strategy 1 universal — no leí ese archivo (puede no existir aún, según el encargo).
- Que "compounding agresivo" implica reinversión automática del balance simulado completo en cada ciclo (no un % fijo separado como reserva) — esto es justamente uno de los puntos de decisión abajo, no lo asumo resuelto.

**Explícitamente fuera de este documento:**
- Diseño de la fórmula matemática de Strategy 1 universal (Vesper, en paralelo).
- Cualquier línea de código, test, migración de DB real o edición de `portfolio.py`/`strategy_allocator.py`/`fill_simulator.py`.
- Ejecución de órdenes reales (no existe en el sistema, línea roja permanente).

---

## 1. Esquema de datos: `SimulatedWallet` / `SimulatedLedger` / `SimulatedBet`

Nuevo módulo propuesto: `src/database/models/paper_wallet.py` (archivo nuevo, no toca `wallet.py`). Import y creación de tablas cableada donde hoy se registran los modelos reales (buscar el `create_tables([...])` en `src/database/database.py` o equivalente — Sara/implementador debe confirmar el punto exacto de registro).

### `SimulatedWallet` (una fila = un "libro" de paper-trading; soporta escenarios independientes)
```
id                  PK
label               CharField, unique   # ej. "paper-default-200usdc"
platform            CharField, default="paper"   # SIEMPRE literal "paper", NUNCA
                                                    # derivado de state.active_platform
                                                    # (evita el bug recurrente documentado
                                                    # en memoria: 4+ rondas de fixes por
                                                    # default implícito a active_platform)
initial_balance      FloatField, default=200.0   # el ancla: 200 USDC
balance              FloatField                   # capital líquido disponible ahora
equity               FloatField                   # balance + valor mark-to-market de posiciones abiertas
started_at           CharField (ISO)
strategy_scope       CharField, default="all"     # "all" | "strategy_1" | "strategy_2" | "strategy_3" | "football_3way"
                                                     # ver Punto de Decisión #3 (capital compartido vs separado)
reset_count          IntegerField, default=0       # cuántas veces se reseteó (ver Punto de Decisión #2)
is_active            BooleanField, default=True
```

### `SimulatedLedger` (cada movimiento — espejo de `WalletLedger` real, líneas 130-147 de `wallet.py`)
```
id                   PK
wallet_id            FK → SimulatedWallet
timestamp            CharField (ISO)
op_type              CharField      # "open_position" | "settle_win" | "settle_loss" | "fee" | "reset"
ref_table            CharField      # "simulatedbet"
ref_id               CharField
amount               FloatField
balance_after         FloatField
equity_after          FloatField
strategy              CharField      # "strategy_1_universal" | "strategy_2_cross_platform" |
                                      # "strategy_3_same_market" | "football_3way"
day_bucket           CharField (YYYY-MM-DD, UTC)  # clave para agregación de curva diaria
note                 TextField
```
`day_bucket` es el campo que hace posible el gráfico de "balance simulado día a día" sin recalcular desde cero en cada request — se agrega por `GROUP BY day_bucket, wallet_id` tomando el último `equity_after` del día.

### `SimulatedBet` (espejo simplificado de `WalletBet`, líneas 45-88 de `wallet.py`)
```
bet_id               CharField PK
wallet_id            FK → SimulatedWallet
strategy             CharField          # una de las 4
platform_legs        TextField (JSON)   # lista de plataformas reales involucradas
                                          # (polymarket/kalshi/manifold) — NO "paper",
                                          # eso es solo el wallet. Necesario para que
                                          # el reporting distinga "arb cross Polymarket-Kalshi"
                                          # vs "arb same-market Polymarket" etc.
opened_at            CharField
total_staked         FloatField
expected_roi_pct     FloatField          # ROI esperado al momento de detectar el arb
simulated_fill_json  TextField           # output crudo de fill_simulator para cada pata
                                          # (ver sección 2) — trazabilidad de que NO fue
                                          # un fill optimista sintético
actual_filled_pct    FloatField          # 0-100, según book real al momento de la simulación
status               CharField, default="open"   # open | settled_win | settled_loss | unfillable
pnl                  FloatField, default=0.0
resolution_meta       TextField, default=""
```

**Relación con las 4 estrategias:** `strategy` es el discriminador único usado tanto en `SimulatedLedger` como en `SimulatedBet` — permite el desglose "por estrategia" que pide el punto 5 (frontend) sin joins complejos, y es el mismo string que usará el allocator adaptado (sección 3) para mapear pesos.

**Compounding día a día arrancando en 200 USDC:** el compounding se deriva, no se guarda como campo separado — es simplemente `equity_after` de la última fila de `SimulatedLedger` por `day_bucket`. La reinversión agresiva significa que el `capital_usd` pasado a `simulate_arb_legs`/al sizing de cada estrategia en el ciclo N+1 usa el `balance` actual de `SimulatedWallet`, no el `initial_balance` fijo — esto es responsabilidad del motor de ejecución simulada (no de este esquema), pero el esquema debe soportarlo sin fricción (ya lo hace, vía `balance` mutable).

**Paralelizable:** Sí — este esquema no depende de la fórmula de Vesper ni de decisiones de allocator. Puede implementarse y testearse (fixtures con 200 USDC, aperturas/settlements sintéticos) en paralelo a la Fase 2 y 3.

**Criterio de "terminado":** migración/creación de tablas corre limpia sobre DB de test; un test de integración abre `SimulatedWallet(initial_balance=200)`, simula 3 ciclos de apertura+settlement con `strategy` distintas, y verifica que `equity_after` compone correctamente día a día agrupado por `day_bucket`; ningún dato toca `Wallet`/`WalletLedger` reales (verificable con un assert de conteo de filas en las tablas reales antes/después = igual).

---

## 2. Motor de simulación de fills

**Hallazgo clave:** `simulate_limit_fill()` (fill_simulator.py:24-83) es genérico y reutilizable tal cual para las 4 estrategias — solo necesita `book_side`, `target_price`, `capital_usd`, `side`. Pero `simulate_arb_legs()` (fill_simulator.py:94-159) SÍ está acoplado: hardcodea `leg.get("book") == "polymarket"` (línea 124) y usa `PolymarketClient().get_order_book(token_id)` (línea 140) como única fuente de book. Descarta Kalshi/Manifold explícitamente por falta de cliente con order-book-depth wireado.

**Adaptación propuesta (no implementada aquí, solo especificada):**
- Nueva función `simulate_arb_legs_multi_platform(arb, capital_tiers, clients={})` en un módulo nuevo `src/core/orchestration/paper_fill_engine.py` que:
  1. Reutiliza `simulate_limit_fill()` tal cual (sin tocarla).
  2. Por cada pata del arb, despacha a un cliente de order-book según `leg["book"]`: Polymarket ya resuelto (usa el mismo `PolymarketClient.get_order_book`); Kalshi/Manifold requieren **primero confirmar si sus clientes actuales exponen bids/asks con tamaño** — esto es un riesgo de investigación, no asumido aquí (ver Riesgos).
  3. Para Strategy 1 universal (limit orders secuenciales), la simulación de fill debe ser **temporal, no solo de profundidad**: no alcanza con "¿hay suficiente tamaño al precio X?", hace falta simular si la orden se llenó **dentro de la ventana de dwell-time** antes de que el usuario cancele la que no llenó. `simulate_limit_fill()` no modela tiempo — esto es una extensión necesaria, candidata natural para que la diseñe Vesper junto con el sizing (mismo dominio matemático), o para una Fase 2b separada si Vesper no la cubre.
  4. Para Strategy 3 (same-market yes/no) y football 3-way, el fill se simula por N patas simultáneas de la MISMA plataforma — reutilizable con múltiples llamadas a `simulate_limit_fill()`, una por pata, sin necesitar lógica temporal (todas se colocan a la vez, no hay cancelación intermedia).
  5. Para Strategy 2 (cross-platform), reutilizable casi 1:1 con la lógica actual de `simulate_arb_legs()`, extendida a más de una plataforma con book real.

**Por qué "no fills optimistas sintéticos" es el requisito no negociable:** el usuario pidió explícitamente que el paper-trading refleje qué tan agresivamente compone una estrategia repetible — si el motor asumiera fill 100% instantáneo al precio de detección, el compounding mostrado sería ficticio y el ejercicio no tendría valor. Por eso cada `SimulatedBet.simulated_fill_json` debe guardar la salida real de `simulate_limit_fill()`/`simulate_arb_legs_multi_platform()`, no un valor hardcodeado.

**Paralelizable:** la extensión de `simulate_arb_legs()` a multi-plataforma (adaptador nuevo, sin tocar el archivo existente) es paralelizable con la Fase 1 (esquema de datos). NO es paralelizable con la parte temporal de Strategy 1 — esa depende de que Vesper entregue su fórmula de sizing, porque el "tamaño objetivo de la 3ra pata de cobertura" determina qué `capital_usd` pasarle a `simulate_limit_fill` en ese caso.

**Criterio de "terminado":** un test de integración corre `simulate_arb_legs_multi_platform` contra un arb sintético con patas en Polymarket+Kalshi (o el book real capturado en un fixture), y produce `fill_pct`/`avg_fill_price`/`classification` para cada pata sin lanzar excepción cuando falta el cliente de una plataforma (debe skipear documentando la limitación, igual que hoy hace con Kalshi/Manifold en el código real).

---

## 3. Adaptación del portfolio/allocator

**Qué reusar de `portfolio.py` tal cual (sin tocar el archivo):**
- El patrón `StrategyProfile` (dataclass) y sus derivadas `calmar`/`kelly`/`score` (líneas 57-77, 128-158) — la matemática de Kelly fraccional (`KELLY_FRACTION=0.25`, línea 50) y el piso de riesgo (`ARB_FLOOR_WEIGHT`, línea 52) son agnósticos de dominio (no dependen de que sea deporte o prediction market), reutilizables 1:1.
- `_estimate_daily_roi()` (líneas 250-263) — la fórmula `Σ peso_i · roi_por_bet_i · bets_dia_i · stake_fraction` es válida para las 4 estrategias de arb siempre que se reemplace `_EST_DAILY_BETS` (línea 240-245, hoy calibrado para deportes/bookies) con volúmenes reales observados de arb-scanning en prediction markets — dato que el sistema YA tiene (logs de detección de Strategy 2/3/football), no hay que inventarlo.
- El patrón de persistencia vía `AppConfig` (JSON) — reutilizable con una nueva key (ej. `paper_portfolio_v1`) para no pisar `strategy_portfolio_v1` (que sigue siendo del sistema real/legacy, aunque fosilizado).

**Qué reemplazar (no editar el archivo, crear uno nuevo paralelo — ej. `src/core/orchestration/paper_portfolio.py`):**
- `STRATEGY_BUCKET` (líneas 40-45) — hoy mapea `football_elo_value`/`nfl_elo_value`/`nhl_elo_value`. El nuevo dict debe mapear las 4 estrategias reales: `{"low_risk_arb": "low_risk_arb", "strategy_1_universal": "medium_value_stat", "strategy_2_cross_platform": "low_risk_arb", "strategy_3_same_market": "low_risk_arb", "football_3way": "low_risk_arb"}` — **el bucketing exacto es un [DECISIÓN SARA]** (ver sección 4), esto es solo una propuesta de arranque porque Strategy 2/3/football son arb puro (riesgo ~0 por diseño, dado que garantizan ROI neto de fees) mientras que Strategy 1 universal tiene riesgo real de no-fill de la 3ra pata (más parecido a "medium_value_stat" en términos de incertidumbre, aunque no sea exactamente lo mismo que un value-bet deportivo).
- `SPORT_TO_STRATEGY` en `strategy_allocator.py` (línea 40-44) — no aplica en absoluto (no hay deportes), se reemplaza por un mapeo de estrategia→estrategia idéntico (posiblemente innecesario, redundante con el bucket).
- El self-backtest histórico (`_load_backtest_profile`, líneas 107-125, que lee `self_backtest_<strategy>` de `AppConfig`) — para el paper-trading el "backtest" ES el propio historial de `SimulatedLedger`: en vez de correr un self-backtest offline separado, el nuevo `paper_portfolio.py` debe derivar `StrategyProfile.roi`/`win_rate`/`n_bets` directamente agregando `SimulatedBet` por `strategy` (fuente `"realized"`, no `"backtest"` — el enum `source` en `StrategyProfile` ya soporta esto, línea 74).

**Qué NO tocar de `strategy_allocator.py`:** `classify_opportunity()` (líneas 131-140) clasifica por substrings en `o["tipo"]` — sirve tal cual siempre que los generadores de oportunidades de las 4 estrategias sigan poniendo `"arb"` en el campo `tipo` para Strategy 2/3/football (ya lo hacen, son arbs) y algo reconocible como `"stat"/"model"/"value"` para Strategy 1 universal SI se decide tratarla como bucket separado (ver decisión de bucketing arriba). Esto exige que el generador de oportunidades de Strategy 1 (que construye Vesper/el implementador después) ponga el campo `tipo` de forma consistente — anotarlo como dependencia cruzada.

**Target diario reinvertido:** `build_portfolio(target_daily_roi=0.02)` (default hoy 0.01, línea 160) debe invocarse con `0.02` para alinear con el pedido del usuario (>=2% diario) — parámetro, no requiere tocar el archivo.

**Paralelizable:** el nuevo `paper_portfolio.py` (agregación desde `SimulatedBet`) depende de que exista el esquema de la Fase 1, pero NO depende de la fórmula de Vesper ni del motor de fills — puede construirse con datos sintéticos de prueba en paralelo a la Fase 2.

**Criterio de "terminado":** `build_portfolio` (versión paper) corriendo sobre un `SimulatedWallet` con >=20 `SimulatedBet` sintéticas repartidas en las 4 estrategias devuelve pesos que suman 1.0, marca `feasible_2pct` correctamente, y NO importa nada de `portfolio.py`/`strategy_allocator.py` que dependa de deportes (verificable por ausencia de referencias a `football_elo_value` etc. en el nuevo módulo).

---

## 4. Fases y dependencias (para lanzar agentes en la próxima ronda)

| Fase | Qué construye | Depende de | Paralelizable con |
|---|---|---|---|
| **A. Esquema de datos** (`src/database/models/paper_wallet.py`) | `SimulatedWallet`/`SimulatedLedger`/`SimulatedBet`, migración/creación de tablas | Nada (solo este documento) | B, C — arranca YA |
| **B. Motor de fills multi-plataforma** (`src/core/orchestration/paper_fill_engine.py`) | Adaptador que reusa `simulate_limit_fill` para Strategy 2/3/football; investiga si Kalshi/Manifold exponen order-book | Nada para 2/3/football. La parte temporal de Strategy 1 depende de Vesper | A, C (para 2/3/football) — NO para la parte de Strategy 1 |
| **C. Paper-portfolio/allocator** (`src/core/orchestration/paper_portfolio.py`) | Agregación de `StrategyProfile` desde `SimulatedBet`, pesos diarios, target 2% | Esquema de A (para leer `SimulatedBet`), pero puede mockear datos mientras A se termina | A (en paralelo con datos sintéticos), B |
| **D. Motor de ejecución de ciclo diario** (nuevo, ej. `src/core/orchestration/paper_trading_cycle.py`) | Orquesta: escanea oportunidades reales (reusa `scanning.py`/`cross_platform_deep.py`/`same_market_arb.py`/`football_3way_arb.py`), pide fill simulado a B, decide mezcla vía C, escribe A | A + B + C completas | No — es integración final, secuencial |
| **E. Endpoints backend** (`src/web/blueprints/paper_trading_bp.py`) | `/api/paper/wallet`, `/api/paper/ledger?days=N`, `/api/paper/portfolio`, `/api/paper/bets` | A completa (lee de las tablas); puede stub-earse antes de D | C, D parcialmente |
| **F. Frontend** (ver sección 5) | Página/componentes Vue de curva de compounding + desglose por estrategia | E completa (necesita contrato de API real) | Nada — última fase |
| **1U. Strategy 1 universal — implementación** | Fuera del scope de este plan (Vesper/implementador aparte) | Fórmula de sizing de Vesper | Puede correr en paralelo a A/B/C/E, converge en D |

**Camino crítico:** A → (paralelo B,C) → D → E → F. La parte de Strategy 1 en B/D es el único punto que bloquea contra el output de Vesper — todo lo demás (Strategy 2/3/football, que ya están implementadas y no dependen de matemática nueva) puede avanzar sin esperarla.

---

## 5. Frontend (solo qué haría falta, no diseño de componente)

- Endpoint `/api/paper/ledger?wallet_id=X&days=30` devolviendo serie `[{day_bucket, equity_after}]` — insumo directo para una curva de línea (compounding día a día), análogo a lo que ya existe para gráficos reales de Aegis (`aegis-dashboard-charts-implementation`, memoria) pero apuntando a la tabla nueva.
- Endpoint `/api/paper/portfolio` devolviendo el output de `paper_portfolio.build_portfolio()` (pesos por estrategia, `expected_daily_roi`, `feasible_2pct`) — insumo para un desglose tipo pie/bar por estrategia.
- Endpoint `/api/paper/bets?strategy=X&status=open|settled` — insumo para una tabla de detalle (igual patrón que `WalletCard.vue`/`api_wallet` en `wallet.py:139-181`, pero namespace `paper`).
- Vista nueva candidata: `frontend/src/pages/PaperTradingPage.vue` (no existe hoy — las 3 páginas actuales son Dashboard/Player/Audit) con: card de balance actual + P&L acumulado (clonando patrón de `WalletCard.vue`), gráfico de línea de equity diaria, y un breakdown por estrategia (4 barras o donut). Ruteo nuevo en el router de Vue (`frontend/src/pages/` sugiere un router central, no leído en detalle en esta pasada — anotar como pendiente de confirmar ubicación exacta del router al implementar).
- Todo esto es Fase F, la última, y depende 100% de que los endpoints (Fase E) devuelvan contratos estables — no diseñar el componente antes de que el contrato de API esté cerrado.

---

## 6. Puntos de decisión de Sara / usuario

**[DECISIÓN SARA #1] — ¿Tiempo real o backtest histórico?**
¿El paper-trading escanea eventos EN VIVO (mismo pipeline de `scanning.py` corriendo en tiempo real, con fills simulados contra el book real del momento) o corre un backtest contra datos históricos que el sistema ya capturó (logs de arbs detectados en rondas anteriores)?
- Recomendación no vinculante: tiempo real. El pedido del usuario es "ver cómo compone" — un backtest sobre datos ya vistos no demuestra nada nuevo sobre el comportamiento futuro del sistema, y el motor de fills (sección 2) fue diseñado justamente para simular contra profundidad real actual, no reconstruible fácilmente en retrospectiva para Kalshi/Manifold (no hay snapshots históricos de su order book guardados, según lo investigado).

**[DECISIÓN SARA #2] — ¿Se resetea el paper-wallet al cambiar de estrategia?**
Si se implementa un cambio en Strategy 1 (post-diseño de Vesper) o se ajusta el allocator, ¿el `SimulatedWallet` mantiene continuidad histórica (el compounding sigue desde donde estaba) o se resetea a 200 USDC para no contaminar la curva con reglas viejas?
- Recomendación no vinculante: mantener continuidad por defecto (el campo `reset_count` en el esquema ya soporta resets explícitos y auditables), pero permitir reset manual explícito vía endpoint/UI cuando el usuario lo pida — nunca automático en cada deploy, porque perdería exactamente el dato de compounding que el usuario quiere ver.

**[DECISIÓN SARA #3] — ¿Capital compartido o separado por estrategia?**
¿Las 4 estrategias compiten por el mismo pool de 200 USDC (un solo `SimulatedWallet`, el allocator decide cuánto asigna a cada una por ciclo) o cada estrategia tiene su propio `SimulatedWallet` de 200 USDC independiente (4 curvas de compounding paralelas, sin competencia por capital)?
- Recomendación no vinculante: un solo pool compartido con `strategy_scope="all"` como caso principal (el esquema ya soporta múltiples wallets si se quiere comparar), porque es lo que más se parece al comportamiento real que el usuario tendría con 200 USDC reales — y es precisamente lo que el allocator adaptado (sección 3) está diseñado para resolver. Pero dejar la opción de instanciar wallets separados por estrategia como herramienta de diagnóstico/comparación, no como el modo por defecto.

**[DECISIÓN SARA #4] — Bucketing de Strategy 1 universal en el allocator.**
Sección 3 propuso tentativamente tratar Strategy 1 como `"medium_value_stat"` (riesgo intermedio, por el riesgo de no-fill de la 3ra pata) y Strategy 2/3/football como `"low_risk_arb"` puro. Esto no está confirmado — depende de qué tan predecible resulte el fill-rate de Strategy 1 una vez Vesper entregue la fórmula.
- Recomendación no vinculante: mantenerlo abierto explícitamente hasta ver los primeros datos reales de fill-rate de Strategy 1 en paper — no fijar el bucket antes de tener evidencia.

**[DECISIÓN SARA #5] — Umbral real de Kalshi/Manifold order-book depth (bloqueante de investigación, no de diseño).**
No confirmé en esta pasada si los clientes actuales de Kalshi/Manifold exponen bids/asks con tamaño (solo confirmé que Polymarket sí, vía `fill_simulator.py`). Si no lo exponen, Strategy 2/3/football en paper-trading tendrían que simular fills solo para la pata Polymarket y asumir optimismo (o pesimismo conservador) en las patas Kalshi/Manifold — lo cual violaría el requisito de "no fills sintéticos optimistas" a medias.
- Recomendación no vinculante: asignar esta verificación como primer sub-paso de la Fase B, antes de escribir el adaptador — es investigación de 30-60 min (grep en los clientes existentes + un fetch de prueba), no una decisión de diseño.

---

## 7. Riesgos conocidos

- **Kalshi/Manifold order-book depth sin confirmar** (ver decisión #5) — riesgo de que el motor de fills termine siendo Polymarket-only en la práctica, lo cual limitaría fuertemente qué tan realista es el paper-trading de Strategy 2/football (que casi siempre tienen una pata no-Polymarket).
- **Strategy 1 universal es un hueco de diseño en paralelo** — cualquier fecha de "Fase D completa" depende de un entregable externo (Vesper) fuera del control de esta planificación; si Vesper tarda, Fases A/B(parcial)/C/E pueden avanzar igual con Strategy 2/3/football solamente, mostrando compounding con 3 de 4 estrategias.
- **`_EST_DAILY_BETS` (portfolio.py:240-245) es un placeholder calibrado para deportes** — reemplazarlo con datos reales de volumen de arb detectado requiere agregar telemetría existente (logs de scan) que no confirmé que esté en un formato fácilmente consultable; podría necesitar una sub-tarea de ETL antes de que `_estimate_daily_roi` sea confiable para prediction markets.
- **Ubicación del router de Vue no confirmada** — no leí el archivo de configuración de rutas del frontend en esta pasada; la Fase F necesita ese archivo identificado antes de crear `PaperTradingPage.vue`.
- **Riesgo de reincidencia del bug de `active_platform`** — el esquema en sección 1 fija `platform="paper"` como literal explícito precisamente para evitar el patrón que causó 4+ incidentes en producción (ver memoria); cualquier implementador debe grep-ear su propio código nuevo contra `state.active_platform` antes de mergear, como ya es práctica recurrente documentada en el proyecto.
- **No confirmé el punto exacto de registro de tablas** (`create_tables([...])` en `database.py`) — dejar como primer paso técnico de Fase A, no asumido resuelto aquí.
