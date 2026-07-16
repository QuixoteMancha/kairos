# Plan: modelo de sobre/infravaloración pre-evento para Strategy 1 (tenis/fútbol/clima/basketball/baseball)

Autora: Nora. Reporta a: Sara. Fecha: 2026-07-11.

Formato/nivel de detalle siguiendo `docs/crypto-15m-kalshi-strategy1-variant-plan-2026-07-11.md` (leído íntegro antes de escribir este plan).

## 1. Alcance y supuestos

**Investigado con evidencia real en esta sesión (código leído completo, no resumido de memoria):**

- `src/core/strategies/event_volatility/base.py:19-59` (`DwellBand`) y los 5 modelos de categoría (`tennis_markov.py`, `football_poisson.py`, `weather_climatology.py`, `basketball_winprob.py`, `baseball_winexp.py`) leídos completos hoy.
- `src/core/strategies/limit_order_formation.py:50` (`_SUPPORTED_CATEGORIES = {"tennis", "football", "soccer", "basketball", "baseball"}`) y `:85` (`_NO_DYNAMIC_EPSILON_CATEGORIES = {"baseball"}`) y `:100` (`estimate_dynamic_epsilon()`) — mecanismo de ponderación por volatilidad YA existente y confirmado en vivo, se reutiliza, no se reinventa.
- `src/core/orchestration/portfolio.py:90` (`STRATEGY1_CATEGORIES` incluye ya `"tennis", "football", "soccer", "basketball", "baseball", "weather", "crypto_15m"`) y `:112` (`STRATEGY1_CALIBRATED_CATEGORIES: set[str] = set()` — sigue vacío hoy, confirmado, fail-closed real).
- `src/core/orchestration/paper_trading_cycle.py:1270-1350` (`_run_strategy1_crypto15m_b_pass`) leído como referencia real de la mecánica "market YA + limit resting asimétrico" que el usuario pidió replicar en espíritu (no en código: la variante crypto_15m es de otra categoría y no se toca).

**Estado real de cada modelo, por categoría, tras leer el código completo (esto era un punto de decisión que el usuario pidió NO asumir):**

| Categoría | ¿Ya separa fair-value PRE-evento de lógica in-play? | Evidencia |
|---|---|---|
| **basketball** | SÍ, ya separado y trivialmente reutilizable. | `basketball_winprob.py:43-45` (`_elo_win_prob`) es una función standalone de 2 líneas que da la probabilidad pre-partido pura a partir de ratings ELO — no depende de ningún estado in-play. `BasketballWinProbModel.estimate()` la usa como semilla (`pregame_p`, línea 74) antes de simular la difusión in-play. |
| **baseball** | SÍ, ya separado y trivialmente reutilizable. | `baseball_winexp.py:34-36` (`_elo_win_prob`), misma forma exacta que basketball — standalone, sin estado de juego. |
| **weather** | SÍ, por diseño — el modelo ES intrínsecamente pre-evento (climatología). | `weather_climatology.py:1-43` (docstring): el output (`expected_time_fraction` por bin de temperatura) ya es "la probabilidad que este bin recibirá al resolver, dado el lead time actual" — no hay estado in-play que separar, el modelo nunca tuvo esa mezcla. Falta sí un helper de mapeo bin→precio-de-mercado (ver Fase 1). |
| **tennis** | NO — está mezclado, requiere un wrapper delgado (no una refactorización grande). | `tennis_markov.py:396-453` (`instantaneous_match_win_prob`) es la única función pública que da una probabilidad puntual, y siempre toma parámetros de estado en vivo (`sets_a, sets_b, games_a, games_b, points_a, points_b, a_serving`) — no hay una función que la llame con el estado 0-0-0-0-0-0 pre-armado. `estimate()` (línea 755) devuelve una LISTA de `DwellBand` (distribución agregada Monte Carlo sobre todo el partido), no un escalar pre-partido. Se necesita una función nueva, pequeña (ej. `pre_match_win_prob(rank_a, rank_b, best_of) -> float`), que llame a `instantaneous_match_win_prob` con el estado inicial fijo — reutiliza el 100% de la maquinaria Markov existente, cero recalibración. |
| **football** | NO — misma situación que tenis, mismo tamaño de fix. | `football_poisson.py:53-75` (`_win_prob_from_state`) es privada (prefijo `_`) y siempre requiere lambdas restantes + marcador actual; se llama internamente en `_simulate_one_match` (línea 105-106) ya en minuto 0 dentro del loop, pero nunca se expone como resultado aislado. Se necesita una función nueva (ej. `pre_match_win_prob(team_a_elo, team_b_elo, league) -> tuple[float, float]`) que llame a `_match_lambdas` + `_win_prob_from_state` con marcador 0-0 y lambdas completos (no restantes) — de nuevo, reutiliza la maquinaria existente, no la reemplaza. |

