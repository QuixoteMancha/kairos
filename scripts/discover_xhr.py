#!/usr/bin/env python3
"""
Descubre endpoints XHR de bookies usando Playwright.

Para cada bookie abre la página de deportes en un navegador headless,
intercepta todas las peticiones de red y filtra las que devuelven JSON
con contenido relevante (odds, eventos, competiciones).

Uso:
    python3 scripts/discover_xhr.py [winamax|betano|all]

Salida: JSON con los endpoints encontrados agrupados por bookie.
"""

import asyncio
import json
import re
import sys
from urllib.parse import urlparse


# ── Config por bookie ─────────────────────────────────────────────────────────

TARGETS = {
    "winamax": {
        "url": "https://www.winamax.es/apuestas-deportivas/sports",
        "wait_ms": 8000,
        "filter_domains": ["winamax.es"],
        "nav_clicks": [],
    },
    "betano": {
        "url": "https://lat.betano.com/sport/futbol/",
        "wait_ms": 8000,
        "filter_domains": ["betano.com"],
        "nav_clicks": [],
    },
}

# Palabras clave en la URL o en el body que sugieren contenido de odds
_ODDS_KEYWORDS = re.compile(
    r'odd|event|match|fixture|sport|competi|leagu|market|champion|game|team|player',
    re.IGNORECASE,
)

# Content-types que nos interesan
_JSON_CONTENT_TYPES = {"application/json", "text/json", "text/javascript"}


# ── Captura ───────────────────────────────────────────────────────────────────

async def capture_xhr(bookie: str, config: dict) -> list[dict]:
    from playwright.async_api import async_playwright

    captured: list[dict] = []
    seen_urls: set[str] = set()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="es-ES",
            timezone_id="Europe/Madrid",
        )
        page = await ctx.new_page()

        ws_frames: list[dict] = []

        async def on_response(response):
            url = response.url
            if url in seen_urls:
                return
            host = urlparse(url).netloc
            if not any(d in host for d in config["filter_domains"]):
                return
            ct = response.headers.get("content-type", "")
            try:
                body_bytes = await response.body()
                body_text = body_bytes.decode("utf-8", errors="replace")
            except Exception:
                body_text = ""
            looks_json = (
                any(t in ct for t in _JSON_CONTENT_TYPES)
                or "json" in url
                or body_text.strip()[:1].startswith(("{", "["))
            )
            # También captura polling de socket.io (body empieza con dígitos + ":[")
            is_socketio_poll = bool(re.match(r'^\d+:\d*\[', body_text.strip()))
            if not looks_json and not is_socketio_poll:
                return
            seen_urls.add(url)
            captured.append({
                "url": url,
                "method": response.request.method,
                "status": response.status,
                "content_type": ct,
                "body_preview": body_text[:800],
            })

        async def on_websocket(ws):
            ws_url = ws.url
            if not any(d in ws_url for d in config["filter_domains"]):
                return
            print(f"[{bookie}] WebSocket conectado: {ws_url}")

            async def on_frame_sent(payload):
                if isinstance(payload, str) and (payload.startswith("4") or "{" in payload):
                    ws_frames.append({"dir": "send", "url": ws_url, "payload": payload[:600]})

            async def on_frame_recv(payload):
                if isinstance(payload, str) and (payload.startswith("4") or "{" in payload):
                    ws_frames.append({"dir": "recv", "url": ws_url, "payload": payload[:600]})

            ws.on("framesent", lambda p: asyncio.ensure_future(
                asyncio.coroutine(lambda: on_frame_sent(p))()
            ) if False else None)

            # Playwright expone on("framesent") y on("framereceived") en WebSocket
            ws.on("framesent", lambda p: ws_frames.append(
                {"dir": "send", "url": ws_url, "payload": str(p)[:600]}
            ) if ("{" in str(p) or str(p).startswith("4")) else None)
            ws.on("framereceived", lambda p: ws_frames.append(
                {"dir": "recv", "url": ws_url, "payload": str(p)[:600]}
            ) if ("{" in str(p) or str(p).startswith("4")) else None)

        page.on("response", on_response)
        page.on("websocket", on_websocket)

        print(f"[{bookie}] Navegando a {config['url']} …", flush=True)
        try:
            await page.goto(config["url"], wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"[{bookie}] Timeout/error en goto: {e} — usando lo capturado hasta ahora")

        await asyncio.sleep(config["wait_ms"] / 1000)

        title = await page.title()
        print(f"[{bookie}] Título: {title!r}  |  WS frames: {len(ws_frames)}  |  HTTP JSON: {len(captured)}")

        # Añadir frames WS únicos al resultado
        seen_ws: set[str] = set()
        for f in ws_frames:
            key = f["payload"][:80]
            if key not in seen_ws:
                seen_ws.add(key)
                captured.append({
                    "url": f["url"],
                    "method": f"WS:{f['dir']}",
                    "status": 0,
                    "content_type": "websocket",
                    "body_preview": f["payload"],
                })

        for selector in config.get("nav_clicks", []):
            try:
                await page.click(selector, timeout=3000)
                await asyncio.sleep(2)
            except Exception:
                pass

        await browser.close()

    return captured


# ── Clasificación ─────────────────────────────────────────────────────────────

def classify(entries: list[dict]) -> dict[str, list[dict]]:
    """Agrupa por tipo inferido de la URL."""
    groups: dict[str, list] = {
        "sports_list": [],
        "competitions": [],
        "events_odds": [],
        "other_json": [],
    }
    for e in entries:
        url = e["url"].lower()
        if any(k in url for k in ("sport", "category", "menu", "tree", "nav")):
            groups["sports_list"].append(e)
        elif any(k in url for k in ("championship", "competi", "league", "tournament", "categor")):
            groups["competitions"].append(e)
        elif any(k in url for k in ("event", "match", "fixture", "game", "odds", "market")):
            groups["events_odds"].append(e)
        else:
            groups["other_json"].append(e)
    return {k: v for k, v in groups.items() if v}


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    targets = TARGETS if arg == "all" else {k: v for k, v in TARGETS.items() if k == arg}

    if not targets:
        print(f"Bookie desconocido: {arg}. Opciones: {list(TARGETS)}")
        sys.exit(1)

    results: dict[str, dict] = {}
    for bookie, config in targets.items():
        raw = await capture_xhr(bookie, config)
        grouped = classify(raw)
        results[bookie] = {
            "total_captured": len(raw),
            "by_type": grouped,
        }
        print(f"\n[{bookie}] {len(raw)} endpoints JSON capturados:")
        for typ, entries in grouped.items():
            print(f"  {typ} ({len(entries)}):")
            for e in entries[:5]:  # máximo 5 por grupo en consola
                print(f"    {e['method']} {e['url']}")
                if e.get("body_preview"):
                    print(f"      preview: {e['body_preview'][:120]!r}")

    # Guardar resultado completo
    out_path = "/tmp/xhr_discovery.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nResultado completo guardado en {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
