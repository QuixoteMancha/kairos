#!/usr/bin/env bash
# kairos.sh — Gestión del stack Docker de Kairos (sin rebuild de imagen).
#
# Uso:
#   ./kairos.sh start      # Arranca todo el stack (o lo reinicia si ya corre)
#   ./kairos.sh stop       # Para todos los containers
#   ./kairos.sh restart    # Reinicia solo kairos
#   ./kairos.sh restart --full   # Reinicia todo el stack
#   ./kairos.sh status     # Estado de los containers
#   ./kairos.sh logs       # Muestra logs en vivo de kairos
#   ./kairos.sh ip         # Actualiza whitelist Brightdata con IP actual
#   ./kairos.sh autopilot  # Prechecks + frontend build (si falta) + docker build/up + healthcheck

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CMD="${1:-status}"
FULL=false
[ "${2:-}" = "--full" ] && FULL=true

_header() {
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  KAIROS · $1"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

_status() {
    echo ""
    docker compose ps
}

_ensure_env_file() {
    if [ -f "$SCRIPT_DIR/.env" ]; then
        return 0
    fi

    if [ -f "$SCRIPT_DIR/.env.example" ]; then
        cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
        echo "  ⚠ .env no existía. Se creó desde .env.example"
        echo "  ⚠ Revisa credenciales en .env antes de operar en real."
        return 0
    fi

    echo "  ✗ Falta .env y .env.example"
    return 1
}

_frontend_build_if_needed() {
    local dist_index="$SCRIPT_DIR/src/web/static/dist/index.html"

    if [ -f "$dist_index" ]; then
        echo "  ✓ Frontend dist ya existe"
        return 0
    fi

    echo "  - Frontend dist no existe. Construyendo frontend..."
    if [ ! -f "$SCRIPT_DIR/frontend/package.json" ]; then
        echo "  ✗ No se encontró frontend/package.json"
        return 1
    fi

    (
        cd "$SCRIPT_DIR/frontend"
        npm install
        npm run build
    )

    if [ ! -f "$dist_index" ]; then
        echo "  ✗ Falló build frontend: no se generó src/web/static/dist/index.html"
        return 1
    fi

    echo "  ✓ Frontend build OK"
}

_wait_health() {
    local health_url="http://localhost:8765/healthz"
    local max_tries=30
    local i

    echo ""
    echo "▶ Esperando healthcheck en ${health_url}..."
    for i in $(seq 1 "$max_tries"); do
        if curl --silent --fail "$health_url" >/dev/null 2>&1; then
            echo "  ✓ Healthcheck OK"
            return 0
        fi
        sleep 2
    done

    echo "  ✗ Healthcheck no respondió a tiempo"
    return 1
}

_autopilot() {
    _header "Autopilot"
    echo ""
    echo "▶ Prechecks de entorno..."
    mkdir -p "$SCRIPT_DIR/data/backups" "$SCRIPT_DIR/logs"
    _ensure_env_file

    echo ""
    echo "▶ Actualizando whitelist Brightdata (best-effort)..."
    _update_ip || true

    echo ""
    echo "▶ Validando frontend compilado..."
    _frontend_build_if_needed

    echo ""
    echo "▶ Build y arranque del stack Docker..."
    docker compose build
    docker compose up -d

    _wait_health

    _status
    echo ""
    echo "  ✓ Autopilot completado"
    echo "  UI: http://localhost:8765"
}

_update_ip() {
    echo ""
    echo "▶ Actualizando whitelist Brightdata…"
    if [ -f "$SCRIPT_DIR/env/bin/activate" ]; then
        # shellcheck disable=SC1091
        source "$SCRIPT_DIR/env/bin/activate"
        python scripts/brightdata_ip_watcher.py
    else
        echo "  ⚠ Virtualenv no encontrado en env/ — asegúrate de activarlo manualmente"
        python3 scripts/brightdata_ip_watcher.py
    fi
}

case "$CMD" in

    start)
        _header "Start"
        echo ""
        echo "▶ Actualizando IP en Brightdata antes de arrancar…"
        _update_ip || true   # No abortar si falla
        echo ""
        echo "▶ Arrancando stack…"
        docker compose up -d
        _status
        echo ""
        echo "  Logs (Ctrl+C para salir):"
        docker compose logs -f --tail=40 kairos
        ;;

    stop)
        _header "Stop"
        echo ""
        echo "▶ Parando stack…"
        docker compose down
        echo "  ✓ Stack detenido"
        ;;

    restart)
        _header "Restart"
        echo ""
        if [ "$FULL" = true ]; then
            echo "▶ Reiniciando stack completo…"
            docker compose down
            docker compose up -d
        else
            echo "▶ Reiniciando solo kairos…"
            docker compose restart kairos
        fi
        _status
        echo ""
        echo "  Logs (Ctrl+C para salir):"
        docker compose logs -f --tail=40 kairos
        ;;

    status)
        _header "Status"
        _status
        ;;

    logs)
        docker compose logs -f --tail=80 kairos
        ;;

    ip)
        _header "Brightdata IP Update"
        _update_ip
        ;;

    autopilot)
        _autopilot
        ;;

    *)
        echo "Uso: $0 {start|stop|restart|status|logs|ip|autopilot} [--full]"
        exit 1
        ;;
esac
