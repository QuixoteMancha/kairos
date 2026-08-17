#!/bin/sh
# Despliegue reproducible: una sola imagen inmutable para todos los servicios.
set -eu

if [ "$#" -lt 2 ]; then
  echo "uso: $0 <image-tag> <service> [service ...]" >&2
  echo "ejemplo: $0 20260816-data tennis_weather_worker tennis_data_gap_filler_worker" >&2
  exit 2
fi

image_tag=$1
shift

case "$image_tag" in
  *[!A-Za-z0-9_.-]*|'')
    echo "image-tag inválido: $image_tag" >&2
    exit 2
    ;;
esac

docker build -t "kairos:$image_tag" .
KAIROS_IMAGE_TAG=$image_tag docker compose run --rm kairos_migrate
KAIROS_IMAGE_TAG=$image_tag docker compose up -d --no-build --no-deps "$@"

echo "desplegado kairos:$image_tag en: $*"