Conclusión de este punto: **NO hace falta ninguna refactorización grande de ningún modelo de categoría.** Basketball/baseball ya exponen la pieza necesaria; tenis/fútbol necesitan una función wrapper nueva y pequeña por módulo (una función, sin tocar la simulación Monte Carlo existente); clima necesita un helper de mapeo, no un cambio de modelo.

**Asumido por falta de acceso/tiempo (marcado explícito, no verificado):**
- No se verificó en vivo si el feed de Kalshi para tenis/fútbol/basketball/baseball expone un precio de mercado ANTES de que el evento empiece (a diferencia de crypto_15m, donde Priya ya confirmó que el libro está vacío pre-`open_time`). El usuario afirma que sí hay datos pre-evento reales de fair-value (rankings, forma, clima) pero esta sesión no confirmó con una llamada real a Kalshi que el book de un partido de tenis/fútbol tiene precio operable horas/días antes del inicio. **Esto es un [DECISIÓN SARA] / verificación pendiente de Fase 0, no se puede saltar.**
- No se auditó si `estimate_dynamic_epsilon()` acepta hoy un contexto "pre-evento" (sin datos in-play) sin romper — se asume que sí porque la firma es `(category, context, fallback)` genérica, pero no se ejecutó ningún test contra ese caso límite en esta sesión.
- No se investigó la estructura real de `resolution_meta`/`status` que usaría el nuevo state machine — se sigue el patrón textual de `crypto15m_B_*` visto en código, pero los nombres concretos de estado son propuestos, no verificados contra ningún test existente.

**Fuera de alcance de este plan (explícito):**
- Cualquier ejecución de órdenes reales — no existe en Kairos, línea roja del proyecto, este plan no la construye ni la asume.
- La variante `crypto_15m` de Strategy 1 (fases 0-4 ya cerradas, gate apagado) — este plan es una feature DISTINTA y no la toca ni depende de ella.
- Calibración real del umbral de gap "significativo" con datos históricos — Fase 3 solo deja el mecanismo listo para calibrar cuando haya datos, no fabrica un número.
- `_SUPPORTED_CATEGORIES`/`_NO_DYNAMIC_EPSILON_CATEGORIES` de baseball y su estado actual no se tocan salvo lo estrictamente necesario para wiring (ver Fase 4).

---

## 2. Fases de trabajo

### Fase 0 — Verificación empírica de datos pre-evento reales en Kalshi

**Qué se hace:** llamadas reales (no mockeadas) a `GET https://external-api.kalshi.com/trade-api/v2/markets` para al menos un mercado real de tenis, uno de fútbol y uno de clima, confirmando que el book (`yes_bid`/`yes_ask`/`no_bid`/`no_ask`) tiene precio operable con horas o días de anticipación al inicio del evento — no solo en vivo. Sin esto, el gap pre-evento no tiene contra qué compararse.

