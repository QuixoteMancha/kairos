# 🎾 KAIROS — Arbitraje, Modelos por Deporte y Portafolio

Motor autónomo de arbitraje + valor (stat) + hedges sobre múltiples deportes (tenis, fútbol, básquet, etc.). Soporta arbs puros (intra/cross), stat con edge, y **stat-hedge** para apuestas correlacionadas (pares/tripletas con cobertura intrínseca).

Incluye wallet virtual, live monitor reactivo (solo datos de bookies), rotación anti-detección, rebalance, settlement y dashboard Vue.

> **Versión**: 2.3+ (refactor bet types + UI grouping) | **Stack**: Python 3.13 + Flask + Vue 3 + SQLite | **Última actualización**: 2026

**Tipos de apuestas (bet_type canónico)**:
- `intra-arb` / `cross-arb`: arbs garantizados.
- `stat`: valor/modelo.
- `stat-hedge`: correlacionadas (structural o value + hedge leg). Todas las correlacionadas usan este tipo.
- `intra-hedge` / `cross-hedge`: coberturas reactivas.

En historial y portafolio: pares/tripletas agrupados por match, con chevron expandible, labels desde `bet_type`, y símbolos por PNL neto del match (✓ verde positivo, ✕ roja pérdida, 🔵 azul solo para void/cancelados).

Per-bookie verbose (fixtures + mercados) en scans cross-book.

---

## 🚀 Inicio Rápido

### Prerrequisitos
- Python 3.13+
- Node.js 18+ (para el frontend)
- Google Chrome (extracción de cookies de Stake)
- API keys de algún provider LLM (Gemini, OpenAI, Groq…)

### Setup

```bash
cd kairos

# Backend Python
source env/bin/activate
pip install -r requirements.txt

# Frontend Vue 3 (solo si se modifica el frontend)
cd frontend && npm install && npm run build && cd ..
```

### Ejecutar

```bash
# Iniciar servidor completo (Flask + scraping en background)
python kairos.py start

# Detener servidor
python kairos.py stop

# Dashboard en: http://localhost:8765

# Docker autopilot (prechecks + build + up + healthcheck)
./kairos.sh autopilot
```

---

## 📁 Estructura del Proyecto

```
kairos/
│
├── kairos.py               ← Entry point (CLI: start / stop)
├── requirements.txt
│
├── src/
│   ├── core/               ← Configuración, auth, modelos base
│   │   ├── config.py       ← Variables de entorno, API keys, rutas
│   │   ├── models.py       ← Clases Pydantic base
│   │   ├── http_client.py  ← Cliente HTTP con anti-detección (Sofascore, Flashscore, books)
│   │   └── extract_cookies.py  ← Extracción de cookies (Stake etc.)
│   │
│   ├── database/
│   │   └── database.py     ← Peewee ORM (SQLite): 15 tablas + Wallet
│   │
│   ├── math/               ← Modelos cuantitativos
│   │   ├── elo.py          ← Sistema de rating Elo adaptado a tenis
│   │   ├── elo_engine.py   ← Motor de cálculo y actualización de Elo
│   │   ├── arbitrage.py    ← Detección de arbs (7 tipos)
│   │   ├── hedge_analysis.py ← Distribuciones empíricas BO3/BO5/WTA
│   │   ├── simulation.py   ← Simulación de escenarios de partidos
│   │   └── volatility.py   ← Índice SDI y métricas de volatilidad
│   │
│   ├── scrapers/           ← Extracción de datos (multi-sport)
│   │   ├── stake_scraper.py      ← GraphQL Stake (odds + scores en vivo)
│   │   ├── sofascore_scraper.py  ← Shim de compat. → sofascore/ (client/parsing/proxy). Bloqueado por challenge anti-bot, ver docs/betting_settlement_rules.md
│   │   ├── flashscore_scraper.py ← Resultados ITF/low-level (Flashscore)
│   │   ├── atp_wta_scraper.py    ← Calendario ATP/WTA fallback
│   │   ├── player_stats/         ← Player stats y ELO (Sofascore + models, multi-sport)
│   │   └── fetch_matches.py      ← Otros fetchers (si aplica)
│   │
│   ├── orchestration/
│   │   └── main.py         ← Orquestador del pipeline de scraping
│   │
│   └── web/
│       ├── dashboard.py    ← Servidor Flask + API REST (~40 endpoints)
│       ├── static/
│       │   └── dist/       ← Build compilado de Vue 3 (NO editar manual)
│       └── templates/
│           ├── index.html  ← Fallback legacy (si dist/ no existe)
│           └── match.html  ← Página de detalle de partido
│
├── frontend/               ← Código fuente del dashboard Vue 3
│   ├── package.json        ← Vite 5, Vue 3.4, Pinia 2, SASS
│   ├── vite.config.js      ← Build → src/web/static/dist/, proxy Flask:8765
│   └── src/
│       ├── main.js         ← Entry point Vue
│       ├── App.vue         ← Raíz: NavBar + DashboardPage/CalcPage + polling
│       ├── api/index.js    ← Capa API: apiFetch() + walletApi, scannerApi…
│       ├── stores/         ← Pinia: wallet, scanner, bets, matches, ui
│       ├── components/
│       │   ├── layout/     ← NavBar, AppToast
│       │   ├── wallet/     ← WalletCard, DepositModal
│       │   ├── arbs/       ← ArbScanner, ArbCard, AutoBetsLog
│       │   ├── bets/       ← PendingBets, BetHistory
│       │   └── matches/    ← MatchPanel
│       ├── pages/          ← DashboardPage, CalcPage
│       └── styles/         ← SCSS: variables (tema negro/amarillo), base
│
├── tests/                  ← Suite de tests con pytest
├── docs/                   ← Documentación técnica detallada
└── env/                    ← Virtualenv Python 3.13
```

