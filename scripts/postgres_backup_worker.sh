#!/bin/sh
set -eu

interval=${POSTGRES_BACKUP_INTERVAL_SECONDS:-86400}
retention_days=${POSTGRES_BACKUP_RETENTION_DAYS:-14}
backup_dir=${POSTGRES_BACKUP_DIR:-/backups}
verify_restore=${POSTGRES_BACKUP_VERIFY_RESTORE:-true}
verify_marker="$backup_dir/.last_restore_verification"

mkdir -p "$backup_dir"

while true; do
  timestamp=$(date -u +%Y%m%dT%H%M%SZ)
  verify_now=false
  if [ "$verify_restore" = "true" ] && ! find "$verify_marker" -mmin -10080 -print -quit 2>/dev/null | grep -q .; then
    verify_now=true
  fi
  verification_ok=true
  for database_name in "${DB_NAME:-kairos}" "${DB_HISTORICAL_NAME:-kairos_historical}"; do
    target="$backup_dir/${database_name}_${timestamp}.dump"
    temporary="$target.tmp"
    if pg_dump -Fc -h "${DB_HOST:-postgres}" -p "${DB_PORT:-5432}" -U "${DB_USER:-kairos}" -d "$database_name" -f "$temporary" \
      && pg_restore --list "$temporary" >/dev/null; then
      mv "$temporary" "$target"
      echo "[postgres_backup] created $target"
      if [ "$verify_now" = "true" ]; then
        restore_database="kairos_restore_verify_${database_name##*_}_${timestamp}"
        dropdb --if-exists -h "${DB_HOST:-postgres}" -p "${DB_PORT:-5432}" -U "${DB_USER:-kairos}" "$restore_database"
        if createdb -h "${DB_HOST:-postgres}" -p "${DB_PORT:-5432}" -U "${DB_USER:-kairos}" "$restore_database" \
          && pg_restore --exit-on-error --no-owner --no-privileges -h "${DB_HOST:-postgres}" -p "${DB_PORT:-5432}" -U "${DB_USER:-kairos}" -d "$restore_database" "$target" \
          && [ "$(psql -h "${DB_HOST:-postgres}" -p "${DB_PORT:-5432}" -U "${DB_USER:-kairos}" -d "$restore_database" -Atc "select count(*) from pg_catalog.pg_tables where schemaname='public'")" -gt 0 ]; then
          echo "[postgres_backup] restore verified database=$database_name"
        else
          verification_ok=false
          echo "[postgres_backup] restore verification failed database=$database_name" >&2
        fi
        dropdb --if-exists -h "${DB_HOST:-postgres}" -p "${DB_PORT:-5432}" -U "${DB_USER:-kairos}" "$restore_database"
      fi
    else
      rm -f "$temporary"
      echo "[postgres_backup] failed database=$database_name" >&2
    fi
  done
  if [ "$verify_now" = "true" ] && [ "$verification_ok" = "true" ]; then
    touch "$verify_marker"
  fi
  find "$backup_dir" -type f -name '*.dump' -mtime "+$retention_days" -delete
  sleep "$interval"
done
