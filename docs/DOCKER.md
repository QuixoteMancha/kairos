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
./kairos.sh autopilot      # flujo automático recomendado (prechecks + build + up + healthcheck)
./rebuild.sh                 # tests + frontend + imagen + restart
./rebuild.sh --skip-tests    # salta pytest
./rebuild.sh --skip-frontend # salta npm build
./rebuild.sh --no-cache      # fuerza reinstalar dependencias
```

## Sync GMKtec sin GitHub

Cuando necesites mover tambien secretos/estado local (por ejemplo `.env`, `data/`, `dist/` o SQLite), usa sync por SSH con rsync.

```bash
# Primera carga completa a GMKtec (incluye .env/data/dist/DB) + deploy remoto
./scripts/gmktec_sync.sh --full --deploy

# Updates incrementales de codigo (omite secretos/estado local) + deploy remoto
./scripts/gmktec_sync.sh --code --deploy

# Seleccionar estrategia de deploy remoto
./scripts/gmktec_sync.sh --code --deploy --deploy-mode rebuild   # default: recompila frontend + rebuild docker
./scripts/gmktec_sync.sh --code --deploy --deploy-mode autopilot # usa kairos.sh autopilot
./scripts/gmktec_sync.sh --code --deploy --deploy-mode restart   # solo restart, sin rebuild

# Watcher continuo (auto-sync + auto-deploy en cada cambio)
./scripts/gmktec_watch.sh --code --interval 4
```

Defaults del destino remoto:
- Host: `PROD_HOST`
- User: `sandman`
- Ruta: `/home/sandman/programas/kairos`

Puedes overridear host/user/ruta/llave con:

```bash
./scripts/gmktec_sync.sh --code --deploy \
    --host PROD_HOST \
    --user sandman \
    --remote-dir /home/sandman/programas/kairos \
    --key ~/.ssh/id_ed25519
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
