#!/usr/bin/env python3
"""
check_bet_scores.py
Revisa y muestra los marcadores finales de los matches en que se apostó.
Fuentes: Sofascore (primario) y fixture_cache en DB (secundario).

Uso:
    python check_bet_scores.py
    python check_bet_scores.py --save    # también guarda scores en la DB
"""

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timedelta

# ── Bootstrap ────────────────────────────────────────────────────────────────
sys.path.insert(0, ".")
from src.database.database import init_db

init_db()

from src.database.database import Match, Player
from src.scrapers.sofascore_scraper import SofascoreScraper
from src.web.dashboard import _fixture_cache, _get_wallet

# ── Utilidades ────────────────────────────────────────────────────────────────

from src.core.name_utils import _last_name, _similar, _names_match  # noqa: F401


def _find_sofa_match(sofa_matches: list, p1: str, p2: str):
    """Busca el match de Sofascore que corresponda a p1 vs p2."""
    for m in sofa_matches:
        sp1 = m["player1"]
        sp2 = m["player2"]
        if ((_names_match(p1, sp1) and _names_match(p2, sp2)) or
                (_names_match(p1, sp2) and _names_match(p2, sp1))):
            return m
    return None


def _get_score_from_db(fixture_name: str) -> dict | None:
    """Busca score en la DB por apellidos de jugadores."""
    parts = fixture_name.split(" vs ", 1)
    if len(parts) != 2:
        return None
    p1_last = _last_name(parts[0])
    p2_last = _last_name(parts[1])
    try:
        rows = Match.select().where(Match.resultado != "")
        for row in rows:
            try:
                p1_obj = Player.get_by_id(row.player1_id)
            except Exception:
                continue
            p1_db = p1_obj.nombre.lower()
            if p1_last not in p1_db:
                continue
            # Verificar p2
            try:
                p2_obj = Player.get_by_id(row.player2_id)
                if p2_last in p2_obj.nombre.lower():
                    return {
                        "resultado":     row.resultado,
                        "period_scores": row.period_scores,
                        "total_games":   row.total_games,
                        "games_p1":      row.games_p1,
                        "games_p2":      row.games_p2,
                        "source":        "DB",
                    }
            except Exception:
                pass
    except Exception:
        pass
    return None


def _save_score_to_db(fixture_name: str, score: dict):
    """Guarda el score en la DB para el match correspondiente."""
    parts = fixture_name.split(" vs ", 1)
    if len(parts) != 2:
        return False
    p1_last = _last_name(parts[0])
    p2_last = _last_name(parts[1])
    try:
        rows = Match.select().where(Match.resultado == "")
        for row in rows:
            try:
                p1_obj = Player.get_by_id(row.player1_id)
            except Exception:
                continue
            p1_db = p1_obj.nombre.lower()
            if p1_last not in p1_db:
                continue
            try:
                p2_obj = Player.get_by_id(row.player2_id)
                if p2_last in p2_obj.nombre.lower():
                    m = Match.get_by_id(row.id)
                    m.resultado     = score.get("resultado") or ""
                    m.period_scores = score.get("period_scores") or ""
                    m.total_games   = score.get("total_games")
                    m.games_p1      = score.get("games_p1")
                    m.games_p2      = score.get("games_p2")
                    m.save()
                    return True
            except Exception:
                pass
    except Exception as e:
        print(f"  [DB] Error guardando score: {e}")
    return False


