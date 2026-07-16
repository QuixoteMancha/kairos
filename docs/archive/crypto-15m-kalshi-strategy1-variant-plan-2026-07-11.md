# Plan: variante "crypto_15m" de Strategy 1 (Kalshi)

Autora: Nora. Reporta a: Sara. Fecha: 2026-07-11.

## 1. Alcance y supuestos

**Investigado con evidencia real en esta sesión:**
- `src/scrapers/kalshi_client.py:57` confirma que `category` es siempre `"uncategorized"` en el schema en vivo hoy — no hay filtro server-side por categoría.
- `src/core/orchestration/portfolio.py:90/104`: `STRATEGY1_CATEGORIES` es una tupla plana, `STRATEGY1_CALIBRATED_CATEGORIES` es un `set()` vacío hoy (fail-closed real, no placebo) — el patrón de gate a seguir existe y está vivo.
- `src/core/strategies/limit_order_formation.py:50/70/104-115`: `estimate_dynamic_epsilon()` ya tiene el fail-closed exacto (`_SUPPORTED_CATEGORIES`, `_NO_DYNAMIC_EPSILON_CATEGORIES`) que la nueva categoría debe replicar, no reinventar.
- `src/core/strategies/event_volatility/tennis_markov.py`: shape de referencia para un modelo por categoría — `calibrate_*()` separado de `estimate()`, y explícitamente documenta de dónde sale cada número (nunca inventado).
- `paper_trading_cycle.py:755-813` (`_run_strategy1_pass`): state machine actual de 2 patas (`observing → awaiting_hedge → resolved_*`), documentado con sus limitaciones (indeterminate cuando cruzan ambas patas en el mismo ciclo de 5 min — **esto es una tensión directa con la sub-estrategia A, ver Riesgo R1**).
- Búsqueda web (sin acceso directo a la API real desde este entorno) confirma que Binance `/api/v3/klines` es público, sin auth, con peso liviano (~2-5 de 6000/min) — candidato viable de baja fricción. Coinbase y Kraken tienen endpoints públicos equivalentes pero esta sesión no verificó sus rate limits con la misma profundidad.

**Asumido por falta de acceso (marcado explícitamente, no verificado empíricamente):**
- El patrón real de `ticker`/`event_ticker` para mercados crypto de 15 min en Kalshi (ej. algo como `KXBTC-25JUL11...` o similar) — nadie en esta sesión hizo una llamada real a `GET /markets` filtrando por esos candidatos. **Esto es Fase 0, no se puede saltar.**
- Si Kalshi mismo expone un endpoint de "candles"/historial de precio propio (el pedido del usuario menciona esta posibilidad como alternativa a un exchange externo) — no confirmado.
- Latencia real punta a punta (Kalshi orderbook fetch + exchange fetch en el mismo ciclo) — no medida.

**Fuera de alcance de este plan (explícito):**
- Cualquier ejecución de órdenes reales — no existe en Kairos y este plan no la asume ni la construye.
- Calibración real de coeficientes de correlación precio↔probabilidad — Fase 2 solo define CÓMO se calibrará cuando haya datos, no calibra con datos inventados.
- Manifold — fuera de scope activo del proyecto (decisión previa del usuario, no relacionada a esta feature).

---

## 2. Fases de trabajo

### Fase 0 — Investigación/verificación previa

**CORRECCIÓN (Sara, 2026-07-11):** el host correcto y ya confirmado-en-vivo por este mismo cliente es `https://external-api.kalshi.com` (`KALSHI_BASE` en `kalshi_client.py:41`, docstring del archivo lo explicita: "NOT api.kalshi.com... NOT api.elections.kalshi.com como host de la API pública"), no `api.elections.kalshi.com` como decía el borrador original de este plan.

