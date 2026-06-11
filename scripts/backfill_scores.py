#!/usr/bin/env python3
"""
backfill_scores.py
══════════════════
Rellena marcadores finales faltantes en la tabla Match consultando Sofascore.
Diseñado para alimentar el modelo de predicción con datos históricos completos.

Uso:
    python3 backfill_scores.py                    # 2026-03-29 → ayer
    python3 backfill_scores.py --from 2026-03-29  # desde una fecha
    python3 backfill_scores.py --date 2026-04-01  # solo esa fecha
    python3 backfill_scores.py --dry-run          # simula sin escribir
    python3 backfill_scores.py --stats            # solo muestra estadísticas

Flujo por fecha:
  1. Carga todos los Match de esa fecha que no tienen resultado
  2. Descarga los matches finalizados de Sofascore (WTA + ATP + Challenger)
  3. Para cada match Sofascore finalizado, busca candidato en DB con fuzzy matching
     de nombres completos (normalizando acentos y formato)
  4. Si encuentra con resultado vacío → actualiza score + ganador
  5. Si no encuentra → inserta como nuevo match con score completo
  6. Reporta resumen por fecha
"""

import argparse
import difflib

# ─── bootstrap: asegura que el package src sea importable ────────────────────
import os
import sys
import time
import unicodedata
from datetime import date, timedelta

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

# ─── ORM y scrapers ──────────────────────────────────────────────────────────
from src.database.database import (
    Match,
    Player,
    db,
    get_or_create_player,
    get_or_create_tournament,
    init_db,
)
from src.scrapers.sofascore_scraper import SofascoreScraper

# ═════════════════════════════════════════════════════════════════════════════
#  CONSTANTES
# ═════════════════════════════════════════════════════════════════════════════

FUZZY_THRESHOLD   = 0.72   # similitud mínima para considerar coincidencia de nombre
PERFECT_THRESHOLD = 0.90   # sobre este valor se acepta sin verificación adicional
REQUEST_DELAY     = 1.2    # segundos entre peticiones a Sofascore (fair use)


# ═════════════════════════════════════════════════════════════════════════════
#  NORMALIZACIÓN / FUZZY MATCHING
# ═════════════════════════════════════════════════════════════════════════════

def _norm(name: str) -> str:
    """
    Normaliza nombre para comparación:
    - Elimina acentos / diacríticos
    - Minúsculas
    - Expande puntos de iniciales: "E. Alexandrova" → "e  alexandrova"
    - Colapsa espacios múltiples
    """
    # Eliminar diacríticos
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Minúsculas, puntos → espacio (iniciales como "E.")
    s = ascii_str.lower().replace(".", " ").replace("-", " ")
    return " ".join(s.split())


def _sim(a: str, b: str) -> float:
    """Similitud de cadenas normalizadas (0–1)."""
    na, nb = _norm(a), _norm(b)
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _pair_sim(sofa_p1: str, sofa_p2: str, db_p1: str, db_p2: str) -> float:
    """
    Similitud del par (sofa_p1,sofa_p2) contra (db_p1,db_p2).
    Retorna el mínimo de las dos similitudes individuales para ser conservador.
    """
    s1 = _sim(sofa_p1, db_p1)
    s2 = _sim(sofa_p2, db_p2)
    return min(s1, s2)


def _find_db_match(
    sofa_p1: str,
    sofa_p2: str,
    db_records: list,
    threshold: float = FUZZY_THRESHOLD,
) -> tuple[object | None, bool]:
    """
    Busca el mejor candidato en db_records para (sofa_p1, sofa_p2).

    Retorna (match_obj, is_reversed):
      - match_obj: el Match de DB, o None si no se encuentra
      - is_reversed: True si sofa_p1 corresponde a db.player2 (orden invertido)

    Estrategia:
      1. Comparación directa (sofa_p1≈db_p1, sofa_p2≈db_p2)
      2. Comparación inversa (sofa_p1≈db_p2, sofa_p2≈db_p1)
      3. Elige el candidato con mayor similitud mínima sobre el umbral
    """
    best_match  = None
    best_score  = 0.0
    best_reversed = False

    for rec in db_records:
        p1_name = rec["p1"]
        p2_name = rec["p2"]

        # Orientación directa
        s_direct = _pair_sim(sofa_p1, sofa_p2, p1_name, p2_name)
        # Orientación inversa
        s_inv    = _pair_sim(sofa_p1, sofa_p2, p2_name, p1_name)

        if s_direct >= threshold and s_direct > best_score:
            best_score   = s_direct
            best_match   = rec["obj"]
            best_reversed = False

        if s_inv >= threshold and s_inv > best_score:
            best_score   = s_inv
            best_match   = rec["obj"]
            best_reversed = True

    return best_match, best_reversed


