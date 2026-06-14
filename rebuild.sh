#!/usr/bin/env bash
# rebuild.sh — Rebuild y redeploy del servicio kairos.
# Uso:
#   ./rebuild.sh                    # tests + frontend build + imagen Docker + restart kairos
#   ./rebuild.sh --full             # igual pero hace down/up de todo el stack
#   ./rebuild.sh --skip-frontend    # salta el npm build (solo rebuild imagen)
#   ./rebuild.sh --frontend         # SOLO frontend: npm build + deploy dist al container vivo (sin rebuild imagen ni tests)
#   ./rebuild.sh --skip-tests       # salta pytest
#   ./rebuild.sh --no-cache         # docker build sin cache
#   ./rebuild.sh --no-logs          # no hace tail de logs al final
#   ./rebuild.sh --fast             # rápido: salta tests, usa cache, sólo frontend + código de la app (no red nueva ni deps pesadas)
#   ./rebuild.sh --clean-bets       # limpia solo la base de apuestas (walletbet + legs + ledger refs + logs + rejected) con cascades
#   ./rebuild.sh --clean-bets --reset-balances
#                                 # como --clean-bets + también resetea los balances de las wallets a 50 EUR initial
#   ./rebuild.sh --clean-all        # resetea TODA la data (apuestas + matches/players/ELO/caches/betmarket/full ledger/etc.)
#                                 #   DEJA LOS BALANCES de las wallets por defecto (no toca la tabla wallet)
#   ./rebuild.sh --clean-all --reset-balances
#                                 # como --clean-all + también resetea wallets a 50 EUR initial
#
# Comportamiento:
#   - Siempre mata primero procesos "python *kairos.py" que corran en el host.
#   - Siempre para y elimina explícitamente todos los contenedores cuyo nombre contenga "kairos".
#   - Luego ejecuta los pasos normales (tests, build frontend, build imagen, up del servicio).
#   - Con --fast se salta pytest, se fuerza build con cache y se hace sólo up -d --no-deps del servicio kairos.
#   - --clean-bets: solo apuestas (con orden correcto de cascades/referencias).
#     Tras borrar las apuestas, re-sincroniza balance/equity al initial (sin pending,
#     el balance libre vuelve al inicial → evita wallets en negativo inconsistente).
#   - --clean-bets --reset-balances: lo anterior + reset completo de wallet
#     (initial/deposits/withdrawals a 0/50) + elimina wallets junk (mayúsculas / sufijo _).
#   - --clean-all: bets + toda la data (matches, players, ELO, caches, betmarket, full ledger, etc.).
#     Por defecto **NO toca la tabla wallet** (balances quedan exactamente como están).
#   - --clean-all --reset-balances: como --clean-all + también resetea wallets.
#   Se puede combinar con --fast. Se puede combinar --clean-bets + --reset-balances libremente.

set -euo pipefail

# ── Mantenimiento preventivo: liberar espacio ──
echo "▶ Mantenimiento: Liberando espacio de caché antigua..."
# Borra imágenes "dangling" (huérfanas)
docker image prune -f 
# Borra cachés de construcción viejas (más de 24h para no afectar builds rápidos)
docker builder prune -f --filter "until=24h"
echo "  ✓ Limpieza de caché completada."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