**Qué se hace:** llamadas reales (no mockeadas) a `GET https://external-api.kalshi.com/trade-api/v2/markets` (vía `KalshiClient.get_markets`, `src/scrapers/kalshi_client.py:212`) filtrando por candidatos de prefijo (`KXBTC`, `KXETH`, o los que surjan de listar eventos de `get_events()` y buscar "15 minute"/"15m" en título/subtítulo) para confirmar el patrón real de ticker y la cadencia real de apertura/cierre de estos mercados (¿arrancan cada 15 min en punto? ¿hay overlap?).

**AGREGADO (Sara, 2026-07-11, resolución de R1):** también hay que verificar EN VIVO `GET https://external-api.kalshi.com/trade-api/v2/markets/trades` (endpoint de tick-por-trade, público sin auth — investigado por Priya hace 6 días, memoria `kairos-strategy4-kalshi-manifold-data-research`, pero NUNCA probado en vivo desde este entorno todavía): confirmar shape de respuesta real, si soporta filtro por `ticker`/rango de tiempo o si hay que traer todo y filtrar localmente, paginación, y rate limit real observado con polling sostenido. Esto es lo que resuelve R1 (saber el orden real de cruce de niveles, no inferirlo de snapshots).

En paralelo, un llamado real a Binance `GET /api/v3/klines?symbol=BTCUSDT&interval=1m&limit=5` (y equivalente en Coinbase `GET /products/BTC-USD/candles` y Kraken `GET /0/public/OHLC`) para confirmar shape de respuesta, timestamp semantics (open o close de la vela) y rate limit real observado.
**Archivo:** no se toca código de producción; esto es un script exploratorio desechable (ej. `scripts/probes/probe_kalshi_crypto15m.py`, siguiendo el patrón ya usado y luego archivado de `scripts/probes/` — ver que esa carpeta fue recién limpiada en esta sesión, así que este probe debe eliminarse o moverse a `forgotten/` al cerrar la fase, no quedar suelto).
**Terminado cuando:** Sara tiene, por escrito, (a) el patrón real de ticker/event_ticker confirmado con al menos 3 ejemplos reales de mercados crypto de 15m en Kalshi, (b) el shape/paginación/rate-limit real de `/markets/trades` confirmado en vivo, (c) la decisión de qué exchange usar (ver `[DECISIÓN SARA]` abajo) con su rate limit real medido, (d) confirmación de si Kalshi expone histórico de precio propio del subyacente o si solo hace falta el tick de probabilidad (el precio real del subyacente sigue viniendo del exchange, per decisión ya confirmada del usuario).
**Paralelizable:** SÍ — la verificación de Kalshi y la de exchanges externos son independientes entre sí, dos agentes pueden correr esto en simultáneo.

### Fase 1 — Recolección de datos

**ACTUALIZADO (2026-07-11, decisión del usuario sobre R1):** el usuario resolvió R1 explícitamente: en vez de snapshots periódicos de 1 min (que no alcanzan para determinar el orden real de cruce), hay que mantener un **registro punto a punto (tick-level) real** de la probabilidad vía la API de Kalshi. Investigación previa de esta sesión (Priya, memoria `kairos-strategy4-kalshi-manifold-data-research`, re-verificada por Sara ahora): Kalshi expone `GET external-api.kalshi.com/trade-api/v2/markets/trades` — público, sin auth, **tick-por-trade real** (no solo book actual). Confirmado con grep: `KalshiClient` (`src/scrapers/kalshi_client.py`) NO tiene todavía ningún método que llame a este endpoint (`get_markets`/`get_events`/`get_order_book` son los únicos existentes) — es trabajo nuevo real, no wiring.

El usuario también pidió explícitamente que este historial sea de **alcance general** (todos los eventos de Kalshi, no solo crypto 15m) para poder estudiar más adelante, con datos reales, cómo reacciona el mercado (volatilidad media) a distintos tipos de evento — no hardcodear la recolección solo a la categoría crypto.

