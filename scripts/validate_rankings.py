#!/usr/bin/env python3
"""
validate_rankings.py
════════════════════
Valida ranking oficial y coherencia ELO de jugadores en kairos.db.

Fuentes oficiales:
  ATP  → atptour.com       (top 500, HTML + AJAX paginado)
  WTA  → api.wtatennis.com (top 500, JSON API)
  ITF  → itftennis.com     (World Tennis Number, top 300)

Métricas:
  1. Player.ranking vs ranking oficial  (delta y tolerancia configurable)
  2. Player.ranking_type correcto (ATP / WTA)
  3. PlayerElo.elo coherencia vs ranking oficial (correlación Spearman + outliers)
  4. Jugadores sin PlayerElo 'Overall'
  5. Jugadores no encontrados en fuentes oficiales (potencialmente retirados o datos stale)

Uso:
  python validate_rankings.py                        # ATP + WTA + ELO
  python validate_rankings.py --source atp           # solo ATP
  python validate_rankings.py --source wta           # solo WTA
  python validate_rankings.py --source itf           # solo ITF (WTN)
  python validate_rankings.py --top 100              # solo top 100 jugadores en DB
  python validate_rankings.py --tol 20               # tolerancia ±20 posiciones
  python validate_rankings.py --csv reporte.csv      # exportar resultados
  python validate_rankings.py --no-elo               # omitir análisis ELO
"""

import argparse
import csv
import difflib
import os
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

from src.database.database import Player, PlayerElo, db, init_db

# ── constantes ────────────────────────────────────────────────────────────────
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.google.com/",
}
_JSON_HEADERS = {**_HEADERS, "Accept": "application/json, */*;q=0.8"}

_ATP_RANKINGS_URL      = "https://www.atptour.com/en/rankings/singles"
_ATP_AJAX_URL          = (
    "https://www.atptour.com/en/-/ajax/Rankings/GetNextRankingPage"
    "?rankType=singles&rankDate={date}&page={page}&nativSort=RankValue&nativSortDir=asc"
)
_LT_ATP_URL            = "http://live-tennis.eu/en/atp-live-ranking"
_LT_WTA_URL            = "http://live-tennis.eu/en/wta-live-ranking"
_LT_ITF_MEN_URL        = "http://live-tennis.eu/en/itf-men-live-ranking"
_LT_ITF_WOMEN_URL      = "http://live-tennis.eu/en/itf-women-live-ranking"

REQUEST_DELAY  = 1.2   # segundos entre requests
ELO_BENCH_200  = 1520  # ELO de referencia para rank ~200 (calibración aproximada)
ELO_BENCH_1    = 1800  # ELO de referencia para rank #1

# ── helpers ───────────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 20, headers: dict = None,
         params: dict = None) -> requests.Response | None:
    try:
        resp = requests.get(
            url, headers=headers or _HEADERS,
            timeout=timeout, allow_redirects=True, params=params,
        )
        if resp.status_code == 200:
            return resp
        print(f"  [HTTP {resp.status_code}] {url}")
        return None
    except Exception as exc:
        print(f"  [ERR] {exc} — {url}")
        return None