**Archivo:** script exploratorio desechable (`scripts/probes/probe_preevent_price_availability.py`, mismo patrón/destino (`forgotten/` o borrado al cerrar) que `probe_kalshi_crypto15m.py` de la Fase 0 del plan crypto_15m).

**Terminado cuando:** Sara tiene, por escrito, confirmación real de que existe precio operable pre-evento para al menos tenis y fútbol (weather se asume por diseño climatológico, no depende de que Kalshi tenga precio anticipado — el modelo climatológico funciona igual sin eso), y una decisión de qué ventana mínima de anticipación (horas antes del evento) es útil para este setup — número que Sara/usuario deciden, no Nora.

**Paralelizable:** SÍ — la verificación de tenis, fútbol y clima son independientes entre sí, tres agentes en simultáneo.

`[DECISIÓN SARA]` — si Fase 0 encuentra que Kalshi NO tiene precio pre-evento operable para alguna categoría (ej. libros que abren muy cerca del inicio), esa categoría se cae del alcance de este plan hasta nuevo aviso — no se fuerza el feature contra datos que no existen.

---

### Fase 1 — Funciones de fair-value pre-evento por categoría

**Qué se construye:**
- `tennis_markov.py`: nueva función `pre_match_win_prob(rank_a: int, rank_b: int, best_of: int = 3) -> float` — llama a `_bucket_for_rank` + `serve_pct_by_bucket` (reutiliza lo existente, línea 763-774) y a `instantaneous_match_win_prob(...)` con el estado fijo `sets_a=sets_b=games_a=games_b=points_a=points_b=0, a_serving=True`. Cero cambios a la simulación Monte Carlo ni a la calibración existente.
- `football_poisson.py`: nueva función `pre_match_win_prob(elo_home: float, elo_away: float, league: str = "default") -> tuple[float, float]` — llama a `_match_lambdas` (línea 36-50, ya existente) y a `_win_prob_from_state` (línea 53-75, ya existente) con marcador 0-0 y los lambdas COMPLETOS (no restantes) para ambos lados. Cero cambios a la simulación.
- `weather_climatology.py`: nueva función helper `fair_prob_for_strike(bands: list[DwellBand], strike_low: float, strike_high: float) -> float` — suma `expected_time_fraction` de los bins de `WeatherClimatologyModel.estimate()` que caen dentro del rango de temperatura que define la pregunta yes/no del mercado (ej. "¿temp entre 20-21C?"). El modelo en sí no cambia, solo se agrega el paso de "traducir bins a la probabilidad de ESTE mercado puntual".
- `basketball_winprob.py`/`baseball_winexp.py`: ninguna función nueva — se documenta explícitamente que `_elo_win_prob(rating_a, rating_b)` (ya pública en la práctica aunque con prefijo `_`, considerar exponerla sin prefijo o vía un wrapper trivial `pre_game_win_prob` por consistencia de nombre con las otras categorías nuevas) ya cumple el rol.

**Terminado cuando:** tests unitarios nuevos (uno por categoría, en el mismo archivo/carpeta de tests que ya cubre cada modelo hoy) confirman que la función pre-evento devuelve un valor determinista y coherente con casos límite conocidos (ej. tenis: rank_a=1 vs rank_b=500 debe devolver una probabilidad alta pero NO 1.0 — el modelo Markov nunca converge a certeza absoluta; fútbol: elo_home=elo_away debe devolver ~0.5 modulado por ventaja de local).

**Paralelizable:** SÍ — las 5 funciones/wrappers son completamente independientes entre sí (5 agentes en simultáneo si hiciera falta), y NO dependen de que Fase 0 cierre (son funciones puras de cálculo, no de datos de mercado) — pero el feature completo no puede activarse hasta que Fase 0 confirme que hay contra qué comparar el output de estas funciones.

---

### Fase 2 — Cálculo del gap y ponderación por volatilidad

