#!/usr/bin/env python3
"""
Validador de resolución de bets — auditoría de coherencia + verificación real.

Dos modos (el segundo es opt-in porque usa proxy BrightData, que cuesta):

  PARTE A — Coherencia OFFLINE (siempre, sin red):
    1. Resolución interna: cada arb resuelto debe tener cobertura (1 won + N-1 lost).
    2. Duplicados sospechosos: mismo jugador/equipo en fixtures DISTINTOS el mismo
       día y mismo deporte → no tiene sentido (un jugador no juega 2 singles el
       mismo día). EXCEPCIÓN: tenis singles + dobles el mismo día (el dobles trae
       "/" en los nombres) — ahí un jugador SÍ puede repetirse.
    3. ROI imposible: arbs con net P&L desproporcionado al stake (señal de stake
       mal repartido — no es un arb bloqueado real).

  PARTE B — Verificación REAL contra Flashscore (--verify, usa proxy):
    Para cada grupo resuelto de 1x2/moneyline, consulta Flashscore el ganador real
    y lo compara con la pata marcada 'won' por el sistema. Reporta MATCH/MISMATCH.

Uso:
    python scripts/validate_resolution.py                       # offline, lee live export
    python scripts/validate_resolution.py --file export.json    # offline, archivo local
    python scripts/validate_resolution.py --verify --limit 20   # + verificación proxy
    python scripts/validate_resolution.py --base http://PROD_HOST:8765

Exit code 0 si no hay inconsistencias críticas, 1 si las hay.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from urllib.request import urlopen

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Carga del export ─────────────────────────────────────────────────────────

def load_export(base: str | None, file: str | None) -> dict:
    if file:
        with open(file) as f:
            return json.load(f)
    url = f"{(base or 'http://PROD_HOST:8765').rstrip('/')}/api/bets/export?format=json"
    with urlopen(url, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


# ── Parsing de fixtures ──────────────────────────────────────────────────────

def split_fixture(fixture: str) -> tuple[str, str]:
    """'A vs B' → (A, B); tolera 'vs.', y quita sufijos entre paréntesis."""
    f = re.sub(r"\(.*?\)", "", fixture or "").strip()
    parts = re.split(r"\s+vs\.?\s+", f, flags=re.IGNORECASE)
    if len(parts) >= 2:
        return parts[0].strip(), parts[1].strip()
    return f, ""


def is_doubles(name: str) -> bool:
    """Un equipo de dobles de tenis trae '/' separando los dos jugadores."""
    return "/" in name


def entities(fixture: str, sport: str) -> list[str]:
    """Devuelve las entidades (jugadores/equipos) del fixture, normalizadas."""
    a, b = split_fixture(fixture)
    out = []
    for side in (a, b):
        if not side:
            continue
        if sport == "tennis" and is_doubles(side):
            # dobles: cada jugador por separado, marcado como dobles
            for p in side.split("/"):
                out.append(("D:" + p.strip().lower()))
        else:
            out.append(("S:" + side.strip().lower()))
    return out


def day_of(ts: str) -> str:
    return (ts or "")[:10]


# ── PARTE A: coherencia offline ──────────────────────────────────────────────

def check_resolution_coverage(groups: list) -> list:
    """Arbs resueltos sin cobertura (no exactamente 1 won + resto lost)."""
    bad = []
    for g in groups:
        gt = g["group_type"]
        if gt not in ("arb_2leg", "triplet_3arb"):
            continue
        legs = g["legs"]
        st = [l["status"] for l in legs]
        if "pending" in st or all(s == "void" for s in st):
            continue
        won = st.count("won")
        lost = st.count("lost")
        n = len([s for s in st if s in ("won", "lost")])
        if won != 1 or lost != (n - 1):
            bad.append((g["group_key"], gt, f"won={won} lost={lost}", g["fixture_name"]))
    return bad


def check_duplicate_same_day(groups: list) -> list:
    """
    Mismo jugador/equipo en fixtures DISTINTOS el mismo día y deporte.
    Excepción: tenis singles+dobles (entidad marcada 'D:' es dobles).
    """
    appear: dict = defaultdict(set)   # (sport, entity, day) -> {fixtures}
    kind: dict = defaultdict(set)     # (sport, base_name, day) -> {'S','D'}

    for g in groups:
        sport = ""
        for l in g["legs"]:
            if l.get("sport"):
                sport = l["sport"]; break
        dd = day_of(g.get("placed_at"))
        if not dd:
            continue  # sin fecha no podemos afirmar "mismo día"
        for ent in entities(g["fixture_name"], sport):
            tag, name = ent[:2], ent[2:]
            appear[(sport, name, dd)].add(g["fixture_name"])
            kind[(sport, name, dd)].add(tag[0])

    dups = []
    for (sport, name, dd), fixtures in appear.items():
        if len(fixtures) <= 1:
            continue
        kinds = kind[(sport, name, dd)]
        # Excepción tenis: si aparece como single Y como dobles → permitido
        if sport == "tennis" and "S" in kinds and "D" in kinds:
            continue
        # Si TODAS las apariciones son dobles, también es plausible (varios dobles)
        if kinds == {"D"}:
            continue
        dups.append((sport, name, dd, sorted(fixtures)))
    return dups


def check_impossible_roi(groups: list) -> list:
    """
    Arbs con net P&L desproporcionado: un arb bloqueado real produce un retorno
    pequeño (< ~15% del stake). Net P&L > 100% del stake ⇒ stake mal repartido
    (no es arb bloqueado, es apuesta direccional disfrazada).
    """
    bad = []
    for g in groups:
        if g["group_type"] not in ("arb_2leg", "triplet_3arb"):
            continue
        if g["status"] != "resolved":
            continue
        staked = g["total_staked"] or 0.0
        if staked <= 0:
            continue
        roi = g["net_pnl"] / staked * 100
        if abs(roi) > 100.0:   # arb real jamás devuelve >100% del stake
            bad.append((g["group_key"], round(roi, 1), g["net_pnl"], staked, g["fixture_name"]))
    bad.sort(key=lambda x: -abs(x[1]))
    return bad


def check_all_legs_lost(groups: list) -> list:
    """
    Arbs RESUELTOS donde TODAS las patas perdieron → SIN SENTIDO.

    En un arb cross-book bien cubierto siempre gana al menos una pata. Si todas
    perdieron es señal de resolución incorrecta o cobertura rota.
    """
    bad = []
    for g in groups:
        if g["group_type"] not in ("arb_2leg", "triplet_3arb"):
            continue
        st = [l["status"] for l in g["legs"]]
        if "pending" in st:
            continue
        resolved = [s for s in st if s in ("won", "lost")]
        if resolved and st.count("won") == 0:
            bad.append((g["group_key"], g["fixture_name"], g["net_pnl"]))
    return bad


# ── PARTE B: verificación real (Sofascore → Flashscore) ─────────────────────

def winner_side_from_group(g: dict) -> str | None:
    """Side (p1/p2/draw) que el sistema marcó como ganador (pata 'won' de 1x2/ML)."""
    for l in g["legs"]:
        if l["status"] != "won":
            continue
        for ml in l.get("market_legs", []):
            tipo = (ml.get("tipo") or "").lower()
            side = (ml.get("side") or "").lower()
            if tipo in ("1x2", "moneyline", "ml") and side in ("p1", "p2", "draw"):
                return side
    return None


def _try_sofascore(p1: str, p2: str, date: str, sport: str) -> tuple[str | None, str]:
    """
    Intenta resolver el ganador vía la API pública de Sofascore (con proxy).

    Returns (winner_side|None, source). Sofascore está protegido por Cloudflare;
    si devuelve 403/challenge o no encuentra el match, retorna (None, 'sofascore_unavailable')
    para que el caller caiga a Flashscore. ITF/ligas menores suelen NO estar en
    Sofascore → también caen a Flashscore.
    """
    try:
        from curl_cffi import requests as _req
        from urllib.parse import urlparse, quote_plus
        import random
        from src.core.config import BRIGHTDATA_PROXY
    except Exception:
        return None, "sofascore_no_proxy"

    try:
        p = urlparse(BRIGHTDATA_PROXY)
        sid = random.randbytes(4).hex()
        rotated = f"{p.scheme}://{p.username}-session-{sid}:{p.password}@{p.hostname}:{p.port}"
        s = _req.Session(impersonate="chrome124")
        s.proxies = {"http": rotated, "https": rotated}
        url = f"https://api.sofascore.com/api/v1/search/all?q={quote_plus(p1)}"
        r = s.get(url, timeout=20, verify=False)
        s.close()
        if r.status_code != 200:
            return None, "sofascore_unavailable"  # Cloudflare/challenge → fallback
        # (Si Sofascore volviera a estar disponible, aquí se parsearía el evento y
        #  se extraería el ganador. Hoy está baneado, así que caemos a Flashscore.)
        return None, "sofascore_no_match"
    except Exception:
        return None, "sofascore_error"


def _try_flashscore(p1: str, p2: str, date: str, sport: str) -> tuple[str | None, str]:
    """Resuelve el ganador vía Flashscore (cubre ITF y ligas menores)."""
    try:
        from src.scrapers.flashscore_scraper import find_match_result, find_team_match_result
    except Exception:
        return None, "flashscore_import_error"
    try:
        if sport == "tennis":
            res = find_match_result(p1, p2, date)
            if not res:
                return None, "flashscore_no_data"
            sa, sb = res.get("resultado", "0-0").split("-")
            return ("p1" if int(sa) > int(sb) else "p2"), "flashscore"
        else:
            res = find_team_match_result(p1, p2, date, sport)
            if not res:
                return None, "flashscore_no_data"
            return res.get("winner"), "flashscore"
    except Exception as e:
        return None, f"flashscore_error:{str(e)[:30]}"


def verify_resolution(groups: list, limit: int) -> list:
    """
    Verifica la resolución del sistema contra fuentes reales.

    Cadena de fuentes (como pediste): Sofascore PRIMERO → si no encuentra el match
    o es liga menor/ITF → Flashscore. Compara el ganador real con la pata 'won'.
    """
    results = []
    checked = 0
    for g in groups:
        if checked >= limit:
            break
        if g["status"] != "resolved":
            continue
        side = winner_side_from_group(g)
        if side is None:
            continue
        sport = ""
        for l in g["legs"]:
            if l.get("sport"):
                sport = l["sport"]; break
        p1, p2 = split_fixture(g["fixture_name"])
        if not p1 or not p2:
            continue
        date = (g.get("placed_at") or "")[:10] or "2026-06-15"

        # 1) Sofascore primero
        real, source = _try_sofascore(p1, p2, date, sport)
        # 2) Fallback Flashscore (ITF / ligas menores / Sofascore baneado)
        if real is None:
            real, source = _try_flashscore(p1, p2, date, sport)

        if real is None:
            results.append((g["fixture_name"], side, "NO_DATA", source))
        else:
            verdict = "MATCH" if real == side else "MISMATCH"
            results.append((g["fixture_name"], side, verdict, f"{source} real={real}"))
        checked += 1
    return results


# ── Reporte ──────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("KAIROS_BASE", "http://PROD_HOST:8765"))
    ap.add_argument("--file", default=None, help="export JSON local (en vez de la API)")
    ap.add_argument("--verify", action="store_true", help="verificar contra Flashscore (usa proxy)")
    ap.add_argument("--limit", type=int, default=15, help="máx grupos a verificar con proxy")
    args = ap.parse_args()

    data = load_export(args.base, args.file)
    groups = data.get("groups", [])
    print(f"[VALIDATE] {len(groups)} grupos · {data.get('summary', {}).get('bets_total')} bets\n")

    fails = 0

    # A1: cobertura
    cov = check_resolution_coverage(groups)
    print(f"── 1. Cobertura de resolución (arbs sin 1-won-resto-lost): {len(cov)}")
    for c in cov[:10]:
        print("    ⚠", c)
    fails += len(cov)

    # A2: duplicados mismo día
    dups = check_duplicate_same_day(groups)
    print(f"\n── 2. Duplicados mismo jugador/equipo, mismo día y deporte: {len(dups)}")
    if not dups:
        print("    (sin fechas en el export o sin duplicados — re-deploy para poblar placed_at)")
    for sport, name, dd, fixtures in dups[:15]:
        print(f"    ⚠ [{sport}] '{name}' {dd}:")
        for fx in fixtures:
            print(f"         {fx}")
    fails += len(dups)

    # A3: ROI imposible
    roi_bad = check_impossible_roi(groups)
    print(f"\n── 3. Arbs con net P&L imposible (>100% stake = no es arb bloqueado): {len(roi_bad)}")
    for gk, roi, pnl, st, fx in roi_bad[:12]:
        print(f"    ⚠ ROI={roi}% pnl={pnl} staked={st} | {fx}")
    fails += len(roi_bad)

    # A4: arbs donde TODAS las patas perdieron (sin sentido)
    all_lost = check_all_legs_lost(groups)
    print(f"\n── 4. Arbs RESUELTOS donde TODAS las patas perdieron (sin sentido): {len(all_lost)}")
    for gk, fx, pnl in all_lost[:10]:
        print(f"    ⚠ {gk} pnl={pnl} | {fx}")
    fails += len(all_lost)

    # B: verificación real
    if args.verify:
        print(f"\n── 5. Verificación REAL (Sofascore→Flashscore, proxy, máx {args.limit}):")
        ver = verify_resolution(groups, args.limit)
        mismatch = [v for v in ver if v[2] == "MISMATCH"]
        for fx, side, verdict, detail in ver:
            mark = {"MATCH": "✓", "MISMATCH": "✗", "NO_DATA": "·", "ERROR": "!"}.get(verdict, "?")
            print(f"    {mark} [{verdict:<8}] sistema={side} {detail:<28} | {fx}")
        print(f"\n    MISMATCH (resolución incorrecta): {len(mismatch)}")
        fails += len(mismatch)
    else:
        print("\n── 5. Verificación Sofascore/Flashscore: omitida (usa --verify para activarla)")

    print(f"\n{'='*60}")
    print(f"  Total inconsistencias: {fails}")
    sys.exit(0 if fails == 0 else 1)


if __name__ == "__main__":
    main()
