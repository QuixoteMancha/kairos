#!/usr/bin/env bash
# gmktec_sync.sh
# Sincroniza Kairos hacia una GMKtec por SSH+rsync.
#
# Casos principales:
#   1) Primera carga completa (incluye .env, data, dist, DB):
#      ./scripts/gmktec_sync.sh --full --deploy
#
#   2) Updates incrementales de codigo (omite secretos/datos pesados):
#      ./scripts/gmktec_sync.sh --code --deploy
#
# Requisitos locales: ssh, rsync

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

REMOTE_HOST="PROD_HOST"
REMOTE_USER="sandman"
REMOTE_PORT="22"
REMOTE_DIR="/home/sandman/programas/kairos"
SSH_KEY="${HOME}/.ssh/id_ed25519"
DEPLOY=false
MODE="code"
DEPLOY_MODE="rebuild"
EXTRA_RSYNC_ARGS=()

usage() {
    cat <<'EOF'
Uso: ./scripts/gmktec_sync.sh [opciones]

Modos:
  --full             Primera sincronizacion completa (incluye .env/data/dist/DB)
  --code             Incremental de codigo (omite secretos y estado local)

Opciones:
    --deploy           Ejecuta deploy remoto al terminar
    --deploy-mode <m>  Modo deploy remoto: rebuild | autopilot | restart (default: rebuild)
  --host <ip|host>   Host remoto (default: PROD_HOST)
  --user <user>      Usuario SSH remoto (default: sandman)
  --port <port>      Puerto SSH (default: 22)
  --remote-dir <dir> Directorio remoto (default: /home/sandman/programas/kairos)
    --key <path>       Llave SSH (default: ~/.ssh/id_ed25519)
  --dry-run          Muestra cambios sin transferir
  -h, --help         Mostrar ayuda

Ejemplos:
  ./scripts/gmktec_sync.sh --full --deploy
  ./scripts/gmktec_sync.sh --code --deploy
  ./scripts/gmktec_sync.sh --code --dry-run
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --full) MODE="full" ;;
        --code) MODE="code" ;;
        --deploy) DEPLOY=true ;;
        --deploy-mode) DEPLOY_MODE="${2:-}"; shift ;;
        --host) REMOTE_HOST="${2:-}"; shift ;;
        --user) REMOTE_USER="${2:-}"; shift ;;
        --port) REMOTE_PORT="${2:-}"; shift ;;
        --remote-dir) REMOTE_DIR="${2:-}"; shift ;;
        --key) SSH_KEY="${2:-}"; shift ;;
        --dry-run) EXTRA_RSYNC_ARGS+=("--dry-run") ;;
        -h|--help) usage; exit 0 ;;
        *)
            echo "Argumento desconocido: $1"
            usage
            exit 1
            ;;
    esac
    shift
done

if [[ -z "$REMOTE_HOST" || -z "$REMOTE_USER" || -z "$REMOTE_DIR" ]]; then
    echo "Error: host/user/remote-dir no pueden ser vacios"
    exit 1
fi

if [[ ! -f "$SSH_KEY" ]]; then
    echo "Error: no existe la llave SSH: $SSH_KEY"
    echo "Pasa una llave valida con --key"
    exit 1
fi

SSH_OPTS=(
    -i "$SSH_KEY"
    -p "$REMOTE_PORT"
    -o BatchMode=yes
    -o StrictHostKeyChecking=accept-new
)

RSYNC_BASE=(
    rsync -az --delete
    --human-readable
    --itemize-changes
    --stats
    --exclude ".git/"
    --exclude "env/"
    --exclude ".venv/"
    --exclude "frontend/node_modules/"
    --exclude "__pycache__/"
    --exclude "*.pyc"
    --exclude ".pytest_cache/"
    --exclude ".ruff_cache/"
)

if [[ "$MODE" == "code" ]]; then
    RSYNC_BASE+=(
        --exclude ".env"
        --exclude "data/"
        --exclude "logs/"
        --exclude "src/database/*.db"
        --exclude "src/database/*.db-*"
        --exclude "src/web/static/dist/"
    )
fi

if [[ "$MODE" == "full" ]]; then
    echo "[gmktec_sync] Modo FULL: se incluye .env/data/dist/DB"
else
    echo "[gmktec_sync] Modo CODE: se omiten .env/data/dist/DB/logs"
fi

echo "[gmktec_sync] Verificando acceso SSH..."
ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "echo 'SSH OK: ' \
\"\
\$(hostname)\""

echo "[gmktec_sync] Preparando directorio remoto: ${REMOTE_DIR}"
ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "mkdir -p '${REMOTE_DIR}'"

echo "[gmktec_sync] Sincronizando proyecto..."
"${RSYNC_BASE[@]}" "${EXTRA_RSYNC_ARGS[@]}" \
    -e "ssh -i '${SSH_KEY}' -p '${REMOTE_PORT}' -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
    "$ROOT_DIR/" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"

if [[ "$DEPLOY" == true ]]; then
    echo "[gmktec_sync] Ejecutando deploy remoto (mode=${DEPLOY_MODE})..."
    case "$DEPLOY_MODE" in
        rebuild)
            ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" \
                "cd '${REMOTE_DIR}' && chmod +x kairos.sh rebuild.sh scripts/*.sh && ./rebuild.sh --skip-tests"
            ;;
        autopilot)
            ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" \
                "cd '${REMOTE_DIR}' && chmod +x kairos.sh scripts/*.sh && ./kairos.sh autopilot"
            ;;
        restart)
            ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" \
                "cd '${REMOTE_DIR}' && chmod +x kairos.sh scripts/*.sh && ./kairos.sh restart"
            ;;
        *)
            echo "Error: --deploy-mode invalido: ${DEPLOY_MODE} (usa rebuild|autopilot|restart)"
            exit 1
            ;;
    esac
fi

echo "[gmktec_sync] OK"
