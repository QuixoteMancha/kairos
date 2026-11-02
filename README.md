# Kairos

Servicio de escaneo de mercados de predicción en **Kalshi**, centrado en
tenis (ATP/WTA) y con soporte parcial para fútbol y deportes de equipo. Kairos
busca oportunidades, las registra y las evalúa en modo shadow/paper, apoyado en
un historial propio de partidos, estadísticas, clima y Elo que mantienen
workers en segundo plano.

> **Ejecución real:** Kairos no coloca órdenes de forma automática. Las
> estrategias que afectan dinero están desactivadas por flag
> (`ENABLE_*=false` en `docker-compose.yml`), y el wiring de órdenes
> (`outer_apis/kalshi/orders.py`) sólo se invoca desde scripts manuales con
> confirmación explícita.

## Estructura

| Ruta | Contenido |
|---|---|
| `kairos.py` | Punto de entrada: un scan, o un loop si `KAIROS_SCAN_INTERVAL_SECONDS` está definido. Respeta el freno global de `database/accounting/systemhaltstate.py`. |
| `engine.py` | Orquesta el scan: obtiene mercados de Kalshi, los empareja y los pasa a las estrategias habilitadas. |
| `strategies/` | `limit_arb.py` (pata market + pata límite contra un fair price), `market_arb.py` (arb same-market: suma de patas < 1), `bet_on_favourite.py` (direccional con filtros estrictos y calibración de Elo), `live_limit_arb.py` (LimitArb live en shadow), `paper_trading.py`, `fill_verification.py`. |
| `models/` | Modelos de dominio: fair price y volatilidad por deporte (p. ej. `models/volatility/sports/tennis/`). |
| `outer_apis/` | Integraciones: cliente/auth/órdenes/websocket de Kalshi (`kalshi/`), scrapers (TennisExplorer, Flashscore, clima…), precios cripto. El parseo específico de cada API vive aquí. |
| `inner_api/` | API HTTP Flask de sólo lectura (`inner_api/server.py`). |
| `database/` | Modelos Peewee y acceso a PostgreSQL, separados en `accounting/` (operativa) e `historical/` (referencia), más `migrations/`. |
| `workers/` | Procesos de larga duración, uno por servicio de Compose. |
| `scripts/` | Mantenimiento, backfills, backtests y despliegue (`scripts/tennis/`, `deploy_*.sh`). |
| `tests/` | Suite pytest, que replica la estructura anterior. |
| `docs/` | Notas de investigación y auditorías. |

### Bases de datos

Producción usa dos bases PostgreSQL separadas en el mismo servidor (ver
`database/db.py`):

- `DB_NAME` (`kairos`): contabilidad operativa (señales, paper trades, estado del sistema).
- `DB_HISTORICAL_NAME` (`kairos_historical`): datos de referencia (historial de
  partidos, ticks de Kalshi, stats, clima, Elo, estado de workers).

SQLite sólo se usa como backend efímero del suite de tests
(`KAIROS_TESTING=1`, configurado en `tests/conftest.py`).

### Workers principales

| Worker | Qué hace |
|---|---|
| `tennis_match_history_sync_worker` | Mantiene `MatchHistory`, ranking y stats a partir de los partidos escaneados. |
| `tennis_player_catalog_backfill_worker` | Backfill continuo del catálogo completo de jugadores de TennisExplorer. |
| `tennis_match_tick_worker` / `tennis_set_tick_worker` | Ticks de probabilidad de Kalshi por partido y por set. |
| `tennis_live_data_worker` | Punto a punto y estadísticas live (Flashscore). |
| `tennis_self_elo_backfill_worker` | Recalcula cada 6 h el Elo point-in-time, el Weighted Elo y la calibración Platt. |
| `tennis_data_gap_filler_worker`, `tennis_match_stats_*`, `tennis_match_duration_*`, `tennis_player_bio_*`, `tennis_tournament_metadata_*` | Relleno de huecos y enriquecimiento del historial. |
| `tennis_weather_worker`, `weather_market_capture_worker` | Clima de partidos y captura de mercados climáticos (sin órdenes). |
| `market_arb_websocket_scanner` | MarketArb en tiempo real vía websocket. |
| `outcome_observer_worker`, `historical_collector_worker` | Resultados de señales y ticks/precios históricos. |
| `tennis_ml_optimizer_worker` | Laboratorio offline de challengers para BetOnFavourite; nunca cambia producción. |
| `tennis_history_archiver_worker` | Archiva la base histórica al disco externo. |
| `system_resource_monitor_worker` | Registra picos de memoria/PSI del host y los contenedores que más consumen. |

