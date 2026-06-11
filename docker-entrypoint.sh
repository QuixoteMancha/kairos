#!/bin/sh
# Garantiza que los directorios de datos y logs sean escribibles por el usuario kairos (uid=1000).
# Este script corre como root (antes de USER kairos) gracias al ENTRYPOINT override en el Dockerfile.
set -e

# Fijar ownership sobre directorios montados como volumen (overwrite el chown del build)
chown -R kairos:kairos /data /app/logs 2>/dev/null || true

# Cambiar a kairos y ejecutar el comando pasado al contenedor
exec setpriv --reuid=kairos --regid=kairos --init-groups "$@"