---

## 🖥️ Dashboard Web

Accesible en `http://localhost:8765` tras `python kairos.py start`.

Construido con **Vue 3 + Pinia + SASS**, tema Binance-inspired (negro `#0b0e11`, amarillo `#f3ba2f`).

### Secciones

| Sección | Descripción |
|---------|-------------|
| **Wallet** | Balance libre, equity, P&L, gráfico histórico, depósitos |
| **Arb Scanner** | Escaneo en tiempo real / caché, auto-bets, log de resultados |
| **Pending Bets** | Apuestas activas con resolución manual (ganó/perdió) |
| **Bet History** | Historial paginado con legs expandibles |
| **Match Panel** | Partidos del día agrupados por torneo, filtros liga/fecha/búsqueda |
| **Calc** | Calculadora hedge (stake + ROI garantizado) |

### Rebuild del frontend

```bash
cd frontend
npm run build     # compila → src/web/static/dist/
```

---

## 💰 Wallet Virtual

### Concepto

```
Equity  = balance_inicial + sum(stakes pendientes) - pérdidas realizadas
Reserva = equity × 10%  (WALLET_RESERVE_PCT = 0.10)
Balance libre = max(0, balance_contable - reserva)
```

- **Reserva** del 10% del equity siempre intocable
- **Auto-settle**: liquida apuestas al detectar resultado vía Sofascore / Flashscore / book feeds (multi-sport)
- Los datos se leen **directamente de BD** en cada request (`api_wallet` no usa caché en memoria)

### Endpoints principales

```
GET  /api/wallet              → balance, equity, pnl, win_rate
POST /api/wallet/deposit      → {amount}
GET  /api/bets                → historial de apuestas
GET  /api/scan                → escanear arbs ahora
GET  /api/scan/cached         → último escaneo cacheado
POST /api/bet                 → {arb_id, stake_pct}
POST /api/resolve/<bet_id>    → internal (autonomous settlement only; no manual human resolution)
POST /api/settle              → auto-liquidar pendientes
POST /api/hedge-calc          → calcular hedge
```

---

## 🔍 Motor de Arbitraje

### 7 tipos de arb implementados