## Desarrollo local

Requiere Python 3.13.

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest
cp .env.example .env      # rellenar sólo las credenciales que necesite tu tarea
```

Ejecución:

```sh
python -m pytest                                   # suite completa
python -m pytest tests/test_compute_self_elo.py -q # un módulo
python kairos.py                                   # un scan (contacta servicios reales)
```

Los tests no deben depender de bases de producción ni de APIs en vivo: aísla
red, credenciales, tiempo y bases con `unittest.mock` y los fixtures de
`tests/conftest.py`.

## Configuración

Las variables se leen de `.env` (plantilla en `.env.example`). Las más
relevantes son:

| Variable | Uso |
|---|---|
| `POSTGRES_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_HISTORICAL_NAME` | Conexión a PostgreSQL (Compose fija host y nombres). |
| `KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY` | Credenciales de Kalshi para lectura autenticada de portfolio (ver `outer_apis/kalshi/portfolio.py`). |
| `BRIGHTDATA_PROXY` | Proxy residencial para scrapers que lo requieren. |
| `SMTP_*`, `ALERT_EMAIL_*` | Alertas por correo (proxy caído o sin saldo). |
| `KAIROS_IMAGE_TAG` | Tag de imagen que usan **todos** los servicios de Compose. |
| `KAIROS_SCAN_INTERVAL_SECONDS` | Activa el modo loop de `kairos.py`. |
| `ENABLE_LIMIT_ARB`, `ENABLE_MARKET_ARB`, `ENABLE_BET_ON_FAVOURITE`, `ENABLE_BET_ON_FAVOURITE_SHADOW` | Flags de estrategias (por defecto `false`). |

Cada worker tiene además variables propias de intervalo y límite por ciclo
(`*_INTERVAL_SECONDS`, `*_LIMIT`), documentadas en `docker-compose.yml`.

Nunca subas al repositorio `.env`, claves, credenciales de proxy ni archivos
de base de datos. Si agregas una variable nueva, documéntala en `.env.example`
con un valor de ejemplo seguro.

## Docker y despliegue

```sh
docker compose build
docker compose up <servicio>      # p. ej. api
```

- `kairos_migrate` corre `python -m database.migrate` antes que el resto de los
  servicios. Las reglas para cambiar el esquema están en
  [`database/migrations/README.md`](database/migrations/README.md).
- El runner de migraciones rechaza versiones desconocidas, así que **todos los
  servicios deben usar la misma imagen**. `scripts/deploy_kairos_stack.sh <tag>`
  construye una imagen, migra, levanta el stack completo y persiste el tag en
  `.env`. `scripts/deploy_minipc.sh` sincroniza el working tree con la mini-pc
  de producción y lo invoca allí. Ojo: sincroniza también los cambios sin
  commitear.
- La API queda en el puerto `8765` (servicio `api`). Endpoints útiles:
  `/healthz`, `/api/workers`, `/api/operations`, `/api/account`,
  `/api/tennis/data-quality`, `/api/tennis/favourite-shadow`,
  `/api/system/resources`, `/api/system/incidents`.
- El disco externo de archivo se monta como sólo lectura en los servicios que
  lo consultan; sólo `tennis_history_archiver_worker` escribe en él.

## Contribuir

- PEP 8, sangría de cuatro espacios, `snake_case` / `PascalCase` /
  `UPPER_SNAKE_CASE`. No hay formateador ni linter configurado: sigue el estilo
  del código cercano.
- El parseo específico de cada API va en su paquete de `outer_apis/`; las
  reglas de negocio, en `models/` o `strategies/`.
- Commits con Conventional Commits y scope, p. ej. `fix(tennis): ...`.
- Los PR deben explicar comportamiento y riesgo, listar los comandos de
  verificación y señalar cambios de esquema, entorno, intervalos de workers o
  Docker.

Las guías completas para agentes y colaboradores están en [`AGENTS.md`](AGENTS.md).

## Licencia

Distribuido bajo la licencia MIT. Ver [`LICENSE`](LICENSE).