**Qué se construye (revisado):**
- Nuevo método `KalshiClient.get_trades(ticker, ...)` (o `get_market_trades`) que llame a `/trade-api/v2/markets/trades`, paginando si hace falta (verificar en Fase 0 si el endpoint soporta filtro por ticker/rango de tiempo o si hay que traer todo y filtrar localmente).
- Tabla nueva de propósito GENERAL (no crypto-específica), ej. `KalshiMarketTick` en `src/database/models/domain.py` — `(kalshi_ticker, event_ticker, category_hint, timestamp, yes_price, no_price, trade_count_or_volume)`, aplicable a cualquier mercado de Kalshi, no solo crypto 15m.
- Tabla nueva `CryptoPriceSnapshot` (sin cambios respecto al plan original) — serie de 1 min del exchange elegido en Fase 0.
- Collector nuevo para el tick-history de Kalshi (cadencia de polling a definir en Fase 0 según el rate limit real del endpoint — un tick-history real puede no necesitar polling tan frecuente si el endpoint devuelve todo el historial reciente en una sola llamada, a diferencia de un snapshot que sí necesita polling constante).
- Collector del exchange (sin cambios).
- Worker de orquestación: nuevo hilo/tick en `workers.py` (verificar cadencia real de workers existentes antes de asumir que hace falta uno nuevo).
**Terminado cuando:** tras correr contra mercados reales, la tabla de ticks de Kalshi tiene filas con el orden real de trades (no inferido) y sin gaps mayores a lo que el rate limit del endpoint permita razonablemente; la tabla de precio del exchange sigue el mismo criterio que el plan original.
**Paralelizable:** el collector de Kalshi (ticks) y el del exchange (precio) son independientes, pero AMBOS dependen de que Fase 0 confirme el shape real de `/markets/trades` (parámetros de filtro, paginación, rate limit) — no se puede empezar Fase 1 sin cerrar Fase 0.

### Fase 2 — Modelo de indicadores + correlación
**Qué se construye:** módulo nuevo `src/core/strategies/event_volatility/crypto_15m.py`, mismo shape que `tennis_markov.py` (`calibrate_*()` separado de `estimate()`, `incremental_recalibrate()` si aplica).
- Indicadores sobre `CryptoPriceSnapshot`: ATR rolling (ventana corta, ej. 14 barras de 1 min — a definir), estocástico %K/%D, std rolling de retornos.
- Formulación de correlación propuesta (a validar por Sara, NO se calibra con números inventados): tasa de variación del precio normalizada por ATR (una especie de "z-score de momentum") + tiempo restante hasta cierre (`seconds_to_close / 900`) como los dos regresores de un modelo simple (ej. regresión logística o lookup table 2D: bucket de momentum-normalizado × bucket de tiempo-restante → probabilidad esperada de que el precio cruce el 50%). **Esto arranca con un placeholder que devuelve "insuficientes datos" (fail-closed) hasta que Fase 1 haya acumulado una muestra mínima real** — el umbral de muestra mínima es una decisión de Sara (ver abajo), no un número que Nora invente.
- `calibrate_crypto_momentum_correlation(rows)` recibe filas reales de ambas tablas (join por timestamp/ticker) y ajusta los coeficientes — nunca hardcodeados de antemano.
**Terminado cuando:** con datos sintéticos de test (fixtures, no producción) el modelo calibra coeficientes deterministas y reproducibles, y con menos del mínimo de muestra definido devuelve explícitamente "no calibrado" (mismo patrón que `_NO_DYNAMIC_EPSILON_CATEGORIES`/`STRATEGY1_CALIBRATED_CATEGORIES` — nunca opera con confianza falsa).
**Paralelizable:** NO con Fase 1 (necesita datos reales fluyendo, aunque sea poco, para tener fixtures representativas) pero SÍ puede empezar su esqueleto/tests con datos sintéticos mientras Fase 1 termina de estabilizarse.