| Tipo | `arb_tipo` | Garantía | Descripción |
|------|-----------|---------|-------------|
| Inside | `inside` | ✅ 100% | Overround negativo en mismo mercado |
| Cross Winner/Sets | `cross` | ✅ 100% | Match winner vs Set winners (BO3 todos los outcomes) |
| Over/Under gap | `cross` | ⚡ Parcial | Líneas adyacentes de totales con gap garantizado |
| Handicap cross | `cross` | ✅ 100% | Hándicap entre líneas distintas |
| Game winner doble | `stat` | 📊 Estadístico | Mismo jugador en dos juegos distintos |
| Mispricing mismo lado | `stat` | 📊 Estadístico | Dos Overs (o Unders) de distintas líneas |
| Player Under + Match Over | `hedge` | 🧮 E[ROI] ≥ 2% | Correlación estructural mercado individual vs partido |

### Hedge-arb (tipo `hedge`)

Explota la correlación no modelada entre `player_total_games` y `match_total_games`:

```
E[ROI] = (1 - p_fail + p_both) / (1/odd_under + 1/odd_over) - 1
```

Con detección automática de formato (ATP BO3, ATP BO5 Grand Slams, WTA BO3).

---

## 📊 Modelos Matemáticos

### Sistema Elo (`src/math/elo.py`)

Rating adaptado a tenis por superficie:

```python
R_nuevo = R_viejo + K × (S - E)
E = 1 / (1 + 10^((R_oponente - R_viejo) / 400))
```

### SDI — Servicio Dominance Index (`src/math/volatility.py`)

Métrica propietaria de dominancia en el servicio. Valores > 70% indican jugador dominante.

---

## 🔐 Autenticación

- **Stake** (y otros books): Cookies extraídas de Chrome vía `extract_cookies.py` o BrightData proxies.
- **Sofascore / Flashscore**: BrightData residential proxies (impersonate) + public endpoints donde posible.
- **LLM**: Multi-provider con fallback (Gemini → OpenAI → Claude → Groq → OpenRouter)

### Configuración `.env`

```bash
# LLM
GEMINI_API_KEY=...
OPENAI_API_KEY=sk-...
GROQ_API_KEY=...

# Stake (si se usa login/cookies)
STAKE_USERNAME=...
STAKE_PASSWORD=...

# BrightData (para Sofascore/Flashscore)
BRIGHTDATA_PROXY=...

# Servidor
FLASK_PORT=8765
DEFAULT_PROVIDER=gemini
```

---

## 🧪 Tests

```bash
source env/bin/activate

# Suite completa
python -m pytest tests/ -v

# Tests específicos
python -m pytest tests/test_elo.py -v
python -m pytest tests/test_architecture.py -v
```

---

## 📚 Documentación

| Archivo | Contenido |
|---------|-----------|
| `docs/ARCHITECTURE.md` | Arquitectura + **diagramas Mermaid** (KairosEngine workers, Scan/Allocator multi-sport, Live Monitor + per-sport delegation) |
| `docs/betting_settlement_rules.md` | Reglas por bookie + **diagrama de estados** de apuestas (won/lost/void/pending + grupos stat-hedge) |
| `docs/AUTH_FLOW.md` | Flujos de autenticación (Stake, proxies para Sofascore/Flashscore) |

---

## 📈 Estado del Proyecto (Abril 2026)

### ✅ Completado
- 7 tipos de arb (inside, cross, stat, hedge, live-hedge) + multi-sport (tennis/football/basketball/baseball/ice_hockey/american_football)
- Dashboard Vue 3 + Pinia con tema negro/amarillo + grouping de pares/tripletas por match
- Wallet virtual con reserva dinámica + book-specific settlement rules
- Auto-settle por score Sofascore / Flashscore / book feeds (multi-sport)
- API Flask con ~40 endpoints, sin caché de browser
- Scrapers Stake / Sofascore / Flashscore + player stats unificados
- Elo por superficie + SDI + hedge analysis + strategy allocator (1% daily ROI target)
- (descripción de arquitectura anterior eliminada)
- Suite de tests pytest

### 🔄 En desarrollo / Sugerencias
- Enriquecimiento continuo de parsers para elapsed/periods/retired en todos los deportes
- Más madurez en settlement rules por bookie + meta tracking
- Backtesting y simulación avanzada
- UI para contexto de resolución manual (result_info)

---

*MIT License*