**Qué se construye:** módulo nuevo, ej. `src/core/strategies/overvaluation_gap.py`, con:
- `compute_overvaluation_gap(market_price: float, fair_prob: float) -> float` — resta simple (`market_price - fair_prob`), documentado explícitamente con el signo: positivo = favorito sobrevalorado (precio de mercado por encima del fair-value del modelo), negativo = favorito infravalorado. Trivial, pero se aísla en su propia función para que el criterio de "qué es un gap" quede en un solo lugar auditable, no repetido por categoría.
- `weight_gap_by_category_volatility(gap: float, category: str, context: dict) -> float` — llama a `limit_order_formation.estimate_dynamic_epsilon(category, context, fallback)` (línea 100, YA EXISTENTE, se reutiliza sin modificar su firma) para obtener el epsilon de volatilidad dwell-time de esa categoría, y lo usa para escalar el nivel de limit sugerido — NO para escalar el gap en sí (el gap es una medida de precio, el epsilon es una medida de dónde poner el limit dado cuánto fluctúa la categoría; mezclar ambos en una sola operación sería confuso de auditar, se mantienen como dos pasos separados y documentados).
- `derive_limit_level(current_price: float, epsilon: float, side: str) -> float` — reutiliza la MISMA restricción que ya existe en el proyecto (Kalshi solo permite limits de compra por debajo del precio actual): `current_price - epsilon`, clamped a `[0.20, 0.80]` (banda dura, ver Fase 3 — el clamp se aplica acá y también se re-valida en la Fase 3 como gate independiente, doble chequeo intencional, no redundancia accidental).
- `max_order_size_from_liquidity(book_depth_at_level: float, avg_volume: float | None = None) -> float` — **NUEVO** (agregado 2026-07-12 por decisión explícita del usuario, post-Fase 0, renombrado de `cap_order_size_by_liquidity` para reflejar que se calcula ANTES de dimensionar cualquier pata, no como recorte post-hoc): devuelve el tamaño MÁXIMO permitido (**1% del volumen total del mercado**) que luego se usa directamente para dimensionar ambas patas del arb — no hay paso posterior de "proponer y recortar". Como `liquidity_dollars`/`liquidity` están DEPRECADOS por Kalshi desde 2026-02-13 (confirmado por Priya vía changelog oficial, devuelven 0 para todo consumidor, con o sin auth — no es un problema de falta de API key), la liquidez se mide con:
  1. **Profundidad real del book** (`size` en el nivel de bid/ask relevante, ya confirmado poblado y real en Fase 0) — criterio principal, "liquidez actual".
  2. **`volume_fp`/`volume_24h_fp`** (`open_interest_fp` como alternativa) como proxy de "liquidez promedio" — **pendiente de verificación empírica**: Priya no confirmó todavía con una llamada real que estos campos tengan valores no-cero en los mismos tickers de tenis/fútbol pre-evento usados en Fase 0. Este chequeo puntual debe hacerse al inicio de Fase 2, antes de fijar la fórmula exacta de "promedio".
  Fail-closed: si ninguna de las dos fuentes de liquidez está disponible/poblada para un mercado dado, la función debe devolver `size=0` (no proponer ninguna pata) en vez de asumir un tamaño por defecto.

**Terminado cuando:** tests unitarios con fixtures sintéticas confirman: (a) el signo del gap es correcto en ambas direcciones, (b) `weight_gap_by_category_volatility` falla-cerrado (devuelve un valor marcado explícitamente como "sin epsilon dinámico" o similar) para categorías en `_NO_DYNAMIC_EPSILON_CATEGORIES` (ej. baseball hoy) en vez de inventar un número, (c) `derive_limit_level` nunca devuelve un valor fuera de `[0.20, 0.80]` incluso con inputs extremos.