### Fase 3 — Lógica de decisión + sub-estrategias
**Qué se construye:** módulo nuevo (o extensión de `crypto_15m.py`) con:
- `classify_regime(indicators) -> Literal["lateral", "trending", "divergent"]`: umbrales iniciales sugeridos como punto de partida a calibrar — ej. `|momentum_zscore| < 1.0` y estocástico sin divergencia => lateral; `|momentum_zscore| >= 1.5` => trending; estocástico %K/%D cruzando en dirección opuesta al precio => divergent. **Estos números son un punto de partida documentado explícitamente como no-definitivo, a recalibrar con Fase 2 una vez haya datos reales.**
- Sub-estrategia A (doble límite secuencial en ~48): nuevo estado de state machine, ej. `crypto15m_A_leg1_pending → crypto15m_A_leg1_filled_awaiting_remontada → crypto15m_A_resolved_arb/indeterminate`, extendiendo el patrón de `_run_strategy1_pass` pero con su propia función (ej. `_run_strategy1_crypto15m_pass` en `paper_trading_cycle.py`) dado que la mecánica de "esperar que el lado contrario cruce el mismo nivel tras el primer fill" no existe hoy en el state machine genérico.
- Sub-estrategia B (market + hedge-limit asimétrico): estado propio (`crypto15m_B_leg1_market_filled → crypto15m_B_awaiting_asymmetric_hedge → resolved_*`), con el nivel de la segunda limit derivado de la formulación de Fase 2 (no un 48 fijo — la mecánica de "arb asimétrico" implica que el segundo nivel depende de a qué precio se llenó la pata de market).
- Bifurcación: `classify_regime()` decide A vs B al momento de proponer la primera orden.
**Terminado cuando:** tests unitarios del state machine (transiciones válidas/inválidas, incluyendo el caso "ambas patas cruzan 48 en la misma ventana de 1 min" — el mismo problema de indeterminate que ya existe en Strategy 1 genérico, ver R1) pasan con fixtures sintéticas de las 2 sub-estrategias.
**Paralelizable:** el diseño de A y B son independientes entre sí (dos agentes en simultáneo), pero ambos dependen de que `classify_regime()` (compartido) esté cerrado primero.

### Fase 4 — Integración con Strategy 1 existente
**Qué se construye:**
- Agregar `"crypto_15m"` a `STRATEGY1_CATEGORIES` en `src/core/orchestration/portfolio.py:90`, **sin** agregarla a `STRATEGY1_CALIBRATED_CATEGORIES` (línea 104) hasta que Fase 2 tenga calibración real documentada — mismo patrón exacto que baseball hoy.
- Agregar `"crypto_15m"` a `_SUPPORTED_CATEGORIES` en `limit_order_formation.py:50`, y decidir si entra a `_NO_DYNAMIC_EPSILON_CATEGORIES` (línea 70) inicialmente (probablemente sí, por la misma razón fail-closed que aplica a baseball hoy — sin datos calibrados, no debe alimentar el epsilon dinámico de otras categorías ni el suyo propio con confianza falsa).
- Cablear la nueva subrutina de Fase 3 dentro de `_run_strategy1_pass` (o como un pase paralelo `_run_strategy1_crypto15m_pass` invocado desde el mismo ciclo en `paper_trading_cycle.py`), filtrando eventos por la categoría nueva.
**Terminado cuando:** un ciclo de paper-trading corrido end-to-end contra fixtures (no producción real todavía) genera bets con `strategy="strategy_1_universal"`, `resolution_meta` conteniendo el sub-tipo (`crypto15m_A`/`crypto15m_B`), y el portfolio scorer (`portfolio.py:213/628`) trata la categoría como no-calibrada (no le asigna capital real hasta decisión explícita de Sara/usuario).
**Paralelizable:** NO — depende de que Fase 3 esté cerrada (necesita el state machine real para cablear el pase).

---

## 3. Puntos de decisión de Sara / usuario

## FASE 0 — CERRADA (2026-07-11, verificación empírica real por Priya, re-verificada por Sara con llamadas propias)

