#!/usr/bin/env python3
"""
backfill_top200.py
══════════════════
Descarga el historial de partidos para los top N ATP y WTA usando:
  - Jeff Sackmann CSVs → datos históricos bulk (con serve/BP stats incluidas,
                          sin proxy, sin rate-limit, Years 2020-2024)
  - Sofascore API       → delta 2025 reciente (sin fetch de stats por partido)

Flujo:
  1. Carga el top N ATP + top N WTA desde Sackmann ranking CSVs.
  2. Por cada jugador:
      a. [Sackmann] populate_cache(nombre, years=--years) → bulk histórico sin proxy.
      b. [Sofascore] _sofascore_sync_matches(target=--delta) → partidos 2025 recientes.
         (Solo si --delta > 0 y se resolvió el Sofascore ID del jugador.)
  3. Al terminar (o con --elo-only), reconstruye los ratings ELO.

Uso:
    # Modo completo: 5 años Sackmann + 100 partidos delta Sofascore, ambos tours
    python3 backfill_top200.py

    # Solo historial Sackmann sin delta Sofascore
    python3 backfill_top200.py --delta 0

    # 3 años, solo ATP
    python3 backfill_top200.py --years 3 --tour atp

    # Saltar jugadores que ya tienen suficientes registros
    python3 backfill_top200.py --skip-complete

    # Solo rebuild ELO
    python3 backfill_top200.py --elo-only

    # Probar con 3 jugadores (debug)
    python3 backfill_top200.py --dry-run --limit 3
"""

import argparse
import os
import sys
import time

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)


# ── Constantes ────────────────────────────────────────────────────────────────
_DEFAULT_YEARS   = 5      # años de historial Sackmann
_DEFAULT_DELTA   = 100    # partidos recientes (2025) a buscar en Sofascore + stats
_DEFAULT_TOP_N   = 200    # cuántos del ranking considerar


# ── Helpers de display ────────────────────────────────────────────────────────

def _bar(done: int, total: int, width: int = 30) -> str:
    """Barra de progreso ASCII."""
    filled = int(width * done / max(total, 1))
    return "[" + "█" * filled + "░" * (width - filled) + f"] {done}/{total}"


def _section(title: str) -> None:
    print("\n" + "═" * 60)
    print(f"  {title}")
    print("═" * 60)


# ── Carga de jugadores top N desde rankings oficiales ATP/WTA ─────────────────

def _load_top_players(top_n: int, tour: str | None) -> list[tuple[str, str, int]]:
    """
    Retorna lista de (nombre, ranking_type, ranking, ss_id).

    Fuente primaria: sitios oficiales ATP (atptour.com) y WTA (wtatennis.com).
    Fallback:        CSVs de Jeff Sackmann (GitHub) por tour si el scraper falla.
    """
    from src.database.database import Player
    from src.scrapers.atp_wta_scraper import get_top_rankings as _official_rankings
    from src.scrapers.sackmann_scraper import SackmannScraper

    tour_arg = tour if tour in ("atp", "wta") else "both"

    # ── Fuente primaria: sitios oficiales ─────────────────────────────────────
    tours_map = {
        "atp":  ["ATP"],
        "wta":  ["WTA"],
        "both": ["ATP", "WTA"],
    }
    live_data: list[dict] = []
    failed_tours: list[str] = []

    for t in tours_map[tour_arg]:
        entries = _official_rankings(n=top_n, tour=t)
        if entries:
            live_data.extend(entries)
        else:
            print(f"  [RANKINGS] {t}: sitio oficial sin datos → usando Sackmann CSV")
            failed_tours.append(t.lower())

    # ── Fallback Sackmann para los tours que fallaron ─────────────────────────
    for t in failed_tours:
        sc_data = SackmannScraper().get_top_rankings(n=top_n, tour=t)
        live_data.extend(sc_data)

    # ── Resolver Sofascore ID desde DB ────────────────────────────────────────
    result = []
    for entry in live_data:
        nombre       = entry["nombre"]
        ranking_type = entry["ranking_type"]
        ranking      = entry["ranking"]

        db_player = (
            Player
            .select()
            .where(Player.nombre == nombre)
            .first()
        )
        if db_player is None:
            parts = nombre.strip().split()
            last  = parts[-1] if parts else nombre
            db_player = (
                Player
                .select()
                .where(Player.nombre.contains(last))
                .where(Player.ranking_type == ranking_type)
                .first()
            )

        ss_id = (db_player.sofascore_id or 0) if db_player else 0
        result.append((nombre, ranking_type, ranking, ss_id))

    return result


# ── Resolución de Sofascore ID ────────────────────────────────────────────────