**Paralelizable:** las 3 funciones son secuenciales entre sí dentro del mismo módulo (cada una consume el output de la anterior) — no paralelizable internamente, pero el módulo completo SÍ puede construirse en paralelo con Fase 1 (no depende de las funciones de fair-value estar mergeadas, solo de su firma acordada de antemano — coordinar la firma exacta con Fase 1 antes de que ambos agentes empiecen).

`[DECISIÓN SARA]` — el umbral de "gap extremo/significativo" que decide si el setup es sub-tipo "gap grande" (comprar underdog a mercado ya) vs "gap chico" (comprar favorito a mercado ya): el usuario dejó esto explícitamente abierto. Dos opciones, sin que Nora elija:
1. **Número fijo elegido a mano** (ej. 0.10 = 10 puntos de probabilidad) — simple, auditable, pero arbitrario hasta que se valide contra resultados reales.
2. **Calibrado empíricamente** con datos históricos ya en la DB — ej. para tenis, partidos ya jugados con precio de mercado inicial (si Fase 0 confirma que existe) vs resultado real, para ver qué gap realmente predijo mean-reversion del precio. Requiere que Fase 0 confirme que hay histórico suficiente de precio pre-evento almacenado (no solo disponible en vivo) — puede no estar disponible todavía si Kairos nunca guardó snapshots de precio pre-evento hasta ahora.

Este plan NO elige entre las dos — Fase 2 debe dejar el umbral como un parámetro inyectable (no hardcodeado inline) para que cualquiera de las dos opciones se pueda aplicar sin tocar la lógica del módulo.

---

### Fase 3 — Lógica de decisión de setup + banda dura de ejecución

**Qué se construye:**
- `classify_overvaluation_setup(gap: float, threshold: float) -> Literal["favorite_overvalued", "market_justified"]` — bifurcación simple según el signo/magnitud del gap contra el `threshold` de Fase 2 (`[DECISIÓN SARA]`, no fijado acá).
- Dirección del setup (mecánica confirmada por el usuario, siguiendo el mismo principio ya verificado en código real en `paper_trading_cycle.py:1270-1350`, `_run_strategy1_crypto15m_b_pass`, adaptado — NO copiado — a esta categoría):
  - `favorite_overvalued` → comprar el UNDERDOG a MERCADO de inmediato (nunca esperar), y colocar un LIMIT resting para el FAVORITO en `derive_limit_level(current_price=favorite_price, epsilon, side="favorite")`.
  - `market_justified` → comprar el FAVORITO a MERCADO de inmediato, y colocar un LIMIT resting para el UNDERDOG en `derive_limit_level(current_price=underdog_price, epsilon, side="underdog")`.
- **Banda dura de ejecución `[0.20, 0.80]`**: gate de riesgo INDEPENDIENTE del gap, aplicado como último chequeo antes de proponer CUALQUIER pata (mercado o limit) — si el precio de la pata de mercado ya está fuera de `[0.20, 0.80]` en el momento de decisión, el setup entero se descarta (no se ejecuta ninguna pata), documentado explícitamente como número fijo confirmado por el usuario, no derivado del modelo ni configurable por categoría.
- **Cap de tamaño por liquidez (1%), calculado PRE-ejecución**: segundo gate de riesgo INDEPENDIENTE, resuelto por decisión explícita del usuario (2026-07-12): `cap_order_size_by_liquidity()` (Fase 2) se llama **ANTES de proponer o ejecutar cualquier pata** — el tamaño máximo permitido (1% del volumen total del mercado) se calcula primero, y ESE tamaño ya limitado es el que se usa desde el inicio para dimensionar AMBAS patas del arb (mercado y limit), nunca al revés (proponer un tamaño mayor y recortarlo después de intentar ejecutar). Esto es coherente con la restricción ya conocida de que la pata de mercado se ejecuta inmediatamente sin esperar — el tamaño debe estar resuelto de antemano, no ajustado post-hoc.
- Nuevo state machine, ej. `_run_strategy1_overvaluation_pass` en `src/core/orchestration/paper_trading_cycle.py` (función nueva, no modifica `_run_strategy1_pass` genérico ni `_run_strategy1_crypto15m_*` existentes), con estados propios (ej. `overvaluation_market_leg_filled_awaiting_hedge → overvaluation_resolved_arb/directional`), siguiendo el patrón textual visto en `_run_strategy1_crypto15m_b_pass` pero como código independiente (no se reutiliza esa función porque su lógica de timeout/hedge es específica del ciclo de 15 min de crypto, no aplicable sin adaptación a partidos de horas de duración).