- **Mercados crypto 15m confirmados**: 12 series (`KXBTC15M`, `KXETH15M`, `KXXRP15M`, `KXSOL15M`, `KXADA15M`, `KXBNB15M`, `KXDOGE15M`, `KXNEAR15M`, `KXTON15M`, `KXZEC15M`, `KXBCH15M`, `KXHYPE15M`) vía `GET /trade-api/v2/series?category=Crypto`, `frequency=="fifteen_min"`. Sara re-confirmó esta lista exacta con una llamada propia. Patrón de ticker: `{SERIES}-{YY}{MMM}{DD}{HHmm}-{mm}` (ej. `KXBTC15M-26JUL110345-45`). Arrancan exacto cada 15 min UTC (`:00/:15/:30/:45`), cero overlap entre ventanas consecutivas, 1 solo mercado `open` por evento a la vez.
- **`/markets/trades` CONFIRMADO como la solución real a R1**: Sara re-verificó con una llamada propia usando un ticker real devuelto por Kalshi — filtro `ticker=` funciona de verdad (todos los trades devueltos correspondían al ticker pedido), shape exacto: `{cursor, trades: [{trade_id, ticker, created_time, yes_price_dollars, no_price_dollars, count_fp, taker_side, taker_outcome_side, taker_book_side, is_block_trade}]}`, orden descendente por `created_time`. Paginación vía `cursor` confirmada por Priya. Filtro `min_ts`/`max_ts` confirmado. Rate limit real observado: ~2.1 req/s sostenido sin 429 en 30 llamadas seguidas (Kalshi no expone headers `X-RateLimit-*` en este endpoint — no se caracterizó el techo real por no arriesgar un burst agresivo contra la API de producción de un tercero sin autorización).
- **Hallazgo extra no buscado**: Kalshi tiene candlesticks NATIVAS propias (`GET /series/{s}/markets/{t}/candlesticks`, hasta 5000 velas/request) del `yes_price` (no del subyacente) — no reemplaza el precio del exchange externo (decisión ya tomada por el usuario), pero es una fuente adicional gratis a considerar para poblar `KalshiMarketTick` en vez de/junto con `/markets/trades` en Fase 1.
- **Exchange elegido: Binance** — confirmado en vivo por Priya y consistente con la investigación previa. Shape de `/api/v3/klines` confirmado (array de 12 campos, no dict — ojo al parsear en Fase 1), timestamp semantics confirmado (campo 0 = open time, campo 6 = close time), peso real `2` de `6000`/min — margen enorme para 1 llamada/min. Coinbase/Kraken NO se probaron en vivo (no hizo falta, Binance funcionó sin fricción) — quedan como fallback documentado, no verificado, si Binance fallara en producción más adelante.
- **Script exploratorio** (`scripts/probes/probe_kalshi_crypto15m.py`): decisión de Sara — se CONSERVA, pero marcado explícitamente como test manual/opt-in (skip por defecto en CI, solo corre bajo un flag explícito), no como test automático de la suite. Razón: documenta comportamiento real de una API de terceros que ya cambió de shape una vez sin aviso (ver `kalshi_client.py:88-109`, el bug histórico `yes_bid`→`yes_bid_dollars`) — es exactamente el tipo de evidencia que ayuda a detectar un cambio de shape futuro rápido, pero no debe correr en cada `pytest` normal (depende de red real y de terceros).

## Decisiones que siguen abiertas (Fase 1 en adelante)