def _format_period_scores(ps) -> str:
    """Formatea period_scores a string legible."""
    if not ps:
        return ""
    if isinstance(ps, str):
        return ps
    if isinstance(ps, list):
        return ";".join(f"{g1},{g2}" for g1, g2 in ps)
    return str(ps)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Revisa marcadores de bets")
    parser.add_argument("--save", action="store_true",
                        help="Guardar scores encontrados en la DB")
    parser.add_argument("--all", action="store_true",
                        help="Incluir bets ya liquidadas (won/lost), no solo pendientes")
    args = parser.parse_args()

    w = _get_wallet()
    bets = w.bets if args.all else w.pending_bets
    total = len(w.bets)
    pending = len(w.pending_bets)

    print(f"\n{'='*62}")
    print("  KAIROS — Revisión de marcadores por bet")
    print(f"{'='*62}")
    print(f"  Total bets: {total}  |  Pendientes: {pending}  |  Mostrando: {len(bets)}")
    print(f"{'='*62}\n")

    if not bets:
        print("  No hay bets que revisar.")
        return

    # ── Agrupar bets por (fixture_name, fecha) ─────────────────────────────
    groups: dict[tuple, list] = defaultdict(list)
    for b in bets:
        fecha = b.timestamp[:10]
        groups[(b.fixture_name, fecha)].append(b)

    # ── Pre-cargar Sofascore por fecha única ───────────────────────────────
    scraper = SofascoreScraper()
    sofa_by_date: dict[str, list] = {}
    for fixture_name, fecha in groups.keys():
        if fecha not in sofa_by_date:
            print(f"  [Sofascore] Cargando {fecha}...", end=" ", flush=True)
            sofa_by_date[fecha] = scraper.get_matches(
                target_date=fecha,
                categories=["WTA", "ATP", "Challenger"]
            )
            # También buscar el día siguiente (matches que empezaron tarde)
            next_day = (datetime.strptime(fecha, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            extra = scraper.get_matches(target_date=next_day, categories=["WTA", "ATP", "Challenger"])
            sofa_by_date[fecha] += extra
            print(f"{len(sofa_by_date[fecha])} matches cargados")

    # ── Mostrar resultados por grupo ───────────────────────────────────────
    saved_count = 0
    for (fixture_name, fecha), bet_list in sorted(groups.items(), key=lambda x: x[0][1]):
        p1, p2 = fixture_name.split(" vs ", 1) if " vs " in fixture_name else (fixture_name, "?")

        # Buscar score en orden: fixture_cache → Sofascore → DB
        score = None
        source = None

        # 1. fixture_cache (en memoria)
        cache = _fixture_cache.get(fixture_name, {})
        if cache.get("resultado") or cache.get("sets_p1") is not None:
            ps = _format_period_scores(cache.get("period_scores"))
            res = cache.get("resultado") or f"{cache.get('sets_p1')}-{cache.get('sets_p2')}"
            score = {
                "resultado":     res,
                "period_scores": ps,
                "total_games":   cache.get("total_games"),
                "games_p1":      cache.get("games_p1"),
                "games_p2":      cache.get("games_p2"),
                "source":        "cache",
            }

        # 2. Sofascore
        if score is None:
            sm = _find_sofa_match(sofa_by_date.get(fecha, []), p1, p2)
            if sm and sm.get("resultado"):
                score = {
                    "resultado":     sm["resultado"],
                    "period_scores": sm.get("period_scores") or "",
                    "total_games":   sm.get("total_games"),
                    "games_p1":      sm.get("games_p1"),
                    "games_p2":      sm.get("games_p2"),
                    "source":        f"Sofascore ({sm.get('tournament','')})",
                }
            elif sm:
                score = {"resultado": None, "source": f"Sofascore/{sm.get('status','?')}"}

        # 3. DB
        if score is None or not score.get("resultado"):
            db_score = _get_score_from_db(fixture_name)
            if db_score:
                score = db_score

        # ── Imprimir grupo ─────────────────────────────────────────────────
        print(f"  {fecha}  {fixture_name}")

        if score and score.get("resultado"):
            res = score["resultado"]
            ps  = _format_period_scores(score.get("period_scores")) or "—"
            g1  = score.get("games_p1")
            g2  = score.get("games_p2")
            src = score.get("source", "?")
            games_str = f"  ({g1}/{g2} juegos)" if g1 is not None else ""
            print(f"  {'─'*58}")
            print(f"    Resultado:  {res}{games_str}")
            print(f"    Sets/juegos: {ps}")
            print(f"    Fuente:      {src}")

            # Guardar en DB si se pidió
            if args.save and score.get("source", "").startswith("Sofascore"):
                saved = _save_score_to_db(fixture_name, score)
                if saved:
                    saved_count += 1
                    print("    [DB] Score guardado ✓")
        else:
            print("    Score: NO ENCONTRADO")

        # ── Desglose de bets del grupo ─────────────────────────────────────
        for b in bet_list:
            status_sym = "✓" if b.status == "won" else "✗" if b.status == "lost" else "⏳"
            scope = b.resolution_scope if hasattr(b, "resolution_scope") else "match"
            legs_txt = " + ".join(
                f"{lg.market_label}/{lg.side}@{lg.odd}" for lg in b.legs
            )
            print(f"    {status_sym}  [{b.arb_tipo:6}] scope={scope:8} "
                  f"stake={b.total_staked:.2f} pnl={b.pnl:+.2f}  {legs_txt[:50]}")

        print()

    if args.save:
        print(f"  Scores guardados en DB: {saved_count}")
    print(f"{'='*62}\n")


if __name__ == "__main__":
    main()
