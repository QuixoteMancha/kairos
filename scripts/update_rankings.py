#!/usr/bin/env python3
"""
update_rankings.py
══════════════════
Actualiza Player.ranking desde fuentes oficiales ATP/WTA/ITF.

Estrategia anti-spam:
  1. Descarga rankings en BULK (live-tennis.eu): 4 requests en total para
     obtener ATP top-1000, WTA top-1000, ITF masc top-300, ITF fem top-300.
  2. Para cada jugador en DB hace un lookup LOCAL contra el dict bulk.
     Si no se encuentra en bulk, hace una búsqueda individual por HTTP
     (limitado a un máximo de `--max-individual` jugadores).
  3. Entre búsquedas individuales: delay base + jitter aleatorio.
  4. Pausa extra y más larga cada `--batch-size` búsquedas individuales.

Requests totales esperados:
  Bulk  : ~4 (uno por fuente)
  Fallback puntual: ≤ max-individual (default 50), con delay 2-5s cada uno

Uso:
  python update_rankings.py                          # seco (no escribe DB)
  python update_rankings.py --apply                  # aplica cambios a DB
  python update_rankings.py --apply --force          # omite throttle diario
  python update_rankings.py --apply --top 200        # solo jugadores rank ≤ 200
  python update_rankings.py --apply --type ATP       # solo ATP
  python update_rankings.py --apply --type WTA       # solo WTA
  python update_rankings.py --apply --delay 3.0      # delay base (default 2.5s)
  python update_rankings.py --apply --batch-size 30  # pausa cada 30 individuales
  python update_rankings.py --apply --max-individual 0  # solo bulk, sin fallback HTTP
"""

import argparse
import difflib
import os
import random
import re
import sys
import time
import unicodedata
from datetime import date

# ── bootstrap ─────────────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

import requests
from bs4 import BeautifulSoup

from src.database.database import (
    AppConfig,
    Player,
    RankingHistory,
    db,
    init_db,
)

# ── constantes ────────────────────────────────────────────────────────────────
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_HEADERS     = {"User-Agent": _UA, "Accept": "text/html,*/*", "Referer": "https://www.google.com/"}
_JSON_HDR    = {**_HEADERS, "Accept": "application/json, */*"}

_LT_ATP      = "http://live-tennis.eu/en/atp-live-ranking"
_LT_WTA      = "http://live-tennis.eu/en/wta-live-ranking"
_LT_ITF_M    = "http://live-tennis.eu/en/itf-men-live-ranking"
_LT_ITF_F    = "http://live-tennis.eu/en/itf-women-live-ranking"
_ATP_AJAX    = "https://www.atptour.com/en/-/ajax/playersearch/{q}"
_WTA_SEARCH  = "https://api.wtatennis.com/tennis/players?name={q}"

# Mapa: (nombre_normalizado) → (ranking, tipo, nombre_oficial)
BulkMap = dict[str, tuple[int, str, str]]

# ── helpers ───────────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 20, headers: dict = None,
         params: dict = None) -> requests.Response | None:
    try:
        r = requests.get(url, headers=headers or _HEADERS,
                         timeout=timeout, allow_redirects=True, params=params)
        if r.status_code == 200:
            return r
        print(f"    [HTTP {r.status_code}] {url[:70]}")
        return None
    except Exception as exc:
        print(f"    [ERR] {exc.__class__.__name__}: {exc} — {url[:60]}")
        return None


def _norm(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_ = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]", " ", ascii_.lower()).strip()


def _fuzzy(target: str, candidates: list[str], cutoff: float = 0.75) -> str | None:
    t = _norm(target)
    normed = {_norm(c): c for c in candidates}
    hits = difflib.get_close_matches(t, list(normed), n=1, cutoff=cutoff)
    return normed[hits[0]] if hits else None


def _jitter(base: float, lo: float = 0.5, hi: float = 1.5) -> float:
    """Devuelve base + ruido aleatorio proporcional."""
    return base + random.uniform(base * lo * 0.3, base * hi * 0.5)


# ══════════════════════════════════════════════════════════════════════════════
#  BULK FETCH (live-tennis.eu)
# ══════════════════════════════════════════════════════════════════════════════

def _parse_lt_table(html: str, tipo: str, top_n: int = 2000) -> BulkMap:
    """
    Parsea tabla live-tennis.eu.
    Estructura: [rank, tier, img, nombre, age, country, points, ...]
    Retorna {nombre_norm: (rank, tipo, nombre_original)}.
    """
    result: BulkMap = {}
    soup = BeautifulSoup(html, "html.parser")
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        rank_txt = cells[0].get_text(strip=True).replace(",", "").strip()
        if not re.match(r"^\d+$", rank_txt):
            continue
        rank = int(rank_txt)
        if rank > top_n:
            continue
        nombre = cells[3].get_text(strip=True)
        if nombre:
            result[_norm(nombre)] = (rank, tipo, nombre)
    return result


