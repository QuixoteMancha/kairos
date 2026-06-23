#!/usr/bin/env bash
# rebuild.sh — Rebuild y redeploy del servicio kairos.
# Uso:
#   ./rebuild.sh                    # tests + frontend build + imagen Docker + restart kairos
#   ./rebuild.sh --full             # igual pero hace down/up de todo el stack
#   ./rebuild.sh --skip-frontend    # salta el npm build (solo rebuild imagen)
#   ./rebuild.sh --frontend         # SOLO frontend: npm build + deploy dist al container vivo
#   ./rebuild.sh --skip-tests       # salta pytest
#   ./rebuild.sh --no-cache         # docker build sin cache
#   ./rebuild.sh --no-logs          # no hace tail de logs al final
#   ./rebuild.sh --fast             # rápido: salta tests, usa cache
#   ./rebuild.sh --clean-bets       # limpia apuestas (walletbet + legs + ledger + logs + rejected)
#   ./rebuild.sh --clean-bets --reset-balances   # limpia apuestas + reset wallets a 50 EUR
#   ./rebuild.sh --clean-all        # resetea TODA la data (NO toca wallet por defecto)
#   ./rebuild.sh --clean-all --reset-balances    # como --clean-all + reset wallets
#
# ORDEN DE EJECUCIÓN:
#   pre-clean → tests → [--clean-bets AQUÍ, DB libre] → frontend build → docker build → up → cp frontend
#
# El --clean-bets se ejecuta DESPUÉS de los tests (no antes) porque los tests
# levantan un contenedor que inicializa la DB y puede dejar WAL locks abiertos.
# Moviéndolo aquí garantizamos que la DB está libre antes de tocarla.

set -euo pipefail

# ── Mantenimiento preventivo: liberar espacio ──
echo "▶ Mantenimiento: Liberando espacio de caché antigua..."
docker image prune -f
docker builder prune -f --filter "until=24h"
echo "  ✓ Limpieza de caché completada."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ensure_node_npm() {
    # En shells SSH no interactivos, npm puede no estar en PATH cuando se usa nvm.
    if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
        return 0
    fi

    if [ -s "$HOME/.nvm/nvm.sh" ]; then
        # shellcheck disable=SC1090
        . "$HOME/.nvm/nvm.sh"
        nvm use --silent default >/dev/null 2>&1 || nvm use --silent --lts >/dev/null 2>&1 || true
    fi

    if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
        echo "  ✗ Node/npm no disponibles en PATH"
        echo "  Sugerencia: instala Node LTS o configura nvm para shells no interactivos"
        exit 1
    fi
}

frontend_install_and_build() {
    ensure_node_npm
    if [ -f package-lock.json ]; then
        npm ci --no-audit --no-fund
    else
        npm install --no-audit --no-fund
    fi
    npm run build
}

show_help() {
    cat << 'HELPEOF'
Uso: ./rebuild.sh [opciones]

Opciones principales:
  ./rebuild.sh                    Tests + build frontend + imagen Docker + restart solo kairos
  ./rebuild.sh --full             Igual pero hace down/up completo del stack

Opciones de velocidad:
  --fast, --quick, --dev, --only-code, --only-app
                                  Modo rápido: salta tests, usa cache de Docker
  --frontend, --only-frontend, --fe
                                  SOLO frontend: npm build + copia dist al container en caliente.

Opciones de limpieza (se ejecutan DESPUÉS de tests, con DB libre):
  --clean-bets                    Limpia apuestas (walletbet + legs + ledger + market_outcome_log
                                  + rejectedopportunity).
  --clean-bets --reset-balances   Limpia apuestas + resetea wallets a 50 EUR initial.
  --clean-all                     Resetea TODA la data. Por defecto NO toca wallet.
  --clean-all --reset-balances    Como --clean-all + resetea wallets.

Otras opciones:
  --skip-tests    Salta pytest
  --skip-frontend Salta el build de Vue
  --no-cache      Fuerza docker build sin cache
  --no-logs       No muestra logs al final
  --help, -h      Muestra esta ayuda

Ejemplos:
  ./rebuild.sh --frontend
  ./rebuild.sh --fast
  ./rebuild.sh --fast --clean-bets
  ./rebuild.sh --fast --clean-all --reset-balances
  ./rebuild.sh --clean-bets --reset-balances
HELPEOF
}

