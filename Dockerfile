# Rescatado y adaptado de la arquitectura pre-"extreme makeover" (Dockerfile
# borrado en el commit 02d7f35). Cambios respecto al original:
#   - Se quitó todo lo relacionado a Flask/servidor web (EXPOSE 8765,
#     HEALTHCHECK contra /healthz) — este repo reconstruido no tiene
#     servidor web, es un motor de scan (kairos.py) que corre one-shot o
#     en loop vía KAIROS_SCAN_INTERVAL_SECONDS (ver kairos.py).
#   - CMD ahora arranca kairos.py directo, sin subcomandos CLI ("run",
#     "dashboard") que ya no existen.
#   - Se mantiene: build multi-stage (wheels precompiladas), usuario
#     non-root, tini para manejo de señales, DB_PATH configurable + volumen
#     persistente para SQLite (database/db.py).

# ─── Stage 1: builder ──────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# build-essential para wheels que compilan (cffi/curl_cffi, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    libssl-dev \
    libcurl4-openssl-dev \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .

# Wheels precompiladas -> la imagen final solo las instala (rápido).
RUN pip wheel --wheel-dir=/wheels -r requirements.txt


# ─── Stage 2: runtime ──────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DB_PATH=/data/kairos.db \
    KAIROS_HOME=/app

# Libs runtime: curl_cffi, sqlite.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcurl4 \
    libssl3 \
    libsqlite3-0 \
    libglib2.0-0 \
    ca-certificates \
    tini \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

# Usuario non-root.
RUN useradd --create-home --uid 1000 kairos
WORKDIR /app

# Instala wheels precompiladas.
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# Código de la app.
COPY --chown=kairos:kairos . /app

# Volumen para la DB persistente (fuera de la capa de imagen, ver
# database/db.py: DB_PATH default /data/kairos.db).
RUN mkdir -p /data && chown -R kairos:kairos /data /app
VOLUME ["/data"]

# El entrypoint corre como root para fijar permisos en el volumen montado,
# luego baja a usuario kairos para el proceso principal.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# tini gestiona señales; el entrypoint fija permisos y baja a usuario kairos.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]

# Sin KAIROS_SCAN_INTERVAL_SECONDS, kairos.py corre una vez y termina
# (comportamiento por defecto). En producción se setea vía docker-compose
# para correr en loop indefinido.
CMD ["python", "kairos.py"]