# ═════════════════════════════════════════════════════════════════════════════
#  LÓGICA DE SCORE / GANADOR
# ═════════════════════════════════════════════════════════════════════════════

def _flip_resultado(resultado: str) -> str:
    """Invierte "2-1" → "1-2"."""
    parts = resultado.split("-")
    if len(parts) == 2:
        return f"{parts[1]}-{parts[0]}"
    return resultado


def _flip_period_scores(period_scores: str) -> str:
    """Invierte period_scores: "6,4;3,6" → "4,6;6,3"."""
    if not period_scores:
        return period_scores
    sets = []
    for s in period_scores.split(";"):
        parts = s.split(",")
        if len(parts) == 2:
            sets.append(f"{parts[1]},{parts[0]}")
        else:
            sets.append(s)
    return ";".join(sets)


def _determine_winner(resultado: str, match_obj) -> Player | None:
    """
    Determina el ganador a partir del resultado (desde perspectiva de player1).
    "2-0", "2-1" → player1. "0-2", "1-2" → player2.
    """
    if not resultado:
        return None
    parts = resultado.split("-")
    if len(parts) != 2:
        return None
    try:
        sets_p1, sets_p2 = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if sets_p1 > sets_p2:
        return match_obj.player1
    if sets_p2 > sets_p1:
        return match_obj.player2
    return None


# ═════════════════════════════════════════════════════════════════════════════
#  BACKFILL POR FECHA
# ═════════════════════════════════════════════════════════════════════════════

