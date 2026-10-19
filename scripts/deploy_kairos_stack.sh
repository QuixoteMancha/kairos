#!/bin/sh
# Despliegue reproducible: una sola imagen inmutable para todos los servicios.
#
# Sin lista de servicios despliega TODOS los que usan la imagen kairos:*.
# 2026-10-18: desplegar servicios sueltos dejó 9 imágenes distintas en prod;
# como cada imagen aplica sus migraciones al arrancar y el runner rechaza
# migraciones desconocidas, tras un reboot 11 workers con imágenes viejas
# quedaron en crash-loop (~1500 reinicios). Por eso el default es el stack
# completo y el tag queda persistido en .env para el próximo `compose up`.
set -eu

if [ "$#" -lt 1 ]; then
  echo "uso: $0 <image-tag> [service ...]" >&2
  echo "ejemplo: $0 20261018-unified            # stack completo (recomendado)" >&2
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

if [ "$#" -eq 0 ]; then
  # Servicios cuya imagen es kairos:* (excluye postgres, backup y el
  # one-shot kairos_migrate, que se corre aparte abajo).
  set -- $(KAIROS_IMAGE_TAG=$image_tag docker compose config --format json \
    | python3 -c 'import json,sys
for name, svc in json.load(sys.stdin)["services"].items():
    if svc.get("image", "").startswith("kairos:") and name != "kairos_migrate":
        print(name)')
fi

docker build -t "kairos:$image_tag" .
KAIROS_IMAGE_TAG=$image_tag docker compose run --rm kairos_migrate
KAIROS_IMAGE_TAG=$image_tag docker compose up -d --no-build --no-deps "$@"

if grep -q '^KAIROS_IMAGE_TAG=' .env; then
  sed -i "s/^KAIROS_IMAGE_TAG=.*/KAIROS_IMAGE_TAG=$image_tag/" .env
else
  echo "KAIROS_IMAGE_TAG=$image_tag" >> .env
fi

echo "desplegado kairos:$image_tag en: $*"