def _norm(name: str) -> str:
    """Normaliza: sin acentos, minúsculas, solo alfanumérico+espacios."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_ = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]", " ", ascii_.lower()).strip()


def _fuzzy(target: str, candidates: list[str], cutoff: float = 0.72) -> str | None:
    t = _norm(target)
    normed = {_norm(c): c for c in candidates}
    matches = difflib.get_close_matches(t, list(normed), n=1, cutoff=cutoff)
    return normed[matches[0]] if matches else None


# ══════════════════════════════════════════════════════════════════════════════
#  SCRAPERS BULK
# ══════════════════════════════════════════════════════════════════════════════

def fetch_atp_rankings(top_n: int = 500) -> dict[str, int]:
    """
    Retorna {nombre_normalizado: ranking} para jugadores ATP.
    Estrategia:
      1. HTML de la página de rankings (≈ primeros 100 con server-side rendering).
      2. AJAX paginado si se necesita más.
    """
    print("  Descargando rankings ATP...")
    result: dict[str, int] = {}
    today = date.today().strftime("%Y-%m-%d")

    # ── Paso 1: HTML ──────────────────────────────────────────────────────────
    resp = _get(_ATP_RANKINGS_URL)
    if resp:
        soup = BeautifulSoup(resp.text, "html.parser")
        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            rank_text = cells[0].get_text(strip=True).replace(",", "").strip()
            if not re.match(r"^\d+$", rank_text):
                continue
            rank = int(rank_text)
            if rank > top_n:
                break
            # Nombre del jugador vía link href /en/players/<slug>/
            link = row.select_one('a[href*="/en/players/"]')
            if not link:
                continue
            href = link.get("href", "")
            slug_m = re.search(r"/en/players/([^/]+)/", href)
            if not slug_m:
                continue
            slug   = slug_m.group(1)
            nombre = " ".join(w.capitalize() for w in slug.split("-"))
            key    = _norm(nombre)
            result[key] = rank
        print(f"    HTML: {len(result)} jugadores")

    # ── Paso 2: AJAX paginado (si necesitamos más) ────────────────────────────
    # ── Paso 1b: live-tennis.eu como respaldo para completar top_n ──────────
    if len(result) < min(top_n, 200):
        time.sleep(REQUEST_DELAY)
        resp_lt = _get(_LT_ATP_URL)
        if resp_lt:
            lt_data = _parse_live_tennis_table(resp_lt.text, top_n)
            before = len(result)
            result.update(lt_data)   # live-tennis complementa o sobreescribe con más precisión
            print(f"    live-tennis.eu ATP: {len(result)} jugadores (antes: {before})")

    if len(result) < min(top_n, 200):
        page = 1
        while len(result) < top_n:
            time.sleep(REQUEST_DELAY)
            url   = _ATP_AJAX_URL.format(date=today, page=page)
            aresp = _get(url, headers=_JSON_HEADERS)
            if not aresp:
                break
            try:
                data = aresp.json()
                # La respuesta puede ser lista directa o {rankings: [...]}
                rows = data if isinstance(data, list) else data.get("rankings", [])
                if not rows:
                    break
                added = 0
                for p in rows:
                    rank = p.get("rankValue") or p.get("rank") or p.get("RankValue")
                    name = p.get("playerName") or p.get("FullName") or p.get("name", "")
                    if rank and name:
                        r = int(str(rank).replace(",", ""))
                        if r <= top_n:
                            result[_norm(name)] = r
                            added += 1
                if added == 0:
                    break
                page += 1
                if page > 10:   # max 10 páginas × ~50 = 500
                    break
            except Exception as exc:
                print(f"    ATP AJAX error pag.{page}: {exc}")
                break
        print(f"    Tras AJAX: {len(result)} jugadores")

    return result


def _parse_live_tennis_table(html: str, top_n: int) -> dict[str, int]:
    """
    Parsea la tabla de rankings de live-tennis.eu.
    Estructura de columna: [rank, tier_flag, img, name, age, country, points, ...]
    """
    result: dict[str, int] = {}
    soup = BeautifulSoup(html, "html.parser")
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        rank_text = cells[0].get_text(strip=True).replace(",", "").strip()
        if not re.match(r"^\d+$", rank_text):
            continue
        rank = int(rank_text)
        if rank > top_n:
            continue
        # Nombre está en cells[3]
        name = cells[3].get_text(strip=True)
        if name:
            result[_norm(name)] = rank
    return result


def fetch_wta_rankings(top_n: int = 500) -> dict[str, int]:
    """
    Retorna {nombre_normalizado: ranking} para jugadoras WTA.
    Fuente primaria: live-tennis.eu (hasta top 1000)
    """
    print("  Descargando rankings WTA...")
    result: dict[str, int] = {}

    resp = _get(_LT_WTA_URL)
    if resp:
        result = _parse_live_tennis_table(resp.text, top_n)
        print(f"    live-tennis.eu WTA: {len(result)} jugadoras")

    return result


def fetch_itf_rankings(top_n: int = 300) -> dict[str, int]:
    """
    Retorna {nombre_normalizado: itf_rank} para la clasificación ITF.
    Cubre jugadores no listados en ATP/WTA top.
    Fuente: live-tennis.eu (masc + fem combinados)
    """
    print("  Descargando rankings ITF...")
    result: dict[str, int] = {}

    resp_m = _get(_LT_ITF_MEN_URL)
    if resp_m:
        men = _parse_live_tennis_table(resp_m.text, top_n)
        result.update(men)
        print(f"    ITF masc: {len(men)} jugadores")

    time.sleep(REQUEST_DELAY)
    resp_f = _get(_LT_ITF_WOMEN_URL)
    if resp_f:
        women = _parse_live_tennis_table(resp_f.text, top_n)
        result.update(women)
        print(f"    ITF fem: {len(women)} jugadoras")

    if not result:
        print("    ITF: no se obtuvieron datos")
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  VALIDACIÓN
# ══════════════════════════════════════════════════════════════════════════════

def validate_rankings(
    official_atp: dict[str, int],
    official_wta: dict[str, int],
    official_itf: dict[str, int],
    top_n: int | None,
    tolerance: int,
) -> list[dict]:
    """
    Compara Player.ranking de la DB con el ranking oficial.

    Retorna lista de dicts con columnas:
      player_id, nombre, db_rank, db_type, official_rank, official_source,
      delta, status  [ok | desactualizado | tipo_incorrecto | no_encontrado]
    """
    query = (
        Player
        .select(Player.id, Player.nombre, Player.ranking, Player.ranking_type)
        .where(Player.ranking.is_null(False))
        .order_by(Player.ranking)
    )
    if top_n:
        query = query.where(Player.ranking <= top_n)

    # Aplanamos las fuentes en un solo mapa con nombre oficial source
    official_all: dict[str, tuple[int, str]] = {}
    for name, rank in official_atp.items():
        official_all[name] = (rank, "ATP")
    for name, rank in official_wta.items():
        existing = official_all.get(name)
        if existing is None or rank < existing[0]:
            official_all[name] = (rank, "WTA")
    for name, rank in official_itf.items():
        if name not in official_all:
            official_all[name] = (rank, "ITF")

    all_official_keys = list(official_all.keys())
    rows = []

    for p in query:
        p_norm = _norm(p.nombre)
        # Búsqueda directa primero, luego fuzzy
        match_key = None
        if p_norm in official_all:
            match_key = p_norm
        else:
            match_key = _fuzzy(p.nombre, [k for k in all_official_keys], cutoff=0.75)

        if match_key:
            off_rank, off_source = official_all[match_key]
            delta = (p.ranking or 0) - off_rank
            # Tipo correcto
            type_ok = (
                (off_source == "ATP" and (p.ranking_type or "ATP").upper() == "ATP") or
                (off_source == "WTA" and (p.ranking_type or "").upper() == "WTA") or
                off_source == "ITF"
            )
            if not type_ok:
                status = "tipo_incorrecto"
            elif abs(delta) <= tolerance:
                status = "ok"
            else:
                status = "desactualizado"
        else:
            off_rank, off_source, delta = None, "—", None
            status = "no_encontrado"

        rows.append({
            "player_id":       p.id,
            "nombre":          p.nombre,
            "db_rank":         p.ranking,
            "db_type":         (p.ranking_type or "ATP"),
            "official_rank":   off_rank,
            "official_source": off_source,
            "delta":           delta,
            "status":          status,
        })

    return rows


def validate_elo(
    official_atp: dict[str, int],
    official_wta: dict[str, int],
) -> list[dict]:
    """
    Analiza coherencia entre PlayerElo.elo y el ranking oficial.

    Para cada jugador con ELO Overall y ranking oficial:
      - Calcula el ELO esperado dado su rank (interpolación lineal simple)
      - Calcula la desviación respecto a la curva media
      - Detecta outliers (|desviación| > 2 sigma)
    """
    official_all: dict[str, tuple[int, str]] = {
        **{n: (r, "ATP") for n, r in official_atp.items()},
        **{n: (r, "WTA") for n, r in official_wta.items()},
    }
    all_keys = list(official_all.keys())

    elo_rows = (
        PlayerElo
        .select(PlayerElo, Player)
        .join(Player)
        .where(PlayerElo.surface == "Overall")
        .order_by(PlayerElo.elo.desc())
    )

    rows = []
    for er in elo_rows:
        p_norm   = _norm(er.player.nombre)
        match_k  = p_norm if p_norm in official_all else _fuzzy(
            er.player.nombre, all_keys, cutoff=0.75
        )
        off_rank = official_all[match_k][0] if match_k else None
        rows.append({
            "player_id":   er.player.id,
            "nombre":      er.player.nombre,
            "elo":         er.elo,
            "matches":     er.matches,
            "wins":        er.wins,
            "official_rank": off_rank,
            "official_source": official_all[match_k][1] if match_k else "—",
        })

    # Filtrar solo los que tienen ranking oficial (para correlación)
    ranked = [r for r in rows if r["official_rank"] is not None]

    if len(ranked) < 5:
        return rows   # sin suficientes datos para análisis estadístico

    # Correlación de Spearman
    ranked_by_elo  = sorted(ranked, key=lambda x: x["elo"], reverse=True)
    ranked_by_rank = sorted(ranked, key=lambda x: x["official_rank"])
    elo_ranks  = {r["player_id"]: i + 1 for i, r in enumerate(ranked_by_elo)}
    off_ranks2 = {r["player_id"]: i + 1 for i, r in enumerate(ranked_by_rank)}
    n = len(ranked)
    d2_sum = sum(
        (elo_ranks[r["player_id"]] - off_ranks2[r["player_id"]]) ** 2
        for r in ranked
    )
    rho = 1 - (6 * d2_sum) / (n * (n * n - 1))

    # ELO esperado lineal (calibración simple rank→ELO)
    # rank 1 ≈ ELO_BENCH_1, rank 200 ≈ ELO_BENCH_200
    def elo_expected(rank: int) -> float:
        if rank <= 0:
            return ELO_BENCH_1
        slope = (ELO_BENCH_1 - ELO_BENCH_200) / (1 - 200)
        return ELO_BENCH_1 + slope * (rank - 1)

    deviations = []
    for r in ranked:
        exp = elo_expected(r["official_rank"])
        r["elo_expected"] = round(exp, 1)
        r["elo_dev"]      = round(r["elo"] - exp, 1)
        deviations.append(r["elo_dev"])

    # Sigma
    mean_dev = sum(deviations) / len(deviations)
    var_dev  = sum((d - mean_dev) ** 2 for d in deviations) / len(deviations)
    sigma    = var_dev ** 0.5 if var_dev > 0 else 1.0

    for r in ranked:
        r["elo_zscore"] = round((r["elo_dev"] - mean_dev) / sigma, 2)
        r["rho"]        = round(rho, 4)

    # También anotar en no-ranked
    for r in rows:
        if r["official_rank"] is None:
            r["elo_expected"] = None
            r["elo_dev"]      = None
            r["elo_zscore"]   = None
            r["rho"]          = round(rho, 4)

    return rows


# ══════════════════════════════════════════════════════════════════════════════
#  REPORT
# ══════════════════════════════════════════════════════════════════════════════

def _bar(n: int, total: int, width: int = 20) -> str:
    filled = round(n / total * width) if total else 0
    return "█" * filled + "░" * (width - filled)


def print_report(
    rank_rows: list[dict],
    elo_rows: list[dict],
    sources: dict,
    tolerance: int,
    args_source: str,
):
    total_db   = len(rank_rows)
    ok_count   = sum(1 for r in rank_rows if r["status"] == "ok")
    stale_count = sum(1 for r in rank_rows if r["status"] == "desactualizado")
    type_count = sum(1 for r in rank_rows if r["status"] == "tipo_incorrecto")
    nf_count   = sum(1 for r in rank_rows if r["status"] == "no_encontrado")

    print()
    print("═" * 72)
    print("  KAIROS — VALIDACIÓN DE RANKINGS Y ELO")
    print(f"  Fecha: {date.today()}")
    print("═" * 72)

    # ── Fuentes ──────────────────────────────────────────────────────────────
    print()
    print("► FUENTES OFICIALES DESCARGADAS")
    for src, count in sources.items():
        status_str = f"{count} jugadores" if count else "sin datos"
        print(f"  {src:<6}: {status_str}")

    # ── Ranking ──────────────────────────────────────────────────────────────
    print()
    print(f"► RANKING DB vs OFICIAL  (tolerancia ±{tolerance} posiciones)")
    print(f"  Jugadores con ranking en DB : {total_db}")
    if total_db > 0:
        pct = lambda n: f"{n/total_db*100:.1f}%"
        print(f"  {'OK (dentro tolerancia)':<30}: {ok_count:>5}  ({pct(ok_count)})  "
              f"{_bar(ok_count, total_db)}")
        print(f"  {'Desactualizados (>±tol)':<30}: {stale_count:>5}  ({pct(stale_count)})  "
              f"{_bar(stale_count, total_db)}")
        print(f"  {'Tipo incorrecto ATP/WTA':<30}: {type_count:>5}  ({pct(type_count)})  "
              f"{_bar(type_count, total_db)}")
        print(f"  {'No encontrados oficialmente':<30}: {nf_count:>5}  ({pct(nf_count)})  "
              f"{_bar(nf_count, total_db)}")

    # Top discrepancias
    stale_sorted = sorted(
        [r for r in rank_rows if r["status"] in ("desactualizado", "tipo_incorrecto")],
        key=lambda x: abs(x["delta"] or 0),
        reverse=True,
    )[:20]
    if stale_sorted:
        print()
        print("  TOP DISCREPANCIAS (mayor delta DB vs oficial):")
        print(f"  {'Jugador':<28} {'DB Rk':>6} {'DB Typ':>6} {'Ofic Rk':>8} {'Ofic':>5} {'Delta':>6} {'Estado':<18}")
        print("  " + "─" * 80)
        for r in stale_sorted:
            delta_str = f"{r['delta']:+d}" if r["delta"] is not None else "—"
            off_r     = str(r["official_rank"]) if r["official_rank"] else "—"
            nombre    = r["nombre"][:27]
            print(f"  {nombre:<28} {r['db_rank']:>6} {r['db_type']:>6} "
                  f"{off_r:>8} {r['official_source']:>5} {delta_str:>6}  {r['status']}")

    # No encontrados (muestra los primeros 15)
    not_found = [r for r in rank_rows if r["status"] == "no_encontrado"]
    if not_found:
        print()
        print(f"  JUGADORES NO ENCONTRADOS EN FUENTES OFICIALES ({len(not_found)}):")
        print("  (pueden estar retirados, ser dobles, o tener ranking muy fuera del top)")
        for r in not_found[:15]:
            print(f"    #{r['db_rank']:>4} [{r['db_type']}] {r['nombre']}")
        if len(not_found) > 15:
            print(f"    ... y {len(not_found) - 15} más")

    # ── ELO ──────────────────────────────────────────────────────────────────
    if elo_rows:
        ranked_elo = [r for r in elo_rows if r.get("official_rank") is not None]
        rho = ranked_elo[0]["rho"] if ranked_elo else None
        total_elo = len(elo_rows)
        with_rank = len(ranked_elo)
        without_overall = _count_no_overall()

        print()
        print("► ELO KAIROS — COHERENCIA VS RANKING OFICIAL")
        print(f"  Jugadores con ELO Overall     : {total_elo}")
        print(f"  Con ranking oficial conocido  : {with_rank}")
        if rho is not None:
            rho_label = (
                "excelente" if rho >= 0.85 else
                "buena"     if rho >= 0.70 else
                "moderada"  if rho >= 0.50 else
                "débil — revisar datos"
            )
            print(f"  Correlación Spearman ρ(ELO, Rank): {rho:.4f}  [{rho_label}]")

        # Outliers ELO (|z-score| > 1.8)
        outliers = sorted(
            [r for r in ranked_elo if abs(r.get("elo_zscore") or 0) > 1.8],
            key=lambda x: abs(x["elo_zscore"]),
            reverse=True,
        )[:20]
        if outliers:
            print()
            print("  OUTLIERS ELO (|z| > 1.8 — ELO muy distinto al esperado para su rank):")
            print(f"  {'Jugador':<28} {'ELO':>7} {'ELO Esp':>8} {'Desv':>7} {'z':>6} {'Rk Ofic':>8} {'Src':>4} {'M':>5}")
            print("  " + "─" * 75)
            for r in outliers:
                nombre = r["nombre"][:27]
                sign   = "▲" if r["elo_dev"] > 0 else "▼"
                print(
                    f"  {nombre:<28} {r['elo']:>7.0f} {r['elo_expected']:>8.0f} "
                    f"{sign}{abs(r['elo_dev']):>6.0f} {r['elo_zscore']:>+6.2f} "
                    f"{r['official_rank']:>8} {r['official_source']:>4} {r['matches']:>5}"
                )

        # Top 20 ELO overall (verificación)
        top_elo = sorted(elo_rows, key=lambda x: x["elo"], reverse=True)[:20]
        print()
        print("  TOP 20 ELO OVERALL (verificación):")
        print(f"  {'#':>3}  {'ELO':>7}  {'Rk Ofic':>8}  {'Δ(ELO_calc-esp)':>16}  Jugador")
        print("  " + "─" * 65)
        for i, r in enumerate(top_elo, 1):
            rk_str  = str(r["official_rank"]) if r["official_rank"] else "?"
            dev_str = f"{r['elo_dev']:+.0f}" if r.get("elo_dev") is not None else "—"
            nombre  = r["nombre"][:35]
            print(f"  {i:>3}  {r['elo']:>7.1f}  {rk_str:>8}  {dev_str:>16}  {nombre}")

        if without_overall > 0:
            print()
            print(f"  ⚠  {without_overall} jugadores NO tienen PlayerElo 'Overall'.")
            print("     Ejecuta: python rebuild_elo.py")

    print()
    print("═" * 72)


def _count_no_overall() -> int:
    """Cuenta jugadores sin PlayerElo Overall."""
    total = Player.select().count()
    with_elo = (
        PlayerElo.select()
        .where(PlayerElo.surface == "Overall")
        .count()
    )
    return max(0, total - with_elo)


# ══════════════════════════════════════════════════════════════════════════════
#  CSV export
# ══════════════════════════════════════════════════════════════════════════════

def export_csv(path: str, rank_rows: list[dict], elo_rows: list[dict]):
    # Fusionar por player_id para CSV combinado
    elo_by_id = {r["player_id"]: r for r in elo_rows}
    with open(path, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "player_id", "nombre",
            "db_rank", "db_type",
            "official_rank", "official_source", "delta", "rank_status",
            "elo", "elo_matches", "elo_wins", "elo_expected", "elo_dev", "elo_zscore", "spearman_rho",
        ]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rank_rows:
            er = elo_by_id.get(r["player_id"], {})
            w.writerow({
                "player_id":       r["player_id"],
                "nombre":          r["nombre"],
                "db_rank":         r["db_rank"],
                "db_type":         r["db_type"],
                "official_rank":   r["official_rank"],
                "official_source": r["official_source"],
                "delta":           r["delta"],
                "rank_status":     r["status"],
                "elo":             er.get("elo", ""),
                "elo_matches":     er.get("matches", ""),
                "elo_wins":        er.get("wins", ""),
                "elo_expected":    er.get("elo_expected", ""),
                "elo_dev":         er.get("elo_dev", ""),
                "elo_zscore":      er.get("elo_zscore", ""),
                "spearman_rho":    er.get("rho", ""),
            })
    print(f"\n  CSV exportado → {path}")


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Valida ranking y ELO de jugadores en kairos.db contra fuentes oficiales."
    )
    parser.add_argument("--source", choices=["atp", "wta", "itf", "all"],
                        default="all", help="Fuente(s) a consultar (default: all)")
    parser.add_argument("--top",   type=int, default=None,
                        help="Limitar a jugadores con ranking ≤ N en la DB")
    parser.add_argument("--tol",   type=int, default=15,
                        help="Tolerancia en posiciones para considerar ranking OK (default: 15)")
    parser.add_argument("--no-elo", action="store_true",
                        help="Omitir análisis de coherencia ELO")
    parser.add_argument("--csv",    type=str, default=None,
                        help="Exportar resultados a este archivo CSV")
    args = parser.parse_args()

    # ── Init DB ───────────────────────────────────────────────────────────────
    print("\nConectando a kairos.db...")
    init_db()
    db.connect(reuse_if_open=True)

    total_players = Player.select().count()
    print(f"  Jugadores en DB: {total_players}")

    # ── Descargar rankings oficiales ──────────────────────────────────────────
    print("\nDescargando fuentes oficiales...")
    atp_map: dict[str, int] = {}
    wta_map: dict[str, int] = {}
    itf_map: dict[str, int] = {}

    if args.source in ("atp", "all"):
        atp_map = fetch_atp_rankings(top_n=args.top or 500)
        time.sleep(REQUEST_DELAY)

    if args.source in ("wta", "all"):
        wta_map = fetch_wta_rankings(top_n=args.top or 500)
        time.sleep(REQUEST_DELAY)

    if args.source in ("itf", "all"):
        itf_map = fetch_itf_rankings(top_n=300)

    sources = {
        "ATP": len(atp_map),
        "WTA": len(wta_map),
        "ITF": len(itf_map),
    }

    # ── Validar rankings ──────────────────────────────────────────────────────
    print("\nComparando DB vs oficial...")
    rank_rows = validate_rankings(atp_map, wta_map, itf_map, args.top, args.tol)

    # ── Validar ELO ──────────────────────────────────────────────────────────
    elo_rows: list[dict] = []
    if not args.no_elo:
        print("Analizando coherencia ELO...")
        elo_rows = validate_elo(atp_map, wta_map)

    # ── Reporte ──────────────────────────────────────────────────────────────
    print_report(rank_rows, elo_rows, sources, args.tol, args.source)

    # ── CSV ──────────────────────────────────────────────────────────────────
    if args.csv:
        export_csv(args.csv, rank_rows, elo_rows)

    db.close()


if __name__ == "__main__":
    main()