**Terminado cuando:** tests unitarios del state machine (transiciones válidas/inválidas, y explícitamente el caso "precio de la pata de mercado cae fuera de `[0.20, 0.80]` en el momento de decisión → cero patas ejecutadas, setup descartado sin excepción") pasan con fixtures sintéticas para las 5 categorías (tenis/fútbol/clima/basketball/baseball).

**Paralelizable:** el diseño de `classify_overvaluation_setup` + banda dura es una unidad (no paralelizable internamente), pero el cableado del state machine por categoría (tenis vs fútbol vs clima vs basketball/baseball) SÍ es paralelizable entre agentes una vez que la función de clasificación esté cerrada — cada categoría solo necesita su propia función de Fase 1 y el contexto de mercado específico.

`[DECISIÓN SARA]` — nombres exactos de estado del state machine (propuestos arriba como punto de partida, no definitivos) deben ser revisados por Sara antes de codear, siguiendo cualquier convención de naming que ya se haya fijado en otras fases recientes (ej. prefijo `crypto15m_` vs uno nuevo para esta feature).

---

### Fase 4 — Integración con Strategy 1 existente + gate de activación

**Qué se construye:**
- NINGÚN cambio a `STRATEGY1_CATEGORIES` (`portfolio.py:90`) — las 5 categorías (tenis, fútbol, fútbol, clima, basketball, baseball) YA están en la tupla desde antes; este feature es una lógica ADICIONAL dentro de esas categorías, no una categoría nueva.
- `STRATEGY1_CALIBRATED_CATEGORIES` (`portfolio.py:112`) permanece vacío/fail-closed — se reitera explícitamente como principio no negociable, igual que en el plan crypto_15m: ninguna categoría se marca calibrada hasta que haya evidencia real de calibración documentada (changelog/doc adjunto, mismo espíritu que el Riesgo R5 del plan crypto_15m).
- Cablear `_run_strategy1_overvaluation_pass` (Fase 3) dentro del ciclo de `paper_trading_cycle.py`, filtrando eventos de las categorías con Fase 0 confirmada, y consultando `estimate_dynamic_epsilon` de `limit_order_formation.py` sin tocar `_SUPPORTED_CATEGORIES` (ya incluye las 5 categorías necesarias, no hace falta agregar nada ahí — a diferencia de crypto_15m, que fue deliberadamente EXCLUIDA de ese set).
- **Gate de activación explícito, mismo patrón que `CRYPTO15M_LIVE_ENABLED = False`**: constante nueva (ej. `OVERVALUATION_MODEL_ENABLED = False`) que impide que este nuevo pase corra — ni siquiera en paper-trading — hasta confirmación explícita y separada del usuario. Requisito no negociable de esta fase, pedido explícitamente por el usuario en la conversación de diseño.

**Terminado cuando:** un ciclo de paper-trading corrido end-to-end contra fixtures (no producción real) genera bets con `resolution_meta` conteniendo el sub-tipo (`favorite_overvalued`/`market_justified`) y la categoría de origen, el portfolio scorer sigue tratando estas categorías como no-calibradas (no asigna capital real hasta decisión explícita), y con `OVERVALUATION_MODEL_ENABLED = False` el pase completo es un no-op verificable (ningún bet se crea aunque el resto del ciclo corra normalmente).