def backfill_date(fecha: str, dry_run: bool = False, scraper: SofascoreScraper | None = None) -> dict:
    """
    Backfill completo para una fecha dada.

    Retorna dict con contadores:
      updated    — matches existentes actualizados con score
      inserted   — matches nuevos insertados desde Sofascore
      skipped    — scores ya existentes (sin cambio)
      no_match   — matches Sofascore sin contrapartida en DB (insertados)
    """
    stats = {"updated": 0, "inserted": 0, "skipped": 0, "no_match": 0, "errors": 0}
    if scraper is None:
        scraper = SofascoreScraper()

    # ── 1. Descargar desde Sofascore ─────────────────────────────────────────
    # Incluimos ITF para cubrir los torneos de circuito menor en nuestra DB
    sofa_matches = scraper.get_matches(
        fecha,
        categories=["WTA", "ATP", "Challenger", "ITF Women", "ITF Men"],
    )
    finished = [m for m in sofa_matches if m.get("status") == "finished" and m.get("resultado")]
    if not finished:
        print(f"  [BACKFILL] {fecha}: 0 matches finalizados en Sofascore → skip")
        return stats

    # ── 2. Cargar matches de DB para esa fecha ───────────────────────────────
    db_records_raw = (
        Match
        .select(Match, Player)
        .join(Player, on=(Match.player1 == Player.id))
        .where(Match.fecha == fecha)
    )

    # Construir índice: [{obj, p1, p2}]
    db_records = []
    for rec in db_records_raw:
        try:
            p2 = Player.get_by_id(rec.player2_id)
            db_records.append({
                "obj": rec,
                "p1":  rec.player1.nombre,
                "p2":  p2.nombre,
            })
        except Exception:
            pass

    total_db       = len(db_records)
    db_no_score    = [r for r in db_records if not (r["obj"].resultado or "").strip()]
    total_no_score = len(db_no_score)

    print(f"\n[BACKFILL] {fecha}: {len(finished)} finalizados Sofascore | "
          f"{total_db} en DB ({total_no_score} sin score)")

    # ── 3. Cruzar Sofascore vs DB ─────────────────────────────────────────────
    for sm in finished:
        sp1     = sm["player1"]
        sp2     = sm["player2"]
        s_res   = sm["resultado"]       # "2-1" desde perspectiva sofa_p1
        s_ps    = sm.get("period_scores") or ""
        s_tg    = sm.get("total_games")
        s_gp1   = sm.get("games_p1")
        s_gp2   = sm.get("games_p2")

        candidate, is_reversed = _find_db_match(sp1, sp2, db_records)

        if candidate is not None:
            # Ajustar orientación si está invertido
            if is_reversed:
                c_res  = _flip_resultado(s_res)
                c_ps   = _flip_period_scores(s_ps)
                c_gp1, c_gp2 = s_gp2, s_gp1
            else:
                c_res  = s_res
                c_ps   = s_ps
                c_gp1, c_gp2 = s_gp1, s_gp2

            c_tg = s_tg  # total_games no cambia con la orientación

            if (candidate.resultado or "").strip():
                # Ya tiene score — verificar consistencia
                if candidate.resultado != c_res:
                    print(f"  ⚠ DISCREPANCIA {candidate.player1.nombre} vs {candidate.player2_id}: "
                          f"DB={candidate.resultado} Sofa={c_res}")
                stats["skipped"] += 1
                continue

            # Actualizar
            winner = _determine_winner(c_res, candidate)
            if not dry_run:
                try:
                    with db.atomic():
                        candidate.resultado     = c_res
                        candidate.period_scores = c_ps
                        candidate.total_games   = c_tg
                        candidate.games_p1      = c_gp1
                        candidate.games_p2      = c_gp2
                        if winner:
                            candidate.ganador = winner
                        candidate.save()
                    stats["updated"] += 1
                    orient_tag = " (inv)" if is_reversed else ""
                    print(f"  ✓ UPDATE{orient_tag}: {candidate.player1.nombre} vs "
                          f"{Player.get_by_id(candidate.player2_id).nombre} → {c_res}")
                except Exception as e:
                    print(f"  ✗ ERROR update: {sp1} vs {sp2}: {e}")
                    stats["errors"] += 1
            else:
                orient_tag = " (inv)" if is_reversed else ""
                print(f"  [DRY] UPDATE{orient_tag}: {candidate.player1.nombre} vs "
                      f"{Player.get_by_id(candidate.player2_id).nombre} → {c_res}")
                stats["updated"] += 1

        else:
            # No hay match en DB → insertar nuevo con score completo
            stats["no_match"] += 1
            if not dry_run:
                try:
                    surface = sm.get("surface") or ""
                    tournament_name = sm.get("tournament") or "Sofascore"
                    hora = sm.get("hora") or ""

                    p1 = get_or_create_player(nombre=sp1)
                    p2 = get_or_create_player(nombre=sp2)
                    t  = get_or_create_tournament(
                        nombre=tournament_name,
                        superficie=surface,
                        ubicacion="",
                        fecha=fecha,
                    )
                    scraper_url = (
                        f"sofascore:{sm.get('sofascore_id') or (sp1+'|'+sp2)}|{fecha}"
                    )
                    match_obj, created = Match.get_or_create(
                        scraper_url=scraper_url,
                        defaults={
                            "player1":       p1,
                            "player2":       p2,
                            "tournament":    t,
                            "fecha":         fecha,
                            "hora":          hora,
                            "superficie":    surface,
                            "resultado":     s_res,
                            "period_scores": s_ps,
                            "total_games":   s_tg,
                            "games_p1":      s_gp1,
                            "games_p2":      s_gp2,
                        }
                    )
                    if created:
                        # Actualizar ganador
                        winner = _determine_winner(s_res, match_obj)
                        if winner:
                            match_obj.ganador = winner
                            match_obj.save()
                        stats["inserted"] += 1
                        print(f"  + INSERT: {sp1} vs {sp2} → {s_res} ({tournament_name})")
                except Exception as e:
                    print(f"  ✗ ERROR insert {sp1} vs {sp2}: {e}")
                    stats["errors"] += 1
            else:
                print(f"  [DRY] INSERT: {sp1} vs {sp2} → {s_res}")
                stats["inserted"] += 1

    return stats