def _resolve_ss_id(nombre: str, db_ss_id: int, sc) -> int | None:
    """
    Devuelve el Sofascore team_id del jugador.
    Primero usa el que ya está en DB; si no, busca en Sofascore.
    Usa aliases para nombres que Sofascore normaliza diferente.
    """
    if db_ss_id and db_ss_id > 0:
        return db_ss_id

    # Aliases: nombres tal como vienen de Sackmann → variante que Sofascore reconoce.
    # Añadir aquí cualquier nuevo caso que falle con "No encontrado".
    _ALIASES: dict = {
        "Christopher Oconnell":  "Christopher O'Connell",
        "Bernarda Pera":         "Bernarda Pera",          # 502 transitorio — misma cadena
        "Anca Alexia Todoni":    "Anca Todoni",
    }
    queries = [nombre]
    if nombre in _ALIASES and _ALIASES[nombre] != nombre:
        queries.append(_ALIASES[nombre])

    print(f"  [ID] Buscando Sofascore ID para '{nombre}'…")
    found = None
    for q in queries:
        found = sc.search_player_id(q)
        if found:
            break

    if found:
        # Persistir para próximas ejecuciones
        try:
            from src.database.database import Player
            Player.update(sofascore_id=found).where(Player.nombre == nombre).execute()
        except Exception:
            pass
        print(f"  [ID] '{nombre}' → {found}")
    else:
        print(f"  [ID] No encontrado: '{nombre}'")
    return found


# ── Fetch + almacenamiento de partidos ────────────────────────────────────────