def fetch_bulk(top_n: int = 2000, delay_between: float = 2.0) -> BulkMap:
    """
    Descarga rankings en bulk con delays corteses entre requests.
    4 fuentes → 4 requests HTTP en total.
    """
    combined: BulkMap = {}
    sources = [
        (_LT_ATP,   "ATP"),
        (_LT_WTA,   "WTA"),
        (_LT_ITF_M, "ITF"),
        (_LT_ITF_F, "ITF"),
    ]
    for i, (url, tipo) in enumerate(sources):
        print(f"  [{i+1}/4] Descargando {tipo} ({url.split('/')[-1]})...", end=" ", flush=True)
        if i > 0:
            wait = _jitter(delay_between, 0.3, 0.8)
            time.sleep(wait)
        resp = _get(url)
        if resp:
            data = _parse_lt_table(resp.text, tipo, top_n)
            # No sobreescribir ATP/WTA con ITF si ya existe
            added = 0
            for k, v in data.items():
                if k not in combined or combined[k][1] == "ITF":
                    combined[k] = v
                    added += 1
            print(f"{added} jugadores")
        else:
            print("FALLO")

    print(f"  Total bulk: {len(combined)} entradas\n")
    return combined


# ══════════════════════════════════════════════════════════════════════════════
#  LOOKUP LOCAL
# ══════════════════════════════════════════════════════════════════════════════

def lookup_player(nombre: str, bulk: BulkMap,
                  prefer_type: str = "auto") -> dict | None:
    """
    Busca un jugador en el dict bulk con matching directo + fuzzy.
    Retorna None si no se encuentra.
    """
    n = _norm(nombre)
    # Directo
    if n in bulk:
        rank, tipo, nombre_oficial = bulk[n]
        return {"ranking": rank, "ranking_type": tipo, "nombre_oficial": nombre_oficial,
                "_source": "bulk_direct"}
    # Fuzzy
    match_key = _fuzzy(nombre, [k for k in bulk], cutoff=0.78)
    if match_key:
        rank, tipo, nombre_oficial = bulk[match_key]
        return {"ranking": rank, "ranking_type": tipo, "nombre_oficial": nombre_oficial,
                "_source": "bulk_fuzzy"}
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  FALLBACK INDIVIDUAL (solo para misses del bulk)
# ══════════════════════════════════════════════════════════════════════════════

def _individual_atp(nombre: str) -> dict | None:
    """Búsqueda puntual AJAX en ATP (liviana, no descarga rankings completos)."""
    url = _ATP_AJAX.format(q=requests.utils.quote(nombre))
    resp = _get(url, headers=_JSON_HDR)
    if not resp:
        return None
    try:
        data = resp.json()
        players = data if isinstance(data, list) else data.get("players", [])
        if not players:
            return None
        names = [p.get("FullName", p.get("name", "")) for p in players]
        hit = _fuzzy(nombre, names, cutoff=0.75)
        if not hit:
            return None
        p = next(p for p in players
                 if _norm(p.get("FullName", p.get("name", ""))) == _norm(hit))
        rank_raw = p.get("Ranking", p.get("ranking", ""))
        rank = int(re.search(r"\d+", str(rank_raw)).group()) if re.search(r"\d+", str(rank_raw)) else None
        return {"ranking": rank, "ranking_type": "ATP", "_source": "atp_ajax"}
    except Exception:
        return None


def _individual_wta(nombre: str) -> dict | None:
    """Búsqueda puntual WTA API."""
    parts = nombre.strip().split()
    last  = parts[-1] if parts else nombre
    url   = _WTA_SEARCH.format(q=requests.utils.quote(last))
    resp  = _get(url, headers=_JSON_HDR)
    if not resp:
        return None
    try:
        data  = resp.json()
        cands = data.get("content", [])
        if not cands:
            return None
        best, best_s = None, 0.0
        n_target = _norm(nombre)
        for c in cands:
            s = difflib.SequenceMatcher(None, n_target, _norm(c.get("fullName", ""))).ratio()
            if s > best_s:
                best_s, best = s, c
        if best and best_s >= 0.72:
            return {"ranking": None, "ranking_type": "WTA",
                    "_wta_id": best["id"], "_source": "wta_api"}
    except Exception:
        pass
    return None