# ═════════════════════════════════════════════════════════════════════════════
#  ESTADÍSTICAS RÁPIDAS
# ═════════════════════════════════════════════════════════════════════════════

def show_stats():
    """Muestra el estado actual de scores en DB."""
    import sqlite3

    from src.core.config import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT substr(fecha,1,10) as d, count(*) as n, "
        "sum(case when resultado IS NOT NULL AND resultado!='' then 1 else 0 end) as ws "
        "FROM match GROUP BY d ORDER BY d"
    ).fetchall()
    conn.close()

    print("\n── Estado de scores en DB ────────────────────────────────────────")
    print(f"{'Fecha':<13} {'Total':>7} {'ConScore':>9} {'SinScore':>9} {'Cobertura':>10}")
    print("─" * 55)
    total_n = total_ws = 0
    for d, n, ws in rows:
        ss = n - ws
        pct = (ws / n * 100) if n > 0 else 0
        print(f"  {d:<11} {n:>7} {ws:>9} {ss:>9} {pct:>9.1f}%")
        total_n += n
        total_ws += ws
    print("─" * 55)
    pct_total = (total_ws / total_n * 100) if total_n > 0 else 0
    print(f"  {'TOTAL':<11} {total_n:>7} {total_ws:>9} {total_n-total_ws:>9} {pct_total:>9.1f}%")
    print()


# ═════════════════════════════════════════════════════════════════════════════
#  ENTRYPOINT
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Backfill de scores desde Sofascore hacia la DB de Kairos"
    )
    parser.add_argument("--date",    help="Solo backfill esta fecha (YYYY-MM-DD)")
    parser.add_argument("--from",    dest="from_date", help="Inicio del rango (YYYY-MM-DD, default: 2026-03-29)")
    parser.add_argument("--to",      dest="to_date",   help="Fin del rango (YYYY-MM-DD, default: ayer)")
    parser.add_argument("--dry-run", action="store_true", help="Simula sin escribir en DB")
    parser.add_argument("--stats",   action="store_true", help="Solo muestra estadísticas, sin backfill")
    args = parser.parse_args()

    # Inicializar ORM
    init_db()

    if args.stats:
        show_stats()
        return

    if args.date:
        dates = [args.date]
    else:
        start = date.fromisoformat(args.from_date) if args.from_date else date(2026, 3, 29)
        end   = date.fromisoformat(args.to_date)   if args.to_date   else date.today() - timedelta(days=1)
        dates = []
        d = start
        while d <= end:
            dates.append(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    print(f"\n{'═'*60}")
    print(f"  KAIROS — Backfill de Scores  [{mode}]")
    print(f"  Fechas: {dates[0]} → {dates[-1]}  ({len(dates)} día(s))")
    print(f"{'═'*60}")

    show_stats()

    scraper = SofascoreScraper()
    total_stats = {"updated": 0, "inserted": 0, "skipped": 0, "no_match": 0, "errors": 0}

    for i, fecha in enumerate(dates):
        if i > 0:
            time.sleep(REQUEST_DELAY)  # cortesía con la API pública
        result = backfill_date(fecha, dry_run=args.dry_run, scraper=scraper)
        for k in total_stats:
            total_stats[k] += result.get(k, 0)

    print(f"\n{'═'*60}")
    print("  RESUMEN FINAL")
    print(f"  ✓ Actualizados : {total_stats['updated']}")
    print(f"  + Insertados   : {total_stats['inserted']}")
    print(f"  ○ Ya tenían score (skip): {total_stats['skipped']}")
    print(f"  ? Sin match DB  (insertados): {total_stats['no_match']}")
    print(f"  ✗ Errores      : {total_stats['errors']}")
    print(f"{'═'*60}")

    if not args.dry_run:
        print()
        show_stats()


if __name__ == "__main__":
    main()
