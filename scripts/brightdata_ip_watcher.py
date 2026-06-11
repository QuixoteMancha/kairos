#!/usr/bin/env python3
"""
brightdata_ip_watcher.py
Detecta cambios de IP pública y actualiza automáticamente el whitelist de la
zona Brightdata. Útil para conexiones domésticas con IP dinámica.

Configuración (.env):
    BRIGHTDATA_API_TOKEN=<token>    # Brightdata → Account → API Token
    BRIGHTDATA_ZONE=residential_proxy1  # Nombre de la zona (opcional, auto-detecta desde BRIGHTDATA_PROXY)

Uso:
    python scripts/brightdata_ip_watcher.py          # run una vez
    python scripts/brightdata_ip_watcher.py --watch  # loop cada 5 min
    python scripts/brightdata_ip_watcher.py --watch --interval 10

Instalar como cron (cada 5 min):
    */5 * * * * cd /ruta/kairos && source env/bin/activate && python scripts/brightdata_ip_watcher.py >> logs/ip_watcher.log 2>&1
"""

import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# ── Bootstrap ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

# Importar utilidades compartidas desde el módulo principal
from src.core.brightdata_utils import (  # noqa: E402
    add_ip_to_zone,
    get_public_ip,
    get_zone_from_proxy_url as _get_zone_from_proxy_url,
    get_zone_ips,
    remove_ip_from_zone,
)

# ── Constantes ───────────────────────────────────────────────────────────────
STATE_FILE = ROOT / ".brightdata_last_ip"


def load_last_ip() -> str | None:
    if STATE_FILE.exists():
        try:
            return STATE_FILE.read_text().strip() or None
        except Exception:
            pass
    return None


def save_last_ip(ip: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(ip)


def run_once(api_token: str, zone: str) -> bool:
    """
    Compara IP actual con la última conocida.
    Si cambió, actualiza el whitelist y devuelve True.
    """
    current_ip = get_public_ip()
    last_ip    = load_last_ip()

    if current_ip == last_ip:
        print(f"[BD-WATCHER] IP sin cambios: {current_ip}")
        return False

    print(f"[BD-WATCHER] IP cambió: {last_ip or '(ninguna)'} → {current_ip}")

    # Obtener IPs actuales en la zona
    try:
        zone_ips = get_zone_ips(api_token, zone)
    except Exception as e:
        print(f"[BD-WATCHER] No se pudo leer whitelist: {e}")
        zone_ips = []

    # Eliminar IP anterior si está en el whitelist
    if last_ip and last_ip in zone_ips:
        try:
            remove_ip_from_zone(api_token, zone, last_ip)
            print(f"[BD-WATCHER] ✓ IP anterior eliminada: {last_ip}")
        except Exception as e:
            print(f"[BD-WATCHER] ⚠ No se pudo eliminar IP anterior: {e}")

    # Añadir la IP nueva
    add_ip_to_zone(api_token, zone, current_ip)
    print(f"[BD-WATCHER] ✓ IP nueva añadida al whitelist: {current_ip} (zona={zone})")

    save_last_ip(current_ip)
    return True


def main():
    parser = argparse.ArgumentParser(description="Brightdata dynamic IP whitelist updater")
    parser.add_argument("--watch", action="store_true", help="Loop continuo (cada --interval minutos)")
    parser.add_argument("--interval", type=int, default=5, help="Intervalo en minutos para --watch (default: 5)")
    args = parser.parse_args()

    api_token = os.getenv("BRIGHTDATA_API_TOKEN", "")
    if not api_token:
        print("[BD-WATCHER] ERROR: BRIGHTDATA_API_TOKEN no está definida en .env")
        print("  Encuéntrala en: Brightdata Dashboard → Account → API Token")
        sys.exit(1)

    # Zona: desde variable o auto-detectada desde la URL del proxy
    zone = os.getenv("BRIGHTDATA_ZONE", "")
    if not zone:
        proxy_url = os.getenv("BRIGHTDATA_PROXY", "")
        zone = _get_zone_from_proxy_url(proxy_url) or ""
    if not zone:
        print("[BD-WATCHER] ERROR: No se pudo determinar la zona. Define BRIGHTDATA_ZONE en .env")
        sys.exit(1)

    if not args.watch:
        try:
            run_once(api_token, zone)
        except Exception as e:
            print(f"[BD-WATCHER] ERROR: {e}")
            sys.exit(1)
    else:
        print(f"[BD-WATCHER] Modo watch activo — revisando cada {args.interval} min (zona={zone})")
        while True:
            try:
                run_once(api_token, zone)
            except Exception as e:
                print(f"[BD-WATCHER] ERROR en ciclo: {e}")
            time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