show_help() {
    cat << 'EOF'
Uso: ./rebuild.sh [opciones]

Opciones principales:
  ./rebuild.sh                    Tests + build frontend + imagen Docker + restart solo kairos
  ./rebuild.sh --full             Igual pero hace down/up completo del stack

Opciones de velocidad:
  --fast, --quick, --dev, --only-code, --only-app
                                  Modo rápido: salta tests, usa cache de Docker,
                                  solo actualiza frontend + código de la app (no recrea red)
  --frontend, --only-frontend, --fe
                                  SOLO frontend: npm build + copia dist al container en caliente.
                                  No corre tests, no reconstruye la imagen Docker, no reinicia.

Opciones de limpieza (se ejecutan antes del build):
  --clean-bets                    Limpia solo la base de apuestas (walletbet + legs + refs en ledger
                                  + market_outcome_log + rejectedopportunity) respetando orden de cascades.
  --clean-bets --reset-balances   Limpia apuestas + resetea las wallets a 50 EUR initial.

  --clean-all                     Resetea TODA la data (apuestas + matches, players, ELO, caches,
                                  betmarket, ledger completo, etc.).
                                  Por defecto DEJA los balances de las wallets tal como están.

  --clean-all --reset-balances    Como --clean-all + también resetea las wallets a 50 EUR initial.

Otras opciones:
  --skip-tests                    Salta la ejecución de pytest
  --skip-frontend                 Salta el build de Vue (npm run build)
  --frontend                      Solo build + deploy del frontend al container vivo (rápido)
  --no-cache                      Fuerza docker build sin usar cache
  --no-logs                       No muestra los logs al final (solo hace el rebuild)
  --help, -h                      Muestra esta ayuda

Comportamiento siempre activo:
  - Mata procesos "python *kairos.py" que estén corriendo en el host.
  - Para y elimina explícitamente todos los contenedores cuyo nombre contenga "kairos".
  - Se puede combinar --fast con las opciones de limpieza.

Ejemplos útiles:
  ./rebuild.sh --frontend
  ./rebuild.sh --fast
  ./rebuild.sh --fast --clean-bets
  ./rebuild.sh --fast --clean-all --reset-balances
  ./rebuild.sh --clean-bets --reset-balances
EOF
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

for arg in "$@"; do
    case "$arg" in
        --help|-h)
            show_help
            exit 0
            ;;
        --full)           FULL=true ;;
        --skip-frontend)  SKIP_FRONTEND=true ;;
        --skip-tests)     SKIP_TESTS=true ;;
        --no-cache)       NO_CACHE=true ;;
        --no-logs)        NO_LOGS=true ;;
        --fast|--quick|--dev|--only-code|--only-app)
                          FAST=true ;;
        --frontend|--only-frontend|--fe)
                          FRONTEND_ONLY=true ;;
        --clean-bets|--purge-bets|--clean-db|--reset-bets)
                          CLEAN_BETS=true ;;
        --clean-all)
                          CLEAN_ALL=true ;;
        --reset-balances)
                          RESET_BALANCES=true ;;
        *)                echo "Argumento desconocido: $arg"; exit 1 ;;
    esac
done

if [ "$CLEAN_ALL" = true ]; then
    CLEAN_BETS=true  # clean-all incluye clean-bets + más
fi

if [ "$RESET_BALANCES" = true ]; then
    CLEAN_BETS=true
fi

# Puerto del host para healthcheck (respeta KAIROS_PORT del .env si existe)
# (se necesita aquí también para el fast-path de --frontend)
if [ -z "${KAIROS_PORT:-}" ] && [ -f .env ]; then
  KAIROS_PORT=$(grep -E '^KAIROS_PORT=' .env | head -1 | cut -d= -f2- | tr -d ' "''\r' || true)
fi
HOST_PORT="${KAIROS_PORT:-8765}"

# ── FAST-PATH: --frontend (solo frontend, container vivo, sin tocar imagen) ──
# No mata el container, no corre tests, no reconstruye la imagen: build Vue +
# copia dist al container corriendo (mismo flujo que scripts/deploy_frontend.sh).
if [ "$FRONTEND_ONLY" = true ]; then
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  KAIROS · Rebuild SOLO FRONTEND (deploy en caliente)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    CONTAINER=$(docker ps --filter "name=kairos" --format "{{.Names}}" | head -1)
    if [ -z "$CONTAINER" ]; then
        echo "  ✗ No hay container 'kairos' corriendo. Usa ./rebuild.sh (build normal) primero."
        exit 1
    fi

    echo ""
    echo "▶ [1/2] Building Vue frontend (npm run build)…"
    docker compose run --rm kairos bash -c "cd frontend && npm install && npm run build"
    cd "$SCRIPT_DIR"
    echo "  ✓ Frontend build OK → src/web/static/dist"

    echo ""
    echo "▶ [2/2] Deploy dist al container '$CONTAINER' (sin rebuild de imagen)…"
    docker exec "$CONTAINER" rm -rf /app/src/web/static/dist
    echo "  ✓ dist copiado"

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  ✓ Frontend desplegado → http://localhost:${HOST_PORT}/wallet"
    echo "  (recarga con Ctrl+Shift+R para saltar cache del navegador)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    exit 0
fi

# --fast implica comportamiento ligero: salta tests, build con cache, solo reinicia el servicio kairos
if [ "$FAST" = true ]; then
    SKIP_TESTS=true
    # SKIP_FRONTEND se respeta si se pasó explícitamente; por defecto se hace el build frontend
    NO_CACHE=false
fi

# Puerto del host para healthcheck (respeta KAIROS_PORT del .env si existe)
if [ -z "${KAIROS_PORT:-}" ] && [ -f .env ]; then
  KAIROS_PORT=$(grep -E '^KAIROS_PORT=' .env | head -1 | cut -d= -f2- | tr -d ' "''\r' || true)
fi
HOST_PORT="${KAIROS_PORT:-8765}"

STEPS=4  # siempre 4 fases anunciadas (incluyendo las OMITIDAS)
if [ "$FAST" = true ]; then
    STEPS=3  # con --fast anunciamos menos pasos pesados
