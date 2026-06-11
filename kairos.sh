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

    *)
        echo "Uso: $0 {start|stop|restart|status|logs|ip} [--full]"
        exit 1
        ;;
esac
