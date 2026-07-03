# ─── Stage 1: builder ──────────────────────────────────────────────────────
# Compila las wheels para tener una imagen final pequeña sin toolchain.
FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# build-essential para wheels que compilan (cffi, curl_cffi, scipy, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    libssl-dev \
    libcurl4-openssl-dev \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .

# Wheels precompiladas → la imagen final solo las instala (rápido).
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
    libsecret-1-0 \
    libglib2.0-0 \
    ca-certificates \
    curl \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Usuario non-root para seguridad.
RUN useradd --create-home --uid 1000 kairos
WORKDIR /app

# Instala wheels precompiladas
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# Código de la app
COPY --chown=kairos:kairos . /app

# Volumen para la DB persistente
RUN mkdir -p /data /app/logs && chown -R kairos:kairos /data /app
VOLUME ["/data"]

# El entrypoint corre como root para fijar permisos en volúmenes montados,
# luego delega a su-exec → kairos para el proceso principal.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8765

# Healthcheck nativo de Docker → reinicia el contenedor si /healthz falla.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent http://localhost:8765/healthz || exit 1

# tini gestiona señales; el entrypoint fija permisos y baja a usuario kairos.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]

# Por defecto arranca el "full system" automático:
#   - KairosEngine (el core/motor): corre solo con scans, auto-bets,
#     live hedge, settlement, rebalance, etc.
#   - Servidor web (Flask + Vue): capa de visualización (balances, arbs,
#     historial...). No es el cerebro, es para ver la data.
#
# Comando recomendado: "run". "dashboard" se mantiene por compatibilidad.
CMD ["python", "kairos.py", "run", "--host", "0.0.0.0", "--port", "8765"]