def _fetch_player(
    nombre:        str,
    ss_id:         int,
    years:         int,
    n_delta:       int,
    sc,
    skip_complete: bool = False,
    dry_run:       bool = False,
) -> tuple[int, int]:
    """
    Pipeline de ingestión para un jugador:

      1. Sackmann bulk histórico  → populate_cache(years=years)
         Sin proxy, sin rate-limit. Stats de servicio/BP incluidas en el CSV.

      2. Sofascore delta reciente → _sofascore_sync_matches(target=n_delta)
         Solo partidos de 2025 que Sackmann aún no tiene.
         Solo se ejecuta si ss_id es válido y n_delta > 0.

    Returns:
        (nuevos_partidos_insertados, 0)  — las stats ya vienen del CSV.
    """
    from src.database.database import SofascoreMatchCache
    from src.scrapers.player_stats_scraper import _sofascore_sync_bp_stats, _sofascore_sync_matches
    from src.scrapers.sackmann_scraper import SackmannScraper

    if skip_complete:
        # Una sola query con agregación condicional — evita round-trips a SQLite
        # y aprovecha el índice compuesto (player_name, player_sofascore_id, serve_stats_fetched)
        from datetime import date as _date

        from src.database.database import db as _db
        row = _db.execute_sql(
            """
            SELECT
                SUM(CASE WHEN player_sofascore_id = 0  THEN 1 ELSE 0 END)              AS n_sack,
                SUM(CASE WHEN player_sofascore_id != 0 AND serve_stats_fetched = 1
                         THEN 1 ELSE 0 END)                                            AS n_ss_stats,
                SUM(CASE WHEN player_sofascore_id != 0 THEN 1 ELSE 0 END)              AS n_ss_total,
                MAX(CASE WHEN player_sofascore_id = 0  THEN fecha ELSE NULL END)        AS last_sack_date
            FROM sofascorematchcache
            WHERE player_name = ?
            """,
            (nombre,)
        ).fetchone()
        n_sack      = row[0] or 0
        n_ss_stats  = row[1] or 0
        n_ss_total  = row[2] or 0
        last_sack_s = row[3]  # 'YYYY-MM-DD' str o None

        # Días desde el último partido registrado en Sackmann
        try:
            days_inactive = (_date.today() - _date.fromisoformat(last_sack_s)).days
        except (TypeError, ValueError):
            days_inactive = 0

        # sack_ok: los datos Sackmann están "terminados" si...
        #   a) tiene ≥ years*25 partidos (cobertura completa)
        #   b) tiene algo de Sackmann pero lleva >365d sin nuevos → CSV no dará más
        #   c) no tiene datos Sackmann pero SÍ tiene partidos Sofascore (no lo cubre el CSV)
        sack_ok = (
            n_sack >= (years * 25)                  # a)
            or (n_sack > 0 and days_inactive > 365) # b)
            or (n_sack == 0 and n_ss_total > 0)     # c)
        )
        # ss_ok: los datos Sofascore están "terminados" si...
        #   a) tenemos ≥ n_delta matches SS con stats (caso ideal)
        #   b) todos los SS matches disponibles ya tienen stats (jugador inactivo con pocos)
        #   c) player sin Sofascore ID → no hay más que buscar
        #   d) 0 SS rows pero inactivo/retirado (último partido Sackmann > 1 año atrás)
        ss_ok = (
            n_ss_stats >= n_delta                               # a)
            or (n_ss_total > 0 and n_ss_stats == n_ss_total)   # b)
            or ss_id == 0                                       # c)
            or (n_ss_total == 0 and days_inactive > 365)        # d)
        )
        if sack_ok and ss_ok:
            reason = (
                f"SS:{n_ss_stats}/{n_ss_total}"
                + (f" inactivo {days_inactive}d" if n_ss_total == 0 and days_inactive > 365 else "")
            )
            print(f"  ✓ Completo (Sackmann:{n_sack}, {reason}) — saltando")
            return (0, 0)

    if dry_run:
        print(f"  [DRY-RUN] Saltando fetch para '{nombre}'")
        return (0, 0)

    # ── 1. Sackmann bulk histórico ────────────────────────────────────────
    print(f"  [SACKMANN] Cargando {years} años de historial…")
    sack = SackmannScraper()

    antes_sack = (
        SofascoreMatchCache
        .select()
        .where(SofascoreMatchCache.player_name == nombre)
        .where(SofascoreMatchCache.player_sofascore_id == 0)
        .count()
    )

    sack_rows = sack.populate_cache(nombre, years=years)
    nuevos_sack = max(0, len(sack_rows) - antes_sack)
    print(f"  [SACKMANN] +{nuevos_sack} nuevos (total Sackmann: {len(sack_rows)})")

    # ── 2. Sofascore delta (partidos recientes 2025) ──────────────────────
    nuevos_ss = 0
    if ss_id and ss_id > 0 and n_delta > 0:
        print(f"  [SOFASCORE] Delta reciente ({n_delta} partidos)…")
        antes_ss = (
            SofascoreMatchCache
            .select()
            .where(SofascoreMatchCache.player_sofascore_id == ss_id)
            .count()
        )
        try:
            ss_rows = _sofascore_sync_matches(
                player_name=nombre,
                pid=ss_id,
                sc=sc,
                target=n_delta,
                categories=["ATP", "WTA", "Challenger"],
            )
            nuevos_ss = max(0, len(ss_rows) - antes_ss)
            print(f"  [SOFASCORE] +{nuevos_ss} nuevos (total SS: {len(ss_rows)})")

            # Fetch stats por partido para los que aún no las tienen
            pending_stats = [
                r for r in ss_rows
                if not getattr(r, 'serve_stats_fetched', False)
                or not getattr(r, 'bp_stats_fetched', False)
            ]
            if pending_stats:
                print(f"  [STATS] Fetching stats para {len(pending_stats)} partidos SS…")
                _sofascore_sync_bp_stats(pending_stats, sc)
            else:
                print("  [STATS] Stats SS ya presentes.")
        except Exception as exc:
            print(f"  ⚠ Sofascore delta falló: {exc}")
    elif n_delta == 0:
        print("  [SOFASCORE] --delta=0, omitiendo.")
    else:
        print("  [SOFASCORE] Sin Sofascore ID — omitiendo delta.")

    total = nuevos_sack + nuevos_ss
    return (total, 0)   # stats ya incluidas en Sackmann CSV


# ── Reconstrucción ELO ────────────────────────────────────────────────────────

