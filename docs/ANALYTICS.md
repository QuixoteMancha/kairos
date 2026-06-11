# DuckDB Analytics Layer (Fase 1)

Capa OLAP de **sólo lectura** sobre los datos operacionales de Kairos. SQLite
sigue siendo la fuente de verdad; DuckDB se monta encima para queries pesadas
sin tocar al runtime.

## Arquitectura

```
                   ┌──────────────────────┐
                   │   DuckDB (in-memory) │
                   │   open_analytics()   │
                   └──────────┬───────────┘
                              │ ATTACH READ_ONLY + read_csv_auto + read_parquet
        ┌─────────────────────┼─────────────────────────────┐
        ▼                     ▼                             ▼
  /data/kairos.db     /data/kairos_analytics.db    /data/odds_history/*.csv.gz
  (operacional)       (rolling 7d odds)            (histórico exportado)
                                                   /data/bets_archive/*.parquet
                                                   (bets archivadas opt.)
```

Vistas unificadas creadas en cada conexión:

| Vista     | Origen                                                      |
| --------- | ----------------------------------------------------------- |
| `odds_all` | `anal.odds_snapshot` + `odds_history/*.csv.gz` (UNION BY NAME) |
| `bets_all` | `op.walletbet` + `bets_archive/*.parquet`                  |
| `ledger`   | `op.walletledger`                                           |

## Endpoints

### `GET /api/analytics/queries`

Lista el catálogo de queries pre-aprobadas + diagnóstico de fuentes.

```json
{
  "queries": [
    {"name": "bets_summary_by_platform", "description": "...", "params": []},
    {"name": "odds_movers_24h", "description": "...",
     "params": [{"name": "hours", "type": "int", "default": 24}, ...]}
  ],
  "sources": {
    "operational_db": "/data/kairos.db",
    "operational_exists": true,
    "odds_csv_files": 12,
    "duckdb_version": "1.4.1"
  }
}
```

### `GET /api/analytics/run/<query_name>?param1=...&param2=...`

Ejecuta una query del catálogo.

```bash
# P&L por plataforma
curl localhost:8765/api/analytics/run/bets_summary_by_platform

# Top movers de las últimas 6h
curl 'localhost:8765/api/analytics/run/odds_movers_24h?hours=6&limit=20'
```

Respuesta:

```json
{
  "ok": true,
  "query": "bets_summary_by_platform",
  "params": [],
  "columns": ["platform", "bets_total", "pnl_total", ...],
  "rows": [["stake", 1247, 312.45, ...]],
  "n": 5,
  "elapsed_ms": 18.3
}
```

## Catálogo actual

- `odds_movers_24h(hours, limit)` — fixtures con mayor delta de odds.
- `overround_by_platform(days)` — margen medio por book.
- `arb_windows(days, limit)` — momentos históricos con `overround < 1.0`.
- `bets_summary_by_platform()` — win-rate / ROI / P&L por book.
- `bets_summary_by_type()` — performance por arb_tipo (stat, inside-arb, …).
- `bets_daily_pnl(days)` — P&L acumulado por día.
- `bets_worst_fixtures(limit)` — top de fixtures con pérdidas.
- `ledger_recent(limit)` — últimas N entradas del libro mayor.

## Seguridad

- DuckDB nunca ejecuta SQL arbitrario desde el frontend. El endpoint sólo
  acepta nombres del diccionario `QUERIES` en [src/analytics/queries.py](../src/analytics/queries.py).
- Los attaches a SQLite son `READ_ONLY` → cero riesgo de bloqueo/corruption
  del runtime.

## Archivado de bets viejas

```bash
# Dry-run: ver qué se movería (sin tocar nada)
docker compose exec kairos python -m scripts.archive_bets --older-than-days 90 --dry-run

# Generar parquets sin borrar de SQLite
docker compose exec kairos python -m scripts.archive_bets --older-than-days 90

# Generar parquets y borrar de SQLite
docker compose exec kairos python -m scripts.archive_bets --older-than-days 90 --delete
```

Genera `/data/bets_archive/bets_YYYY-MM.parquet` (ZSTD). La vista `bets_all`
los incorpora automáticamente.

## Próximas fases (no implementadas todavía)

- **Fase 2**: convertir CSV.gz históricos → parquet particionado por mes
  (10-20× mejor compresión y query speed).
- **Fase 3**: precomputar agregados (`platform x day x arb_tipo`) como
  materialized parquets.
- **Fase 4**: feature store ML (winrate por superficie, drift de ELO, etc.).

---

## Métricas Técnicas / Runtime (Proxy, RAM, Latencia, Errores)

**Grafana + Prometheus fueron removidos completamente del proyecto.** (No hay nada en deploy/, docs/, ni código que los referencie.)

Toda la analítica técnica ahora es **in-app** vía endpoints JSON estructurados, listos para visualizar con Chart.js (o cualquier frontend) sin dependencias externas.

### Endpoint principal
`GET /api/analytics/tech`

Devuelve snapshot "chart-friendly":

```json
{
  "ts": "2026-...",
  "proxy": {
    "by_book": [
      {"book": "stake", "calls": 1240, "errors": 3, "error_rate": 0.0024,
       "last_latency_ms": 187.3, "cache_age_sec": 42.1, "last_error": null, ...},
      ...
    ],
    "summary": {
      "total_calls": 8721, "total_errors": 17, "overall_error_rate": 0.0019,
      "avg_latency_ms": 231.4, "num_books": 7
    }
  },
  "ram": {
    "rss_mb": 387.6, "vms_mb": 1240.2, "percent": 9.7, "available": true
  },
  "cache": { "ttl_sec": 180, "enabled_books": ["stake", "pinnacle", ...] },
  "any_error": false
}
```

- `proxy.by_book` → ideal para bar charts horizontales (calls) + stacked errors, o tabla.
- `proxy.summary.overall_error_rate` + `avg_latency_ms` → gauges / KPI cards.
- `ram` → gauge de consumo RAM (o sparkline histórico si se samplea en worker).
- Si `psutil` no está instalado: `ram.available=false` + nota (el feature es opcional y ligero).

### Endpoint legacy (compat)
`GET /api/scanner/state` — sigue devolviendo el shape anterior por libro (usado por Monitoring).

### Cómo se popula
- Cada `_fetch_book_fixtures` / `_fetch_sport_raw_fixtures` en `src/core/orchestration/cross_book.py` actualiza `_cb_book_metrics` (count, errors, last_latency_ms, last_error...).
- RAM se samplea on-demand vía `psutil.Process` (sin overhead si no se pide el endpoint).
- No hay logging pesado a DB para estas métricas (se mantiene bajo consumo RAM en VPS 4GB).

### Visualización recomendada (frontend)
Usar el JSON directamente:
- Barras: proxy calls + error_rate por book.
- Gauge: RAM % + overall error rate.
- Tabla: detalle por book con last_error destacado (rojo si presente).
- Polling cada 30-60s desde MonitoringApp.vue o Dashboard.

Añadir `pip install psutil` en el entorno/Docker para RAM completa (no obligatorio).

Esto reemplaza cualquier viz antigua externa manteniendo todo autocontenido y observable desde la misma app que corre el motor.