fi
_STEP=0
step() { _STEP=$((_STEP + 1)); echo ""; echo "▶ [$_STEP/$STEPS] $*"; }

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  KAIROS · Rebuild"
if [ "$FAST" = true ]; then
    echo "  (modo --fast: sólo frontend + código de la app, con cache)"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Pre-clean: matar procesos kairos.py en el host y contenedores ──
echo ""
echo "▶ Pre-clean: matando procesos 'python *kairos.py' y contenedores kairos..."

# Matar procesos en el host que ejecuten kairos.py (varias variantes)
if pgrep -f "kairos.py" > /dev/null 2>&1; then
    echo "  - Matando procesos que contienen 'kairos.py' (python kairos.py)..."
    pkill -f "kairos.py" 2>/dev/null || true
    sleep 1
    # Intento más específico
    pkill -f "python.*kairos.py" 2>/dev/null || true
    # Forzar si aún viven
    pkill -9 -f "kairos.py" 2>/dev/null || true
else
    echo "  - No se encontraron procesos 'python *kairos.py' corriendo en el host"
fi

# Parar explícitamente TODOS los contenedores cuyo nombre contenga "kairos"
echo "  - Parando explícitamente contenedores kairos vía docker compose (si existe el proyecto)..."
docker compose stop kairos 2>/dev/null || true

KAIROS_CONTAINERS=$(docker ps -a --filter "name=kairos" --format "{{.Names}}" 2>/dev/null || true)
if [ -n "$KAIROS_CONTAINERS" ]; then
    echo "  - Parando y eliminando contenedores kairos encontrados: $KAIROS_CONTAINERS"
    echo "$KAIROS_CONTAINERS" | xargs -r docker stop 2>/dev/null || true
    echo "$KAIROS_CONTAINERS" | xargs -r docker rm -f 2>/dev/null || true
else
    echo "  - No se encontraron contenedores con 'kairos' en el nombre"
fi

# Por si hay un stack de compose activo, intentar bajarlo (no falla si no existe)
docker compose down --remove-orphans 2>/dev/null || true
echo "  ✓ Pre-clean completado"

# ── Opcional: Limpieza de base de apuestas (con cascades y orden correcto) ──
if [ "$CLEAN_BETS" = true ]; then
    echo ""
    if [ "$CLEAN_ALL" = true ]; then
        echo "▶ Limpieza completa de data (--clean-all)..."
    elif [ "$RESET_BALANCES" = true ]; then
        echo "▶ Limpieza de apuestas + reset de balances (--clean-bets --reset-balances)..."
    else
        echo "▶ Limpieza de base de apuestas (--clean-bets)..."
    fi

    DB_VOLUME="kairos_kairos_data"
    DB_MOUNT="/data"
    DB_PATH="${DB_MOUNT}/kairos.db"

    # Backup antes de tocar nada destructivo
    BACKUP_NAME="kairos_bets_clean_$(date +%Y%m%d_%H%M%S).db"
    echo "  - Haciendo backup de la DB antes de limpiar..."
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
' || echo "  ⚠ Backup falló (continuando de todos modos)"

    echo "  - Ejecutando queries de limpieza con orden correcto para referencias (cascades manuales)..."

    # Write Python cleanup script on the HOST (safest quoting).
    # Then mount it into the container. The Python never goes through shell expansion here.
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
c.execute("PRAGMA foreign_keys = ON;")

print("=== Tablas ANTES de limpiar ===")
bet_tables = ["walletbet", "walletbetleg", "walletledger", "market_outcome_log", "rejectedopportunity"]
data_tables = ["match", "player", "tournament", "playerelo", "matchstats", "sofascorematchcache", "betmarket"]
all_tables = bet_tables + data_tables

for tbl in all_tables:
    try:
        c.execute(f"SELECT COUNT(*) FROM {tbl}")
        print(f"  {tbl}: {c.fetchone()[0]}")
    except:
        pass

print("\nLimpiando...")

# Order matters for FK/refs (manual cascade)
c.execute("DELETE FROM walletbetleg;")
print(f"  walletbetleg borradas: {c.rowcount}")

c.execute("DELETE FROM market_outcome_log;")
print(f"  market_outcome_log borradas: {c.rowcount}")

c.execute('DELETE FROM walletledger WHERE ref_table = "walletbet";')
print(f"  walletledger (bet refs) borradas: {c.rowcount}")

c.execute("DELETE FROM walletbet;")
print(f"  walletbet borradas: {c.rowcount}")

c.execute("DELETE FROM rejectedopportunity;")
print(f"  rejectedopportunity borradas: {c.rowcount}")

if clean_all:
    print("\n--- CLEAN-ALL: borrando data adicional ---")
    for tbl in ["match", "player", "tournament", "playerelo", "matchstats", "sofascorematchcache", "betmarket"]:
        try:
            c.execute(f"DELETE FROM {tbl};")
            print(f"  {tbl}: {c.rowcount}")
        except:
            pass
    c.execute("DELETE FROM walletledger;")
    print(f"  walletledger (todo): {c.rowcount}")