FULL=false
SKIP_FRONTEND=false
SKIP_TESTS=false
NO_CACHE=false
NO_LOGS=false
FAST=false
FRONTEND_ONLY=false
CLEAN_BETS=false
CLEAN_ALL=false
RESET_BALANCES=false
PREBUILT_IMAGE=false

for arg in "$@"; do
    case "$arg" in
        --help|-h)            show_help; exit 0 ;;
        --full)               FULL=true ;;
        --skip-frontend)      SKIP_FRONTEND=true ;;
        --skip-tests)         SKIP_TESTS=true ;;
        --no-cache)           NO_CACHE=true ;;
        --no-logs)            NO_LOGS=true ;;
        --fast|--quick|--dev|--only-code|--only-app)
                              FAST=true ;;
        --frontend|--only-frontend|--fe)
                              FRONTEND_ONLY=true ;;
        --clean-bets|--purge-bets|--clean-db|--reset-bets)
                              CLEAN_BETS=true ;;
        --clean-all)          CLEAN_ALL=true ;;
        --reset-balances)     RESET_BALANCES=true ;;
        *)                    echo "Argumento desconocido: $arg"; exit 1 ;;
    esac
done

if [ "$CLEAN_ALL" = true ];      then CLEAN_BETS=true; fi
if [ "$RESET_BALANCES" = true ]; then CLEAN_BETS=true; fi

# Puerto del host para healthcheck
if [ -z "${KAIROS_PORT:-}" ] && [ -f .env ]; then
  KAIROS_PORT=$(grep -E '^KAIROS_PORT=' .env | head -1 | cut -d= -f2- | tr -d ' "'"'"'\r' || true)
fi
HOST_PORT="${KAIROS_PORT:-8765}"

# ── FAST-PATH: --frontend (solo frontend, container vivo, sin tocar imagen) ──
if [ "$FRONTEND_ONLY" = true ]; then
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  KAIROS · Rebuild SOLO FRONTEND (deploy en caliente)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    CONTAINER=$(docker ps --filter "name=kairos" --format "{{.Names}}" | head -1)
    if [ -z "$CONTAINER" ]; then
        echo "  ✗ No hay container 'kairos' corriendo. Usa ./rebuild.sh primero."
        exit 1
    fi

    echo ""
    echo "▶ [1/2] Building Vue frontend (npm run build)…"
    cd frontend && frontend_install_and_build
    cd "$SCRIPT_DIR"
    COPY_SRC="$SCRIPT_DIR/src/web/static/dist"
    if [ ! -d "$COPY_SRC" ]; then
        echo "  ✗ Error: no se encontró el directorio de build '$COPY_SRC'"
        exit 1
    fi
    echo "  ✓ Frontend build OK"

    echo ""
    echo "▶ [2/2] Deploy dist al container '$CONTAINER'…"
    docker exec "$CONTAINER" rm -rf /app/src/web/static/dist
    docker cp "$COPY_SRC" "$CONTAINER":/app/src/web/static/dist
    echo "  ✓ dist copiado"

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  ✓ Frontend desplegado → http://localhost:${HOST_PORT}/wallet"
    echo "  (recarga con Ctrl+Shift+R para saltar cache del navegador)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    exit 0
fi

if [ "$FAST" = true ]; then
    SKIP_TESTS=true
fi

STEPS=4
if [ "$FAST" = true ]; then STEPS=3; fi
_STEP=0
step() { _STEP=$((_STEP + 1)); echo ""; echo "▶ [$_STEP/$STEPS] $*"; }

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  KAIROS · Rebuild"
if [ "$FAST" = true ]; then echo "  (modo --fast: con cache, salta tests)"; fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Pre-clean: matar procesos kairos.py en el host y contenedores ──
echo ""
echo "▶ Pre-clean: matando procesos 'python *kairos.py' y contenedores kairos..."

if pgrep -f "kairos.py" > /dev/null 2>&1; then
    echo "  - Matando procesos kairos.py..."
    pkill -f "kairos.py" 2>/dev/null || true
    sleep 1
    pkill -f "python.*kairos.py" 2>/dev/null || true
    pkill -9 -f "kairos.py" 2>/dev/null || true
