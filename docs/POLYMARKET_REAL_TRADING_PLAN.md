# Polymarket Real Trading Plan (Crypto 5m)

> **Estado**: Sprint 1 completado. El motor de tripletas (`polymarket_triplet_live.py`) implementa Sprint 2–4 en modo paper. Pendiente: integración con CLOB API real para ejecución de órdenes.

## Objetivo
Integrar trading real en Polymarket para mercados crypto de 5 minutos con capital inicial de prueba (USD 200), bajo controles estrictos de riesgo operativo y contable.

## Scope inicial
- Venue: Polymarket
- Mercado: binary YES/NO en crypto 5m
- Modo actual: read-only scanner + observabilidad
- Modo siguiente: ejecución real con settlement y conciliación

## Modelo operativo: stat-hedge (canónico)

Este plan usa el mismo modelo canónico de Kairos:
- `stat`: entrada con edge/modelo (pierna primaria).
- `stat-hedge`: estructura con cobertura intrínseca o plan de cobertura explícito.
- `intra-hedge` / `cross-hedge`: cobertura reactiva adicional según evolución del mercado.

Para Polymarket crypto 5m:
- La pata `stat` nace del scanner/modelo (`market_type=crypto_5m_binary`).
- El motor evalúa riesgo en vivo y decide si aplicar hedge reactivo según reglas de riesgo/latencia.
- Toda transición (stat -> hedge -> settlement) debe registrarse en ledger real y quedar conciliada.

Reglas mínimas para esta vertical:
- No abrir `stat` si no existe ruta de salida definida (cancel/offset/hedge).
- Hedge solo si mejora payoff ajustado por fees/slippage.
- Si falla ejecución del hedge, activar guardas (size cut o pause por riesgo).

## Patrón de ejecución objetivo (Limit -> Live Market)

Objetivo operativo para compensar comisiones:
- Entrada pre-evento con orden límite pasiva (`postOnly + GTC/GTD`) para buscar rol maker.
- En vivo, cuando cambian probabilidades, ejecutar salida/cobertura con orden marketable (`FAK/FOK`) solo si el neto supera costos.

Condición mínima de acción:
- `net_bps = gross_bps - fee_bps - slippage_bps`
- Ejecutar `arb_exit_market` solo si `net_bps >= MIN_NET_ARB_BPS`.
- Ejecutar `hedge_exit_market` si `net_bps <= -STOP_LOSS_BPS`.

Importante:
- No hardcodear fee final: usar `fee_rate_bps` real por trade en fills/historial del venue.
- Los valores de fee/slippage en config son solo fallback para decisión ex-ante.

## Estructura preferida: tripleta (2 pre-event + 1 live)

Preferencia operativa definida:
- `Leg A` pre-evento: limit pasiva por debajo del precio de referencia.
- `Leg B` pre-evento: limit pasiva por encima del precio de referencia.
- `Leg C` live: marketable order de hedge/offset cuando el precio se desplaza.

Razonamiento de la preferencia:
- Reduce costo esperado de entrada al priorizar fills maker en A/B.
- Permite capturar ambos escenarios pre-evento (micro-rango arriba/abajo) sin forzar market entry.
- Usa la pata live (`Leg C`) como control dinámico de riesgo y cierre de beneficio neto.
- Replica exactamente la lógica probada en deportes: par pre-match (stat value) + live-hedge reactivo.

Reglas de control para la tripleta:
- Si solo una pata pre-evento llena, la otra debe cancelarse/ajustarse por timeout definido.
- No ejecutar `Leg C` si no mejora neto después de fees/slippage o si no cumple regla de stop.
- Toda asociación de patas debe persistirse con `source_bet_id + source_group_key` para auditoría.

## Etapas de desarrollo

### Etapa 0 - Marco de riesgo (obligatoria)
- Capital piloto: USD 200
- Riesgo por operación: 1%-2% (USD 2-4)
- Pérdida diaria máxima: 5% (USD 10)
- Pérdida semanal máxima: 12%-15%
- Límite de operaciones diarias
- Kill-switch por:
  - 3-4 pérdidas consecutivas
  - errores críticos de API / reconciliación

Criterio de salida:
- Política activa en código y validada con tests de guard-rails.

### Etapa 1 - Integración read-only (estado actual)
- Scanner Polymarket API para mercados crypto 5m
- Filtros por liquidez, spread y tiempo a resolución
- Cache local de 1 segundo (configurable) para evitar abuso de rate limits
- Market type formal: crypto_5m_binary

Criterio de salida:
- Feed estable 48h sin huecos críticos.

### Etapa 2 - Ejecución real mínima
- Place order / cancel order / order status
- Idempotencia por client_order_id
- Reintentos seguros + reconciliación post-timeout

Criterio de salida:
- 20-30 órdenes pequeñas sin inconsistencias de estado.

### Etapa 3 - Ledger real + conciliación
- Ledger de eventos reales: submit, fill, fee, cancel, settle
- Conciliación periódica contra API del venue
- Pausa automática ante drift > umbral (ej. USD 0.50)

Criterio de salida:
- Conciliación automática estable durante varios ciclos diarios.

### Etapa 4 - Settlement real
- Resolver estado final desde fuente oficial de Polymarket
- Manejo de edge-cases: fill parcial, cierre sin fill, reversión/corrección
- Validación de PnL neto: payout - stake - fees