def individual_fallback(nombre: str, ranking_type: str,
                        delay: float = 2.5) -> dict | None:
    """Intenta lookup puntual con delay + jitter."""
    time.sleep(_jitter(delay))
    if ranking_type == "WTA":
        return _individual_wta(nombre)
    return _individual_atp(nombre) or _individual_wta(nombre)


# ══════════════════════════════════════════════════════════════════════════════
#  PIPELINE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def run(
    apply: bool,
    force: bool,
    top_n: int | None,
    type_filter: str | None,
    delay: float,
    batch_size: int,
    max_individual: int,
):
    today = date.today().isoformat()

    # ── Throttle ──────────────────────────────────────────────────────────────
    cfg_key = f"ranking_update:{today}"
    if not force and not apply:
        pass  # DRY-RUN siempre corre
    elif not force and AppConfig.get_value(cfg_key, "0") == "1":
        print(f"[THROTTLE] Pipeline ya corrió hoy ({today}). Usa --force para re-ejecutar.")
        return

    # ── Selección jugadores ───────────────────────────────────────────────────
    query = Player.select().where(Player.ranking.is_null(False), Player.ranking > 0)
    if type_filter:
        query = query.where(Player.ranking_type == type_filter.upper())
    if top_n:
        query = query.where(Player.ranking <= top_n)
    players = list(query.order_by(Player.ranking))
    total = len(players)
    print(f"Jugadores a procesar: {total}")
    if total == 0:
        print("Nada que hacer.")
        return

    # ── Bulk fetch ────────────────────────────────────────────────────────────
    print("Descargando rankings bulk (4 requests)...")
    bulk = fetch_bulk(top_n=2000, delay_between=2.5)

    # ── Estadísticas ──────────────────────────────────────────────────────────
    ok = unchanged = updated = not_found_bulk = individual_ok = individual_fail = 0
    type_fixed = 0
    individual_done = 0
    changes: list[dict] = []

    print(f"Procesando {total} jugadores (bulk lookup + fallback individual limitado)...\n")

    for idx, player in enumerate(players):
        nombre = player.nombre
        rt     = (player.ranking_type or "ATP").upper()

        # 1. Bulk lookup
        data = lookup_player(nombre, bulk, prefer_type=rt)

        # 2. Fallback individual (limitado, con delay)
        if data is None:
            not_found_bulk += 1
            if individual_done < max_individual:
                data = individual_fallback(nombre, rt, delay=delay)
                individual_done += 1
                # Pausa larga cada batch_size individuales
                if individual_done % batch_size == 0:
                    long_pause = delay * 5 + random.uniform(2, 6)
                    print(f"  ⏸  Pausa larga ({long_pause:.1f}s) tras {individual_done} individuales...")
                    time.sleep(long_pause)
                if data:
                    individual_ok += 1
                else:
                    individual_fail += 1
            else:
                individual_fail += 1   # no se intentó por límite

        if data is None:
            # Sin datos — registrar pero no cambiar DB
            changes.append({
                "nombre": nombre, "db_rank": player.ranking, "db_type": rt,
                "new_rank": None, "new_type": None, "accion": "no_encontrado",
            })
            continue

        new_rank = data.get("ranking")
        new_type = data.get("ranking_type", rt)

        # Determinar si hay cambio real
        rank_changed = new_rank and new_rank != player.ranking
        type_changed = new_type != rt

        if not rank_changed and not type_changed:
            unchanged += 1
            continue

        accion = []
        if rank_changed:
            accion.append(f"rank {player.ranking}→{new_rank}")
        if type_changed:
            accion.append(f"tipo {rt}→{new_type}")
            type_fixed += 1

        changes.append({
            "nombre":   nombre,
            "db_rank":  player.ranking,
            "db_type":  rt,
            "new_rank": new_rank,
            "new_type": new_type,
            "accion":   ", ".join(accion),
            "_source":  data.get("_source", "?"),
        })

        if rank_changed:
            ok += 1
        updated += 1

        # Aplicar a DB (con retry para database is locked)
        if apply:
            for _attempt in range(5):
                try:
                    with db.atomic():
                        if rank_changed:
                            player.ranking      = new_rank
                        if type_changed:
                            player.ranking_type = new_type
                        player.save()
                        RankingHistory.get_or_create(
                            player=player,
                            fecha=today,
                            defaults={
                                "ranking":      new_rank or player.ranking,
                                "elo_points":   0,
                                "ranking_type": new_type,
                            },
                        )
                    time.sleep(0.005)   # micro-pausa para no saturar el WAL
                    break   # éxito
                except Exception as _dbe:
                    if "locked" in str(_dbe).lower() and _attempt < 4:
                        time.sleep(0.5 + _attempt * 0.5)
                    else:
                        print(f"    [DB-ERR] {player.nombre}: {_dbe}")
                        break

        # Progreso cada 100 jugadores
        if (idx + 1) % 100 == 0 or (idx + 1) == total:
            pct = (idx + 1) / total * 100
            print(f"  [{idx+1:>4}/{total}  {pct:>5.1f}%]  cambiados={updated}  "
                  f"sin_cambio={unchanged}  no_encontrado={not_found_bulk}")

    # ── Marcar throttle (solo si --apply) ─────────────────────────────────────
    if apply:
        AppConfig.set_value(cfg_key, "1")

    # ── Reporte final ─────────────────────────────────────────────────────────
    _print_report(changes, total, ok, unchanged, not_found_bulk,
                  individual_ok, individual_fail, type_fixed, apply, today)


