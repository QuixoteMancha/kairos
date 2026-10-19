#!/bin/sh
# Sincroniza este working tree a la mini-pc y despliega el stack completo
# con una sola imagen (ver scripts/deploy_kairos_stack.sh).
#
# La mini-pc construye desde su propio checkout (~/programas/kairos), que no
# sigue git: por eso se sincronizan archivos, sin --delete (archivos que sólo
# existen allá se conservan) y sin tocar .env, datos ni backups.
set -eu

host=${KAIROS_DEPLOY_HOST:-PROD_HOST}
remote_dir=${KAIROS_DEPLOY_DIR:-programas/kairos}
image_tag=${1:-$(date +%Y%m%d)-unified}

cd "$(dirname "$0")/.."

stamp=$(date +%Y%m%d%H%M%S)
ssh "$host" "cd $remote_dir && cp -p docker-compose.yml docker-compose.yml.bak-$stamp && cp -p .env .env.bak-$stamp"

rsync -az \
  --exclude '.git/' --exclude '.env' --exclude '.env.*' --exclude '.claude/' \
  --exclude 'data/' --exclude 'backups/' --exclude 'audit_reports/' \
  --exclude '__pycache__/' --exclude '.venv/' --exclude 'venv/' \
  --exclude '*.db' --exclude '*.bak*' --exclude '.pytest_cache/' \
  ./ "$host:$remote_dir/"

ssh "$host" "cd $remote_dir && sh scripts/deploy_kairos_stack.sh $image_tag"
ssh "$host" "docker ps -a --filter name=kairos --format '{{.Names}} | {{.Image}} | {{.Status}}' | sort"
