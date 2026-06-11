#!/usr/bin/env bash
# deploy_frontend.sh — Build Vue + deploy limpio al container (sin rebuild imagen Docker).
# Uso rápido cuando sólo cambia código frontend o blueprints Python.
#
#   ./scripts/deploy_frontend.sh            # build + deploy frontend + blueprints
#   ./scripts/deploy_frontend.sh --py-only  # sólo copia .py, sin npm build

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

PY_ONLY=false
for arg in "$@"; do
    [ "$arg" = "--py-only" ] && PY_ONLY=true
done

CONTAINER="kairos"

if [ "$PY_ONLY" = false ]; then
    echo "▶ [1/3] Building Vue frontend…"
    cd frontend && npm run build && cd "$SCRIPT_DIR"
    echo "  ✓ Frontend listo"

    echo "▶ [2/3] Limpiando dist viejo del container (evita archivos zombies)…"
    docker exec "$CONTAINER" rm -rf /app/src/web/static/dist
    docker cp src/web/static/dist "$CONTAINER":/app/src/web/static/
    echo "  ✓ Frontend deploado"
else
    echo "▶ [1/3] Frontend — OMITIDO (--py-only)"
fi

echo "▶ [3/3] Copiando blueprints Python…"
docker cp src/web/blueprints/. "$CONTAINER":/app/src/web/blueprints/
docker cp src/web/dashboard.py "$CONTAINER":/app/src/web/ 2>/dev/null || true
docker cp src/database/database.py "$CONTAINER":/app/src/database/ 2>/dev/null || true
echo "  ✓ Python deploado"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✓ Deploy rápido completo → http://localhost:8765/wallet"
echo "  Nota: si cambiaste routes nuevas, haz: docker restart $CONTAINER"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
