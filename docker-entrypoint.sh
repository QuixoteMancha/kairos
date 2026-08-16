#!/bin/sh
# Rescatado sin cambios de la arquitectura pre-"extreme makeover" (borrado
# en el commit 02d7f35) -- sigue aplicando tal cual: garantiza que el
# volumen /data (secretos/compatibilidad) sea escribible por kairos (uid=1000)
# antes de bajar de root a ese usuario.
set -e

chown -R kairos:kairos /data 2>/dev/null || true

exec setpriv --reuid=kairos --regid=kairos --init-groups "$@"
