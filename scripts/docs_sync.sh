#!/usr/bin/env bash
# Daily doc-sync: reviews recent git history + session memory notes and keeps
# docs/*.md and README.md in sync with the actual state of the code.
# Run locally (cron), NOT in the cloud — needs access to the local repo and
# to the local Claude memory notes at ~/.claude/projects/.../memory/.

set -euo pipefail

REPO_DIR="/home/sandman/Programas2/python/projects/kairos"
MEMORY_DIR="/home/sandman/.claude/projects/-home-sandman-Programas2-python-projects-kairos/memory"
LOG_DIR="$REPO_DIR/logs/docs_sync"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y%m%d_%H%M%S).log"

cd "$REPO_DIR"

PROMPT="Estás corriendo como una tarea programada diaria (cron), sin usuario presente para responder preguntas — toma tus propias decisiones razonables y documenta cualquier duda en el propio texto de la documentación en vez de preguntar.

Tarea: mantener actualizada la documentación de Kairos ($REPO_DIR) para que refleje fielmente el estado real del código.

1. Revisa \`git log --oneline -50\` y \`git log -1 -- docs/\` (para saber cuándo se actualizó la documentación por última vez) para entender qué cambió en el código desde la última actualización de docs.
2. Revisa las memorias de sesión en $MEMORY_DIR (archivos con prefijo kairos-*.md, y el índice MEMORY.md) para entender el contexto/motivación detrás de esos cambios — las memorias explican el 'por qué', el git log el 'qué'.
3. Actualiza README.md y los archivos existentes en docs/ (ARCHITECTURE.md, DOCKER.md, betting_settlement_rules.md, strategies_models.md, ANALYTICS.md, AUTH_FLOW.md, POLYMARKET_REAL_TRADING_PLAN.md) — NO reescribas todo desde cero cada vez, actualiza solo las secciones desactualizadas o agrega las que falten. Si una sección describe algo que ya no existe en el código (verifica con grep/Read antes de asumir), corrígela o elimínala.
4. NO toques código fuente (src/, frontend/, tests/, scripts/ salvo este mismo script) — solo archivos de documentación (*.md).
5. Si hiciste cambios, comitea con git usando un mensaje descriptivo (ej. 'docs: sync with recent changes — <resumen breve>'). Si no hay nada que actualizar, no comitees nada y termina sin error.
6. Sé conservador: si no estás seguro de si algo cambió, verifica leyendo el código real antes de escribir en la documentación — no inventes detalles de arquitectura."

claude -p "$PROMPT" \
  --allowedTools "Bash,Read,Write,Edit,Grep,Glob" \
  >> "$LOG_FILE" 2>&1

echo "docs_sync run complete: $LOG_FILE"
