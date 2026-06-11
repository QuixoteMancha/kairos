#!/usr/bin/env python3
"""Probe: ¿exponen Pinnacle/Kambi el score de matches en vivo / terminados?

Verifica empíricamente la hipótesis de Grok ("los bookies no guardan scores").
Busca el endpoint y el campo que contiene el marcador, partiendo del id que ya
guardamos (matchupId Pinnacle, event_id Kambi).
"""
import json

import requests

from src.scrapers.common import SCRAPER_API_HEADERS

PIN = "https://guest.api.arcadia.pinnacle.com/0.1"
KAMBI = "https://eu-offering-api.kambicdn.com/offering/v2018/ubse"


def _get(url, **kw):
    try:
        r = requests.get(url, headers=SCRAPER_API_HEADERS, timeout=15, **kw)
        return r.status_code, r
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def probe_pinnacle():
    print("\n════════ PINNACLE (Guest API) ════════")
    for sid, name in ((19, "ice_hockey"), (29, "basketball"), (4, "football")):
        st, r = _get(f"{PIN}/sports/{sid}/matchups", params={"withSpecials": "false"})
        if st != 200:
            print(f"  {name}: matchups HTTP {st}")
            continue
        ms = r.json()
        live = [m for m in ms if m.get("type") == "matchup"]
        print(f"  {name}: {len(live)} matchups | sample keys: {list(live[0].keys()) if live else '—'}")
        for m in live[:3]:
            mid = m.get("id")
            sk = {k: m.get(k) for k in ("status", "state", "isLive", "live", "score", "scores", "periods") if k in m}
            print(f"    id={mid} {sk}")
            # endpoint de detalle / resultado
            for path in (f"/matchups/{mid}", f"/matchups/{mid}/related"):
                stt, rr = _get(PIN + path)
                body = (rr.text[:160] if hasattr(rr, "text") else str(rr))
                print(f"      GET {path} -> {stt} | {body}")
            break


def probe_kambi():
    print("\n════════ KAMBI (Unibet) ════════")
    # 1) live feed (lo que ya usa cross_book_settle)
    st, r = _get(f"{KAMBI}/event/live/open.json")
    if st == 200:
        data = r.json()
        evs = data.get("liveEvents") or []
        print(f"  live/open.json: {len(evs)} eventos en vivo")
        if evs:
            e = evs[0]
            ev = e.get("event", {})
            ld = e.get("liveData", {})
            print(f"    sample event id={ev.get('id')} name={ev.get('name')!r}")
            print(f"    liveData.score = {ld.get('score')}")
            print(f"    liveData keys = {list(ld.keys())}")
            eid = ev.get("id")
            # 2) endpoint de evento individual (¿sobrevive tras terminar?)
            for path in (f"/event/{eid}.json", f"/event/{eid}/statistics.json"):
                stt, rr = _get(KAMBI + path)
                body = (rr.text[:160] if hasattr(rr, "text") else str(rr))
                print(f"    GET {path} -> {stt} | {body}")
    else:
        print(f"  live/open.json HTTP {st}")

    # 3) endpoint de resultados/result Kambi (si existe)
    for path in ("/event/result.json", "/event/live/results.json"):
        stt, rr = _get(KAMBI + path)
        print(f"  GET {path} -> {stt}")


if __name__ == "__main__":
    probe_pinnacle()
    probe_kambi()
    print("\n[done]")