else
    echo "  - No se encontraron procesos 'python *kairos.py' en el host"
fi

echo "  - Parando contenedores kairos..."
docker compose stop kairos 2>/dev/null || true

KAIROS_CONTAINERS=$(docker ps -a --filter "name=kairos" --format "{{.Names}}" 2>/dev/null || true)
if [ -n "$KAIROS_CONTAINERS" ]; then
    echo "  - Eliminando: $KAIROS_CONTAINERS"
    echo "$KAIROS_CONTAINERS" | xargs -r docker stop 2>/dev/null || true
    echo "$KAIROS_CONTAINERS" | xargs -r docker rm -f 2>/dev/null || true
else
    echo "  - No se encontraron contenedores kairos"
fi

docker compose down --remove-orphans 2>/dev/null || true
echo "  ✓ Pre-clean completado"

# ── 1. Tests ──────────────────────────────────────────────────────
if [ "$SKIP_TESTS" = false ]; then
    step "Corriendo tests (pytest dentro de Docker)…"
    # Build previo para que los tests usen SIEMPRE el código recién sincronizado
    # y las dependencias actuales de requirements.txt.
    if [ "$NO_CACHE" = true ]; then
        docker compose build --no-cache kairos
    else
        docker compose build kairos
    fi
    PREBUILT_IMAGE=true
    # Aislar tests del volumen persistente /data para evitar estado residual
    # entre corridas (bets/tracker previos) que vuelve flaky algunos tests.
    docker compose run --rm \
        -e DB_PATH=/tmp/kairos_test.db \
        kairos bash -c "ls -F && python3 -m pytest tests/ -q --tb=short"
    echo "  ✓ Tests OK"

    # Eliminar contenedores residuales de los tests antes de tocar la DB.
    # Si quedan activos pueden tener un WAL lock sobre kairos.db.
    echo "  - Limpiando contenedores residuales de tests..."
    docker ps -a --filter "name=kairos" --format "{{.Names}}" | xargs -r docker rm -f 2>/dev/null || true
else
    step "Tests — OMITIDOS (--skip-tests)"
fi

# ── Limpieza de DB ────────────────────────────────────────────────
# Se ejecuta AQUÍ (después de tests) porque en este punto garantizamos que:
#   1. Pre-clean ya mató todos los contenedores kairos.
#   2. Los tests terminaron y sus contenedores fueron eliminados arriba.
#   3. Ningún proceso tiene la DB abierta → sin WAL locks.
# En el script original estaba ANTES de los tests, lo que permitía que
# el contenedor de tests volviera a abrir la DB después de la limpieza.
if [ "$CLEAN_BETS" = true ]; then
    echo ""
    if [ "$CLEAN_ALL" = true ]; then
        echo "▶ Limpieza completa de data (--clean-all)..."
    elif [ "$RESET_BALANCES" = true ]; then
        echo "▶ Limpieza de apuestas + reset de balances..."
    else
        echo "▶ Limpieza de base de apuestas (--clean-bets)..."
    fi

    DB_VOLUME="kairos_kairos_data"
    DB_MOUNT="/data"

    # La limpieza usa un contenedor con Python para operar sobre el volumen.
    # Si no existe la imagen local, la construimos para garantizar ejecución.
    if ! docker image inspect kairos:latest >/dev/null 2>&1; then
        echo "  - Imagen kairos:latest no existe. Construyendo para limpieza..."
        docker compose build kairos
    fi

    # Backup
    BACKUP_NAME="kairos_bets_clean_$(date +%Y%m%d_%H%M%S).db"
    echo "  - Haciendo backup..."
    mkdir -p "$PWD/data/backups"
    docker run --rm --user root \
        -v "${DB_VOLUME}:${DB_MOUNT}" \
        -v "$PWD/data/backups:/backup" \
        kairos:latest \
        python3 -c '
import shutil, os
src = "/data/kairos.db"
dst = "/backup/'"${BACKUP_NAME}"'"
os.makedirs("/backup", exist_ok=True)
shutil.copy2(src, dst)
print("Backup creado:", dst)
'

    echo "  - Ejecutando limpieza..."

    CLEAN_PY="/tmp/kairos_clean_bets_$$.py"
    cat > "${CLEAN_PY}" << 'PYEOF'
import sqlite3
import os
from datetime import datetime

