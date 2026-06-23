#!/usr/bin/env bash
# gmktec_watch.sh
# Watcher simple para auto-sincronizar Kairos hacia la GMKtec.
# Usa inotifywait si esta disponible; si no, hace polling cada N segundos.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$ROOT_DIR"

INTERVAL=4
DEPLOY=true
SYNC_MODE="code"
EXTRA_ARGS=()

usage() {
    cat <<'EOF'
Uso: ./scripts/gmktec_watch.sh [opciones]

Opciones:
  --interval <seg>   Intervalo de polling fallback (default: 4)
  --full             Watch en modo full
  --code             Watch en modo code (default)
  --no-deploy        No ejecutar autopilot remoto tras cada sync
  --deploy           Ejecutar autopilot remoto tras cada sync (default)
  --                 Todo lo que siga se pasa a gmktec_sync.sh

Ejemplos:
  ./scripts/gmktec_watch.sh --code --interval 3
  ./scripts/gmktec_watch.sh --full -- --host PROD_HOST --user sandman
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --interval) INTERVAL="${2:-}"; shift ;;
        --full) SYNC_MODE="full" ;;
        --code) SYNC_MODE="code" ;;
        --no-deploy) DEPLOY=false ;;
        --deploy) DEPLOY=true ;;
        --) shift; EXTRA_ARGS+=("$@"); break ;;
        -h|--help) usage; exit 0 ;;
        *) EXTRA_ARGS+=("$1") ;;
    esac
    shift
done

if ! [[ "$INTERVAL" =~ ^[0-9]+$ ]]; then
    echo "Error: --interval debe ser entero"
    exit 1
fi

sync_once() {
    local args=("--${SYNC_MODE}")
    if [[ "$DEPLOY" == true ]]; then
        args+=("--deploy")
    fi
    args+=("${EXTRA_ARGS[@]}")

    echo "[gmktec_watch] Sync: ${args[*]}"
    "${SCRIPT_DIR}/gmktec_sync.sh" "${args[@]}" || {
        echo "[gmktec_watch] Sync fallo. Reintentando en ${INTERVAL}s..."
        return 1
    }
    return 0
}

echo "[gmktec_watch] Iniciando watcher en ${ROOT_DIR}"
echo "[gmktec_watch] Modo=${SYNC_MODE} deploy=${DEPLOY} interval=${INTERVAL}s"

echo "[gmktec_watch] Sync inicial..."
sync_once || true

if command -v inotifywait >/dev/null 2>&1; then
    echo "[gmktec_watch] Usando inotifywait"
    inotifywait -m -r \
        -e modify,create,delete,move \
        --exclude '(^|/)(\.git|env|\.venv|frontend/node_modules|__pycache__|\.pytest_cache|\.ruff_cache)(/|$)' \
        "$ROOT_DIR" | while read -r _dir _evt _file; do
            # debounce simple
            sleep 1
            sync_once || true
        done
else
    echo "[gmktec_watch] inotifywait no encontrado, usando polling"
    while true; do
        sleep "$INTERVAL"
        sync_once || true
    done
fi
