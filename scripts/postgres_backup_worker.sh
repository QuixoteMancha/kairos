#!/bin/sh
set -eu

interval=${POSTGRES_BACKUP_INTERVAL_SECONDS:-86400}
retention_days=${POSTGRES_BACKUP_RETENTION_DAYS:-14}
backup_dir=${POSTGRES_BACKUP_DIR:-/backups}

mkdir -p "$backup_dir"

while true; do
  timestamp=$(date -u +%Y%m%dT%H%M%SZ)
  for database_name in "${DB_NAME:-kairos}" "${DB_HISTORICAL_NAME:-kairos_historical}"; do
    target="$backup_dir/${database_name}_${timestamp}.dump"
    temporary="$target.tmp"
    if pg_dump -Fc -h "${DB_HOST:-postgres}" -p "${DB_PORT:-5432}" -U "${DB_USER:-kairos}" -d "$database_name" -f "$temporary" \
      && pg_restore --list "$temporary" >/dev/null; then
      mv "$temporary" "$target"
      echo "[postgres_backup] created $target"
    else
      rm -f "$temporary"
      echo "[postgres_backup] failed database=$database_name" >&2
    fi
  done
  find "$backup_dir" -type f -name '*.dump' -mtime "+$retention_days" -delete
  sleep "$interval"
done