db_path = "/data/kairos.db"
clean_all = os.environ.get("CLEAN_ALL", "false").lower() == "true"
reset_balances = os.environ.get("RESET_BALANCES", "false").lower() == "true"

conn = sqlite3.connect(db_path)
c = conn.cursor()

# Los modelos Peewee NO declaran ForeignKeyField reales → no hay constraints
# en SQLite que bloqueen DELETEs. FK OFF para asegurar, y WAL checkpoint
# para liberar cualquier lock residual de sesiones anteriores.
c.execute("PRAGMA foreign_keys = OFF;")
c.execute("PRAGMA wal_checkpoint(TRUNCATE);")

print("=== Tablas ANTES de limpiar ===")
bet_tables = ["walletbet", "walletbetleg", "walletledger", "market_outcome_log", "rejectedopportunity"]
data_tables = ["match", "player", "tournament", "playerelo", "matchstats", "sofascorematchcache", "betmarket"]
all_tables = bet_tables + data_tables

for tbl in all_tables:
    try:
        c.execute(f"SELECT COUNT(*) FROM {tbl}")
        print(f"  {tbl}: {c.fetchone()[0]}")
    except Exception as e:
        print(f"  {tbl}: ERROR ({e})")

print("\nLimpiando...")

c.execute("DELETE FROM walletbetleg;")
print(f"  walletbetleg borradas: {c.rowcount}")

c.execute("DELETE FROM market_outcome_log;")
print(f"  market_outcome_log borradas: {c.rowcount}")

c.execute("DELETE FROM walletledger;")
print(f"  walletledger borradas: {c.rowcount}")

c.execute("DELETE FROM walletbet;")
print(f"  walletbet borradas: {c.rowcount}")

c.execute("DELETE FROM rejectedopportunity;")
print(f"  rejectedopportunity borradas: {c.rowcount}")

if clean_all:
    print("\n--- CLEAN-ALL: borrando data adicional ---")
    for tbl in ["match", "player", "tournament", "playerelo", "matchstats",
                "sofascorematchcache", "betmarket"]:
        try:
            c.execute(f"DELETE FROM {tbl};")
            print(f"  {tbl}: {c.rowcount}")
        except Exception as e:
            print(f"  {tbl}: ERROR ({e})")

if reset_balances:
    print("\n--- Reseteando wallets a 50 EUR initial ---")
    ts = datetime.utcnow().isoformat()
    try:
        c.execute("""
            DELETE FROM wallet
            WHERE platform GLOB '*[A-Z]*' OR platform LIKE '%\\_' ESCAPE '\\'
        """)
        print(f"  wallets junk eliminadas: {c.rowcount}")
    except Exception as e:
        print(f"  (wallets junk: {e})")
    c.execute("""
        UPDATE wallet
        SET initial_balance = 50.0,
            balance = 50.0,
            deposits = 0.0,
            withdrawals = 0.0,
            equity = 50.0,
            started_at = ?
    """, (ts,))
    print(f"  wallets reseteadas: {c.rowcount}")
else:
    # Sin apuestas pendientes el balance libre debe volver al inicial.
    c.execute("""
        UPDATE wallet
        SET balance = initial_balance,
            equity  = initial_balance
    """)
    print(f"  balances re-sincronizados a initial: {c.rowcount}")

conn.commit()

print("\n=== Tablas DESPUÉS de limpiar ===")
for tbl in all_tables:
    try:
        c.execute(f"SELECT COUNT(*) FROM {tbl}")
        print(f"  {tbl}: {c.fetchone()[0]}")
    except Exception as e:
        print(f"  {tbl}: ERROR ({e})")

c.execute("VACUUM;")
print("\n✓ VACUUM ejecutado")

