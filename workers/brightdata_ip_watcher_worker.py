"""Worker periódico: mantiene la IP pública de este contenedor en el
whitelist de la zona residencial de BrightData.

CONTEXTO (rescatado y adaptado 2026-07-30, ver memoria
kairos-brightdata-account-invalid-status-2026-07-30 y commit pre-rebuild
`4c2ccd7`): el sistema pre-"extreme makeover" tenía este mismo mecanismo
como worker en-proceso dentro del engine monolítico; en este rebuild cada
worker es su propio contenedor Docker (ver docker-compose.yml), así que
esto se adapta a ese patrón en vez de al hilo periódico viejo.

Por qué existe además de `request_via_proxy_with_ip_retry` (reactivo, en
`outer_apis/scrapers/brightdata_proxy.py`, ya wireado en
flashscore_scraper.py): ese mecanismo solo actualiza el whitelist DESPUÉS
de que una request real ya falló con 407 -- este worker lo hace
proactivamente cada `_INTERVAL_S`, así una IP residencial que cambia (o
un contenedor recién levantado con una IP de salida distinta) nunca
llega a romper el primer scrape.

IMPORTANTE (hallazgo real 2026-07-30): un 407 persistente puede NO ser un
problema de whitelist en absoluto -- verificado en producción que la
cuenta de BrightData devolvía "Customer has invalid status" (422) en
`GET /zone`, un problema de estado de cuenta/facturación ajeno a este
worker. Este worker resuelve el caso "la IP cambió", NO un problema de
cuenta -- si seguís viendo 407 con este worker corriendo, revisar el
dashboard de BrightData directamente, no asumir que es un bug de código."""

import os
import time

from outer_apis.scrapers.brightdata_proxy import (
    ensure_ip_whitelisted, get_brightdata_config,
)

_REFRESH_INTERVAL_S = os.getenv("BRIGHTDATA_WATCHER_INTERVAL_SECONDS")
_DEFAULT_INTERVAL_S = 300  # 5 min -- mismo intervalo que el script/worker pre-rebuild recomendaba


def _run_once() -> bool:
    config = get_brightdata_config()
    if not config:
        print("[brightdata_ip_watcher] BRIGHTDATA_PROXY no configurado -- nada que hacer")
        return False
    if not config.get("api_token") or not config.get("zone"):
        print("[brightdata_ip_watcher] falta BRIGHTDATA_API_TOKEN o no se pudo resolver la zona -- nada que hacer")
        return False

    added = ensure_ip_whitelisted(config["api_token"], config["zone"], verbose=True)
    if added:
        print(f"[brightdata_ip_watcher] IP pública actualizada en el whitelist (zona={config['zone']})")
    return added


if __name__ == "__main__":
    if _REFRESH_INTERVAL_S:
        interval = int(_REFRESH_INTERVAL_S) if _REFRESH_INTERVAL_S.strip() else _DEFAULT_INTERVAL_S
        print(f"[brightdata_ip_watcher] loop mode, refresh_interval={interval}s")
        while True:
            try:
                _run_once()
            except Exception as e:
                print(f"[brightdata_ip_watcher] error: {e}")
            time.sleep(interval)
    else:
        _run_once()