**Paralelizable:** NO — depende de que Fase 3 esté cerrada (necesita el state machine real para cablear el pase) y de que Fase 0 haya confirmado qué categorías entran realmente en producción.

---

## 3. Puntos de decisión de Sara / usuario (consolidado)

- `[DECISIÓN SARA]` Fase 0: si alguna categoría no tiene precio pre-evento operable en Kalshi, se cae del alcance — a confirmar con datos reales, no asumido.
- `[DECISIÓN SARA]` Fase 2: umbral de "gap significativo" — fijo a mano vs calibrado empíricamente (ambas opciones descritas, ninguna elegida por este plan). Depende de que Fase 0 confirme si hay histórico de precio pre-evento almacenado (no solo en vivo) para la opción calibrada.
- `[DECISIÓN SARA]` Fase 3: nombres exactos de estado del state machine, y convención de naming a seguir.
- Resuelto (2026-07-12, ya no es punto abierto): el cap de 1% de volumen total del mercado se calcula ANTES de ejecutar cualquier pata — el tamaño ya viene acotado desde el diseño del setup, ninguna pata se propone con un tamaño mayor al 1% para después recortarlo.
- Reiterado como principio no negociable (no es una decisión, es un requisito): `STRATEGY1_CALIBRATED_CATEGORIES` sigue vacío hasta evidencia real; gate `OVERVALUATION_MODEL_ENABLED = False` obligatorio en Fase 4 antes de cualquier despliegue, incluso a paper-trading.

---

## 4. Riesgos conocidos

- **R1 — Disponibilidad real de precio pre-evento no verificada todavía:** todo el plan depende de que Fase 0 confirme que Kalshi expone book operable con antelación real al evento para tenis/fútbol. Si el book solo se puebla minutos antes del inicio (como pasa con crypto_15m, donde Priya ya confirmó libro vacío pre-`open_time`), el gap pre-evento no tiene contra qué compararse y el feature completo queda bloqueado para esa categoría.
- **R2 — Falta de histórico de precio pre-evento almacenado:** aunque el precio esté disponible EN VIVO ahora, puede que Kairos nunca haya guardado snapshots pre-evento históricamente — esto bloquearía la opción "calibrado empíricamente" del umbral de gap (Fase 2, `[DECISIÓN SARA]`) hasta acumular datos nuevos desde cero.
- **R3 — El mismo riesgo de "orden de cruce" que ya existe en Strategy 1 genérico (documentado como R1 en el plan crypto_15m) también aplica acá:** si ambas patas (mercado + limit) terminan cruzando niveles relevantes en una ventana de tiempo indistinguible por el ciclo de polling actual, el resultado puede quedar "indeterminate" igual que en el caso genérico — no se investigó en esta sesión si el mismo mecanismo de tick-level (`KalshiMarketTick`, ya construido para crypto_15m) se puede/debe reutilizar acá, o si el ciclo de eventos de tenis/fútbol/clima es lo bastante más lento (horas vs 15 min) para que un polling de 1-5 min sea suficiente sin necesitar tick-level. Queda como pregunta abierta para Fase 0/1.
- **R4 — Confusión potencial de nombres entre `pre_match_win_prob` nuevo y `estimate()` existente:** ambas funciones conviven en el mismo módulo por categoría; un futuro agente apurado podría confundir cuál usar para qué. Vale la pena que cada módulo documente claramente en el docstring de la función nueva que es un "escalar pre-evento", distinto de la distribución agregada de `estimate()`.
- **R5 — Gate de calibración fácil de "trampear" bajo presión de resultados** (mismo riesgo ya señalado como R5 en el plan crypto_15m, aplica idéntico acá): `STRATEGY1_CALIBRATED_CATEGORIES` es un simple `set()` en código — vale la pena el mismo test/nota explícita que falle si alguna de estas categorías se marca calibrada sin changelog/doc de calibración adjunto.