if reset_balances:
    print("\n--- Reseteando balances de wallets a 50 EUR initial ---")
    ts = datetime.utcnow().isoformat()
    # Primero eliminar wallets "junk" duplicadas por normalización de nombre
    # (ej: 'Stake' vs 'stake', 'stake_', 'ubse_'). Solo deja slugs lowercase válidos.
    try:
        c.execute("""
            DELETE FROM wallet
            WHERE platform GLOB '*[A-Z]*' OR platform LIKE '%\\_' ESCAPE '\\'
        """)
        print(f"  wallets junk (mayúsculas / sufijo _) eliminadas: {c.rowcount}")
    except Exception as _e:
        print(f"  (no se pudieron limpiar wallets junk: {_e})")
    c.execute("""
        UPDATE wallet 
        SET initial_balance = 50.0, 
            balance = 50.0, 
            deposits = 0.0, 
            withdrawals = 0.0, 
            equity = 50.0,
            started_at = ?
    """, (ts,))
    print(f"  wallets actualizadas: {c.rowcount}")
else:
    # IMPORTANTE: al borrar las apuestas NO se debe dejar el balance en su valor
    # previo (que incluía stake comprometido en bets ya borradas) → quedaría
    # inconsistente / negativo. Sin apuestas pendientes, el balance libre debe
    # volver al inicial y equity = balance. Esto evita el bug de wallets en negativo.
    c.execute("""
        UPDATE wallet
        SET balance = initial_balance,
            equity  = initial_balance
    """)
    print(f"  balances re-sincronizados a initial (sin apuestas): {c.rowcount}")

conn.commit()

print("\n=== Tablas DESPUÉS de limpiar ===")
for tbl in all_tables:
    try:
        c.execute(f"SELECT COUNT(*) FROM {tbl}")
        print(f"  {tbl}: {c.fetchone()[0]}")
    except:
        pass

c.execute("VACUUM;")
print("\n✓ VACUUM ejecutado (archivo compactado)")

conn.close()
print("\n✓ Limpieza completada.")
PYEOF

    docker run --rm --user root \
        -v "${DB_VOLUME}:${DB_MOUNT}" \
        -v "${CLEAN_PY}:/tmp/clean_bets.py:ro" \
        -e CLEAN_ALL="${CLEAN_ALL}" \
        -e RESET_BALANCES="${RESET_BALANCES}" \
        kairos:latest \
        python3 /tmp/clean_bets.py || echo "  ⚠ Error durante la limpieza de la DB (revisa volumen/permisos)"

    rm -f "${CLEAN_PY}"

    if [ "$CLEAN_ALL" = true ]; then
        echo "  ✓ Limpieza completa (--clean-all) finalizada"
    elif [ "$RESET_BALANCES" = true ]; then
        echo "  ✓ Limpieza de apuestas + reset de balances (--clean-bets --reset-balances) finalizada"
    else
        echo "  ✓ Limpieza de apuestas (--clean-bets) finalizada"
    fi
fi

# ── 1. Tests ──────────────────────────────────────────────────────
if [ "$SKIP_TESTS" = false ]; then
    step "Corriendo tests (pytest dentro de Docker)…"
    
    # Levantamos un contenedor temporal solo para correr los tests y luego destruirlo (--rm)
    docker compose run --rm kairos bash -c "ls -F && python3 -m pytest tests/ -q --tb=short"
    
    echo "  ✓ Tests OK"
else
    step "Tests — OMITIDOS (--skip-tests)"
fi

# ── 2. Build frontend Vue (DENTRO del contenedor, como usuario kairos) ─────────
if [ "$SKIP_FRONTEND" = false ]; then
    step "Building Vue frontend (dentro del contenedor)…"
    
    # Añadimos --user kairos para evitar el conflicto con /root/.npm
    docker compose run --rm --user kairos kairos bash -c "cd frontend && npm install && npm run build"
    
    echo "  ✓ Frontend compilado dentro del contenedor"
else
    step "Frontend — OMITIDO (--skip-frontend)"
fi

# ── 3. Build imagen Docker ────────────────────────────────────────
if [ "$FAST" = true ]; then
    step "Building Docker image kairos:latest (modo rápido con cache)…"
    docker compose build kairos
else
    step "Building Docker image kairos:latest…"
    if [ "$NO_CACHE" = true ]; then
        docker compose build --no-cache kairos
    else
        docker compose build kairos
    fi
fi

# ── 4. Restart / up ──────────────────────────────────────────────
if [ "$FULL" = true ] && [ "$FAST" = false ]; then
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