- `[DECISIÓN SARA]` Cadencia real del collector — con `/markets/trades` confirmado (tick real, no snapshot), la cadencia de POLLING puede ser más relajada que "1 min" (el endpoint ya trae el historial reciente completo por ticker); a definir en Fase 1 según cuántos ticks por minuto se observen en la práctica para mercados activos.
- `[DECISIÓN SARA]` Tamaño de muestra mínima antes de que Fase 2 deje de devolver "no calibrado" — número que depende de cuántos ciclos completos de 15 min hacen falta para un ajuste estadísticamente razonable; Nora no lo fija, Priya tampoco.
- `[DECISIÓN SARA]` Umbrales iniciales de `classify_regime()` (propuestos como punto de partida en Fase 3) — deben ser revisados/ajustados por Sara antes de codear, no tomados como definitivos.
- `[DECISIÓN SARA]` Elegir entre `/markets/trades` (tick-por-trade genérico, cualquier mercado) y las candlesticks nativas de Kalshi (`/series/{s}/markets/{t}/candlesticks`, ya agregadas a nivel de vela) para poblar `KalshiMarketTick` — o ambas, con propósitos distintos (ticks para reconstruir orden real de cruces en la ventana activa, candlesticks para historial agregado de eventos ya cerrados).

---

## 4. Riesgos conocidos

- **R1 — Tensión estructural con la mecánica de Sub-estrategia A — RESUELTO EN DISEÑO (2026-07-11), pendiente de verificación empírica en Fase 0:** la mecánica de "poner limit en 48 en el lado ganador, luego limit en 48 en el lado que remonta" depende de detectar el orden real de cruces dentro de una ventana muy corta (5-15 min). El state machine actual de Strategy 1 ya declara "indeterminate" cuando ambas patas cruzan su nivel en el mismo ciclo de 5 min (`paper_trading_cycle.py:805-811`) — un muestreo periódico (aunque sea de 1 min) no resuelve esto de fondo, solo lo mitiga. Decisión del usuario: en vez de inferir el orden por muestreo, mantener un registro punto-a-punto (tick-level) REAL de la probabilidad vía `GET external-api.kalshi.com/trade-api/v2/markets/trades` (tick-por-trade, público sin auth, investigado por Priya pero nunca probado en vivo — ver Fase 0 actualizada). Esto también responde a un pedido más amplio del usuario: este historial de ticks debe ser de alcance GENERAL (todos los eventos de Kalshi, no solo crypto 15m) para poder estudiar después, con datos reales, cómo reacciona el mercado (volatilidad media) a distintos tipos de evento — no es una tabla crypto-específica, es infraestructura de registro reutilizable. Ver Fase 1 actualizada (`KalshiMarketTick`, tabla de propósito general). Sigue pendiente confirmar en Fase 0 que el endpoint efectivamente permite reconstruir el orden real de cruces (paginación/rate-limit/filtro por ticker) antes de dar esto por cerrado del todo.
- **R2 — Patrón de ticker crypto 15m no verificado:** toda la Fase 1 en adelante depende de que Fase 0 encuentre un patrón real y estable. Si Kalshi cambia el naming o no hay suficiente volumen/liquidez en estos mercados hoy, el resto del plan queda bloqueado.
- **R3 — Reloj desincronizado entre Kalshi y el exchange:** correlacionar dos series de tiempo de fuentes distintas (Kalshi 1 min, exchange 1 min) introduce riesgo de desfase de reloj/latencia de red que puede contaminar la calibración de Fase 2 si no se maneja explícitamente (timestamps de servidor vs. de recepción).
- **R4 — Rate limits no verificados empíricamente para Coinbase/Kraken:** solo Binance fue caracterizado con algo de profundidad vía búsqueda web; si Sara elige otro exchange, Fase 0 debe re-verificar esto contra la API real, no asumir lo encontrado en esta búsqueda.
- **R5 — Gate de calibración es fácil de "trampear" bajo presión de resultados:** dado que `STRATEGY1_CALIBRATED_CATEGORIES` es un simple `set()` en código, cualquier futuro agente apurado podría agregar `"crypto_15m"` sin datos reales detrás. Vale la pena que Sara deje una nota/test explícito que falle si esta categoría se marca calibrada sin un changelog/doc de calibración adjunto (mismo espíritu que ya existe en portfolio.py).
