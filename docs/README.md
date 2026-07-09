# KAIROS — Documentación

| Archivo | Descripción |
|---------|-------------|
| [../README.md](../README.md) | Guía principal: setup, uso, estructura del proyecto |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Arquitectura técnica + **diagramas de flujo Mermaid** (Engine workers, Scan+Allocator multi-sport, Live Monitor + delegación por deporte) |
| [betting_settlement_rules.md](betting_settlement_rules.md) | Reglas de settlement por book + **diagrama de estados** de apuestas (won/lost/void/pending) |
| [AUTH_FLOW.md](AUTH_FLOW.md) | Flujos de autenticación y proxies para books + Sofascore/Flashscore |
| [POLYMARKET_REAL_TRADING_PLAN.md](POLYMARKET_REAL_TRADING_PLAN.md) | ⚠️ OBSOLETO (2026-07-09): Polymarket fue removido por completo del proyecto (código y scope). Documento conservado solo como referencia histórica de diseño, no describe capacidad actual del sistema. |
| [ANALYTICS.md](ANALYTICS.md) | Consultas analíticas DuckDB/SQLite, endpoints `/api/analytics/*`, vistas `v_match_pnl` |
| [strategies_models.md](strategies_models.md) | Modelos estadísticos por deporte (ELO, Dixon-Coles, NBA, etc.) y madurez de cada estrategia |

---

## Resumen del Stack

| Capa | Tecnología |
|------|-----------|
| Backend | Python 3.13 + Flask |
| Base de datos | SQLite 3 via Peewee ORM (columnas `bet_type`, `source_group_key` para stat-hedge + asociaciones) |
| Frontend | Vue 3.4 + Pinia 2 + SASS (build con Vite 5) |
| Scraping | requests + curl_cffi + Playwright + BeautifulSoup |
| LLM | Gemini / OpenAI / Claude / Groq (multi-provider con fallback) |
| Math | Elo personalizado + SDI + Hedge analysis empírico |

**Tipos de apuestas (bet_type)**: intra-arb, cross-arb, stat, **stat-hedge** (todas las correlacionadas/intrínsecas), intra-hedge, cross-hedge. Labels en historial/portafolio usan bet_type. Pares/tripletas agrupados por match con chevron expandible + símbolos PNL (✓ verde positivo, ✕ roja pérdida, 🔵 azul solo void/cancelados). Per-bookie verbose de fixtures/mercados en scans.

## Comandos clave

```bash
# Iniciar servidor (motor autónomo + web)
python kairos.py run           # "dashboard" también funciona por compatibilidad

# Reconstruir frontend
cd frontend && npm run build

# Recalcular Elo desde cero
python scripts/rebuild_elo.py

# Tests
python -m pytest tests/ -v
```
