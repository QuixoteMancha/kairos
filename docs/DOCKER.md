# Kairos · Docker stack

App completa en contenedor único, lista con un solo comando.

## Servicios

| Servicio | Puerto host | Descripción |
|----------|-------------|-------------|
| `kairos` | `8765` | Dashboard Flask + worker + live-monitor |

## Volúmenes persistentes

| Volumen | Propósito |
|---------|-----------|
| `kairos_data` | SQLite DB (`/data/kairos.db` dentro del contenedor) |
| `./logs/` (bind mount) | Logs accesibles desde el host |

## Primera vez

```bash
# 1. Configuración local
cp .env.example .env
$EDITOR .env             # ajustá BRIGHTDATA_PROXY, TZ, etc.

# 2. (opcional) migrá tu DB actual al volumen
mkdir -p ./logs
docker volume create kairos_data
docker run --rm -v kairos_data:/data -v "$(pwd)/src/database":/src alpine \
    sh -c "cp /src/kairos.db /data/kairos.db && chown 1000:1000 /data/kairos.db"

# 3. Build + arranque
docker compose build
docker compose up -d

# 4. Verificación
curl http://localhost:8765/healthz       # → {"status":"ok"}
curl http://localhost:8765/readyz        # → detalle de componentes
```

## Operaciones comunes

```bash
docker compose logs -f kairos            # logs en vivo
docker compose ps                        # estado
docker compose restart kairos            # recarga app (sin tocar DB)
docker compose down                      # baja todo, mantiene volúmenes
docker compose down -v                   # baja todo + BORRA volúmenes (peligroso)

# Backup manual
docker compose exec kairos python scripts/backup_db.py

# Migraciones
docker compose exec kairos python scripts/migrate.py
```

## Rebuild rápido

```bash
./rebuild.sh                 # tests + frontend + imagen + restart
./rebuild.sh --skip-tests    # salta pytest
./rebuild.sh --skip-frontend # salta npm build
./rebuild.sh --no-cache      # fuerza reinstalar dependencias
```

## Healthcheck

Docker reinicia el contenedor automáticamente si `/healthz` falla 3 veces seguidas.
Configurado a `interval=30s, timeout=5s, retries=3`.

## Producción (VPS)

1. Apuntá un dominio (ej. `kairos.tudominio.com`) al VPS.
2. Añadí un reverse-proxy con HTTPS automático (Caddy/Traefik). Ejemplo Caddy:

   ```caddy
   kairos.tudominio.com {
       reverse_proxy kairos:8765
   }
   ```

## Build details

`Dockerfile` es **multi-stage**:
- **Stage 1 (builder)**: compila wheels con `build-essential`.
- **Stage 2 (runtime)**: solo libs runtime, usuario non-root `kairos:1000`,
  init con `tini` (señales correctas), imagen final ~250 MB.


Playwright **no** está incluido (Kairos no lo usa en runtime, solo scrapers
desactivados). Si reactivás algún scraper Playwright, añadí al Dockerfile:

```dockerfile
RUN apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk-bridge2.0-0 libgtk-3-0 libxss1 libasound2 \
    && python -m playwright install chromium
```