def _print_report(
    changes: list[dict], total: int, ok: int, unchanged: int,
    not_found_bulk: int, individual_ok: int, individual_fail: int,
    type_fixed: int, applied: bool, today: str,
):
    print()
    print("═" * 68)
    print(f"  KAIROS — ACTUALIZACIÓN DE RANKINGS   [{today}]")
    mode = "APLICADO" if applied else "DRY-RUN (sin cambios en DB)"
    print(f"  Modo: {mode}")
    print("═" * 68)
    print(f"  Total procesados      : {total}")
    print(f"  Con cambios (ranking) : {ok}")
    print(f"  Sin cambios           : {unchanged}")
    print(f"  Tipo ATP/WTA corregido: {type_fixed}")
    print(f"  No encontrados (bulk) : {not_found_bulk}")
    print(f"    └ fallback individual OK  : {individual_ok}")
    print(f"    └ fallback individual FAIL: {individual_fail}")
    print()

    # Muestra los 30 mayores cambios
    big_changes = [c for c in changes if c.get("new_rank") is not None]
    big_changes.sort(key=lambda x: abs((x["new_rank"] or 0) - (x["db_rank"] or 0)), reverse=True)
    if big_changes:
        print("  TOP 30 CAMBIOS:")
        print(f"  {'Jugador':<30} {'DB':>6} {'Nuevo':>6} {'Tipo ':>5} {'Fuente':<14}")
        print("  " + "─" * 62)
        for c in big_changes[:30]:
            nombre = c["nombre"][:29]
            delta  = (c["new_rank"] or 0) - (c["db_rank"] or 0)
            arrow  = "▲" if delta < 0 else "▼"
            print(f"  {nombre:<30} {c['db_rank']:>6} {c['new_rank']:>6}  "
                  f"{c['new_type']:>4}  {arrow}{abs(delta):<5} {c.get('_source','?')[:13]}")

    not_found = [c for c in changes if c["new_rank"] is None]
    if not_found:
        print()
        print(f"  NO ENCONTRADOS EN NINGUNA FUENTE ({len(not_found)}):")
        for c in not_found[:20]:
            print(f"    #{c['db_rank']:>5} [{c['db_type']}] {c['nombre']}")
        if len(not_found) > 20:
            print(f"    ... y {len(not_found) - 20} más")

    if not applied:
        print()
        print("  ⚠  Modo DRY-RUN: no se escribió nada en DB.")
        print("     Añade --apply para aplicar los cambios.")
    print("═" * 68)


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Actualiza Player.ranking desde fuentes oficiales ATP/WTA/ITF."
    )
    parser.add_argument("--apply", action="store_true",
                        help="Aplicar cambios a DB (default: dry-run)")
    parser.add_argument("--force", action="store_true",
                        help="Ignorar throttle diario")
    parser.add_argument("--top",   type=int, default=None,
                        help="Solo jugadores con ranking ≤ N en DB")
    parser.add_argument("--type",  choices=["ATP", "WTA"], default=None,
                        help="Filtrar por tipo de ranking")
    parser.add_argument("--delay", type=float, default=2.5,
                        help="Delay base en segundos entre búsquedas individuales (default: 2.5)")
    parser.add_argument("--batch-size", type=int, default=30,
                        help="Nº búsquedas individuales antes de pausa larga (default: 30)")
    parser.add_argument("--max-individual", type=int, default=60,
                        help="Máx búsquedas individuales HTTP (default: 60)")
    args = parser.parse_args()

    print("\nConectando a kairos.db...")
    init_db()
    db.connect(reuse_if_open=True)

    run(
        apply          = args.apply,
        force          = args.force,
        top_n          = args.top,
        type_filter    = args.type,
        delay          = args.delay,
        batch_size     = args.batch_size,
        max_individual = args.max_individual,
    )

    db.close()


if __name__ == "__main__":
    main()