# Checkpoint final: fusiona TODOS los DELETE al kairos.db del volumen antes de
# cerrar. Sin esto los borrados podrían quedar sólo en kairos.db-wal dentro del
# volumen Docker y reaparecer si un backup/restore copia sólo el .db.
c.execute("PRAGMA wal_checkpoint(TRUNCATE);")
print("✓ WAL checkpoint(TRUNCATE) ejecutado")
conn.close()
print("✓ Limpieza completada.")
PYEOF

    docker run --rm --user root \
        -v "${DB_VOLUME}:${DB_MOUNT}" \
        -v "${CLEAN_PY}:/tmp/clean_bets.py:ro" \
        -e CLEAN_ALL="${CLEAN_ALL}" \
        -e RESET_BALANCES="${RESET_BALANCES}" \
        kairos:latest \
        python3 /tmp/clean_bets.py

    rm -f "${CLEAN_PY}"

    if [ "$CLEAN_ALL" = true ]; then
        echo "  ✓ Limpieza completa (--clean-all) finalizada"
    elif [ "$RESET_BALANCES" = true ]; then
        echo "  ✓ Limpieza + reset de balances finalizada"
    else
        echo "  ✓ Limpieza de apuestas finalizada"
    fi
fi

# ── 2. Build frontend Vue ─────────────────────────────────────────
# Solo compila el dist. El docker cp ocurre en el paso 4 (post-up).
if [ "$SKIP_FRONTEND" = false ]; then
    step "Building Vue frontend…"
    cd frontend && frontend_install_and_build
    cd "$SCRIPT_DIR"
    COPY_SRC="$SCRIPT_DIR/src/web/static/dist"
    echo "  Src build: $COPY_SRC"
    if [ ! -d "$COPY_SRC" ]; then
        echo "  ✗ Error: no se encontró el directorio de build '$COPY_SRC'"
        exit 1
    fi
    echo "  ✓ Frontend compilado → dist listo"
else
    step "Frontend — OMITIDO (--skip-frontend)"
    COPY_SRC=""
fi

# ── 3. Build imagen Docker ────────────────────────────────────────
if [ "$PREBUILT_IMAGE" = true ] && [ "$NO_CACHE" = false ]; then
    step "Build Docker image — OMITIDO (ya construido para tests)"
elif [ "$FAST" = true ]; then
    step "Building Docker image kairos:latest (modo rápido con cache)…"
    if [ "$NO_CACHE" = true ]; then
        docker compose build --no-cache kairos
    else
        docker compose build kairos
    fi
else
    step "Building Docker image kairos:latest…"
    if [ "$NO_CACHE" = true ]; then
        docker compose build --no-cache kairos
    else
        docker compose build kairos
    fi
fi

# ── 4. Restart / up ──────────────────────────────────────────────
if [ "$FULL" = true ]; then
    step "Restarting full stack (--full)…"
    docker compose down
    docker compose up -d
else
    if [ "$FAST" = true ]; then
        step "Restarting only kairos (modo rápido --fast)…"
    else
        step "Restarting only kairos container…"
    fi
    docker compose up -d --no-deps kairos
fi

# ── Copiar frontend al contenedor ya levantado ────────────────────
# Se hace AQUÍ (post-up) porque el contenedor fue eliminado en el pre-clean
# y recién se acaba de recrear en el paso anterior.
if [ "$SKIP_FRONTEND" = false ] && [ -n "${COPY_SRC:-}" ] && [ -d "$COPY_SRC" ]; then
    echo ""
    echo "  Copiando frontend al contenedor…"
    CONTAINER=$(docker ps --filter "name=kairos" --format "{{.Names}}" | head -1)
    if [ -n "$CONTAINER" ]; then
        docker exec "$CONTAINER" rm -rf /app/src/web/static/dist
        docker cp "$COPY_SRC" "$CONTAINER":/app/src/web/static/dist
        echo "  ✓ dist copiado al contenedor '$CONTAINER'"
    else
        echo "  ⚠ No se encontró contenedor kairos corriendo — dist no copiado"
    fi
fi

# ── Healthcheck ───────────────────────────────────────────────────
echo ""
echo "  Esperando healthcheck…"
for i in $(seq 1 20); do
    if curl --fail --silent "http://localhost:${HOST_PORT}/healthz" > /dev/null 2>&1; then
        echo "  ✓ Kairos UP (http://localhost:${HOST_PORT})"
        break
    fi
    sleep 2
    if [ "$i" = "20" ]; then
        echo "  ⚠ Healthcheck no respondió tras 40s — revisa los logs"
    fi
done

# ── Status ────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Estado del stack:"
docker compose ps
if [ "$NO_LOGS" = false ]; then
    echo ""
    echo "  Logs (Ctrl+C para salir):"
    docker compose logs -f --tail=40 kairos
fi