def _rebuild_elo(verbose: bool = True) -> int:
    from src.core.math.elo_engine import EloEngine
    _section("Reconstruyendo ratings ELO")
    engine = EloEngine()
    processed = engine.rebuild(fix_ganador=True, verbose=verbose)
    print(f"  Partidos procesados: {processed}")
    return processed


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Backfill top-N ATP/WTA: historial Sackmann + delta Sofascore"
    )
    parser.add_argument("--years",         type=int, default=_DEFAULT_YEARS,
                        help=f"Años de historial Sackmann (default: {_DEFAULT_YEARS})")
    parser.add_argument("--delta",         type=int, default=_DEFAULT_DELTA,
                        help=f"Partidos recientes 2025 desde Sofascore + stats por partido (default: {_DEFAULT_DELTA}). "
                             "0 = solo Sackmann.")
    parser.add_argument("--top",           type=int, default=_DEFAULT_TOP_N,
                        help=f"Cuántos del ranking considerar (default: {_DEFAULT_TOP_N})")
    parser.add_argument("--tour",          type=str, default=None,
                        choices=["atp", "wta", "both"], metavar="TOUR",
                        help="atp | wta | both (default: both)")
    parser.add_argument("--skip-complete", action="store_true",
                        help="Saltar jugadores que ya tienen suficientes registros")
    parser.add_argument("--no-elo",        action="store_true",
                        help="No reconstruir ELO al finalizar")
    parser.add_argument("--elo-only",      action="store_true",
                        help="Solo rebuild ELO, sin fetch")
    parser.add_argument("--limit",         type=int, default=None,
                        help="Procesar solo los primeros N jugadores (debug)")
    parser.add_argument("--dry-run",       action="store_true",
                        help="Mostrar qué se haría sin hacer fetch real")
    args = parser.parse_args()

    # ── Init DB ──────────────────────────────────────────────────────────
    from src.database.database import db, init_db
    init_db()
    db.connect(reuse_if_open=True)

    # ── Solo ELO ─────────────────────────────────────────────────────────
    if args.elo_only:
        _rebuild_elo()
        return

    # ── Verificar proxy (solo para delta Sofascore) ───────────────────────
    from src.core.config import BRIGHTDATA_PROXY
    if args.delta > 0:
        if BRIGHTDATA_PROXY:
            print(f"\n[PROXY] Brightdata activo (para delta SS): {BRIGHTDATA_PROXY[:45]}…")
        else:
            print("\n[PROXY] ⚠ Sin proxy — delta Sofascore puede ser bloqueado.")
            print("         Configura BRIGHTDATA_PROXY en .env, o usa --delta 0.")

    # ── Instancia Sofascore (solo para delta) ────────────────────────────
    from src.scrapers.sofascore_scraper import SofascoreScraper
    sc = SofascoreScraper()

    # ── Cargar jugadores ─────────────────────────────────────────────────
    tour_arg = args.tour if args.tour and args.tour != "both" else None
    players = _load_top_players(args.top, tour_arg)

    if args.limit:
        players = players[: args.limit]

    atp_cnt = sum(1 for _, t, _, _ in players if t == "ATP")
    wta_cnt = sum(1 for _, t, _, _ in players if t == "WTA")

    _section(f"Backfill Top {args.top} ATP + WTA  (Sackmann + Sofascore delta)")
    print(f"  Jugadores cargados:  {len(players)}  (ATP: {atp_cnt}  WTA: {wta_cnt})")
    print(f"  Años Sackmann:       {args.years}")
    print(f"  Delta Sofascore:     {args.delta} partidos recientes")
    print(f"  Skip completos:      {'Sí' if args.skip_complete else 'No'}")
    print(f"  Dry-run:             {'Sí' if args.dry_run else 'No'}")
    print()

    total_nuevos_partidos = 0
    skipped               = 0
    failed                = 0

    t_start = time.time()

    for i, (nombre, tour, ranking, db_ss_id) in enumerate(players, 1):
        print(f"\n{_bar(i-1, len(players))}  [{tour}] #{ranking} {nombre}")

        # ── Resolver Sofascore ID ─────────────────────────────────────
        ss_id = _resolve_ss_id(nombre, db_ss_id, sc)
        if not ss_id:
            print("  ✗ Sin Sofascore ID — saltando")
            failed += 1
            continue

        # ── Fetch partidos + stats ────────────────────────────────────
        try:
            nuevos, _ = _fetch_player(
                nombre        = nombre,
                ss_id         = ss_id,
                years         = args.years,
                n_delta       = args.delta,
                sc            = sc,
                skip_complete = args.skip_complete,
                dry_run       = args.dry_run,
            )
            total_nuevos_partidos += nuevos

            if nuevos == 0 and args.skip_complete:
                skipped += 1

        except Exception as exc:
            print(f"  ✗ Error: {exc}")
            failed += 1

        # ── ETA ──────────────────────────────────────────────────────
        elapsed  = time.time() - t_start
        per_item = elapsed / i
        remaining = per_item * (len(players) - i)
        print(f"  ⏱ ~{remaining/60:.1f} min restantes")

    # ── Resumen ──────────────────────────────────────────────────────────
    _section("Resumen")
    elapsed_total = time.time() - t_start
    print(f"  Jugadores procesados: {len(players) - failed - skipped}")
    print(f"  Saltados (completos): {skipped}")
    print(f"  Fallidos (sin ID):    {failed}")
    print(f"  Nuevos partidos:      {total_nuevos_partidos}")
    print(f"  Tiempo total:         {elapsed_total/60:.1f} min")

    # ── Rebuild ELO ──────────────────────────────────────────────────────
    if not args.no_elo and not args.dry_run:
        _rebuild_elo()

    print("\n  ✓ Backfill completado.\n")


if __name__ == "__main__":
    main()