Criterio de salida:
- 100% de operaciones cerradas auditables.

### Etapa 5 - Capital y rebalance real
- Bucket crypto_5m con límites de exposición
- Reglas de rebalance operativo
- Restricciones anti-overtrading y cooldown

Criterio de salida:
- Cero posiciones huérfanas y sin negativos inesperados.

### Etapa 6 - Integración full pipeline
- Scanner -> Selector -> Executor -> Settlement -> Analytics -> UI
- Distinción visual clara real vs virtual

Criterio de salida:
- End-to-end funcional en API, DB, dashboard y reportes.

### Etapa 7 - Piloto controlado
- Semana 1: size fijo mínimo, baja frecuencia
- Semana 2: incremento gradual si KPIs estables

KPIs mínimos:
- error rate ejecución < 2%
- cero dobles órdenes
- cero drifts contables sin resolver > 24h

### Etapa 8 - Go / No-Go
- Go: operación robusta + settlement + conciliación limpia
- No-Go: persistencia de errores críticos operativos/contables

## Backlog por Sprints (S1-S4)

### Sprint 1 (read-only robusto) ✅ COMPLETADO
- [x] Endpoint: GET /api/scan/polymarket/crypto-5m (`polymarket_scan.py`)
- [x] Endpoint cache: GET /api/scan/polymarket/crypto-5m/cached
- [x] Config env para scan y throttling (`config.py` — POLYMARKET_* vars)
- [x] Métricas de latencia y errores por ciclo
- [x] Colector de intervalos 5m (`polymarket_5m_collector.py` + `Crypto5mInterval` DB)
- [x] Paper ledger (`polymarket_paper_ledger.py`)
- [x] Modelo de tripletas (`polymarket_triplet_model.py`)
- [x] Motor de tripletas live en paper (`polymarket_triplet_live.py`)

Deliverable:
- Scanner 5m en producción interna (sin ejecución real). ✅

### Sprint 2 (execution MVP)
- Cliente de trading autenticado
- place/cancel/status
- client_order_id e idempotencia
- dry-run switch por config

Deliverable:
- Trade real mínimo controlado con tamaño fijo pequeño.

### Sprint 3 (ledger + conciliation)
- Tablas/eventos de ledger real
- Worker de conciliación
- alertas y auto-pause por drift

Deliverable:
- Contabilidad real consistente con venue.

### Sprint 4 (settlement + UI)
- Worker de settlement oficial
- UI para operaciones reales y reconciliación
- reportes operativos diarios

Deliverable:
- Ciclo completo de operación real auditada.

## Contratos API (propuestos)

### GET /api/scan/polymarket/crypto-5m
Response (resumen):
- ts
- source
- market_type=crypto_5m_binary
- count
- markets[]
  - id
  - condition_id
  - question
  - yes_price/no_price
  - best_bid/best_ask/spread
  - liquidity
  - volume_24h
  - hours_to_resolution
  - clob_token_ids
  - value_score
- from_cache
- refresh_sec

### POST /api/trading/polymarket/order (Sprint 2)
Request:
- market_id
- side (yes|no)
- price
- size_usd
- tif
- client_order_id

Response:
- accepted
- order_id
- client_order_id
- status
- venue_ts

### POST /api/trading/polymarket/cancel (Sprint 2)
Request:
- order_id

Response:
- canceled
- order_id
- status

### GET /api/trading/polymarket/orders/:id (Sprint 2)
Response:
- order_id
- status
- filled_size
- avg_fill_price
- fees
- updated_at

## DB esquema mínimo sugerido (Sprint 3)

### polymarket_order
- id (pk)
- client_order_id (unique)
- venue_order_id
- condition_id
- token_id
- side
- price
- size_usd
- filled_usd
- avg_fill_price
- status
- created_at
- updated_at

### polymarket_fill
- id (pk)
- venue_fill_id (unique)
- order_id (fk)
- price
- size
- fee
- ts

### polymarket_settlement
- id (pk)
- condition_id
- outcome
- payout
- resolved_at
- source_ref

### polymarket_reconcile_log
- id (pk)
- ts
- internal_balance
- venue_balance
- drift
- action (ok|warn|pause)
- note

## Checklist de aceptación por etapa

### Read-only ✅
- [x] scan 1s estable
- [x] cache efectivo
- [x] sin errores críticos en logs

### Execution MVP
- [ ] place/cancel/status confiables
- [ ] idempotencia validada
- [ ] sin doble orden

### Ledger/Conciliación
- [ ] cada fill en ledger
- [ ] drift detectado y manejado
- [ ] auto-pause funciona

### Settlement
- [ ] estado final consistente
- [ ] PnL neto correcto
- [ ] auditoría reproducible

## API Keys y credenciales
Para scanner read-only NO se requieren credenciales privadas.
Para ejecución real sí serán necesarias credenciales/API de trading de Polymarket.

Variables preparadas en config:
- POLYMARKET_API_KEY
- POLYMARKET_API_SECRET
- POLYMARKET_API_PASSPHRASE
- POLYMARKET_PRIVATE_KEY

## Notas operativas
- 1 request/s puede ser viable, pero debe respetar rate limits reales del venue.
- Si hay 429 o degradación, aumentar refresh_sec y usar más cache.
- Siempre priorizar seguridad operativa sobre frecuencia de scan.
