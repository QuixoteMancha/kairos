#!/usr/bin/env python3
"""
rebuild_elo.py
══════════════
Reconstruye todos los ratings ELO desde los matches históricos en DB.

Uso:
    python3 rebuild_elo.py               # rebuild completo con fix de ganador_id
    python3 rebuild_elo.py --no-fix      # solo ELO, no reparar ganador_id
    python3 rebuild_elo.py --top N       # muestra top N jugadores (default: 30)
    python3 rebuild_elo.py --player "Jannik Sinner"  # detalle de un jugador
"""

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

from src.database.database import init_db
from src.core.math.elo_engine import ELO_START, EloEngine


def show_top(n: int, surface: str = "Overall"):
    """Muestra los N jugadores con mayor ELO en la superficie dada."""
    from src.database.database import Player, PlayerElo
    records = (
        PlayerElo
        .select(PlayerElo, Player)
        .join(Player)
        .where(
            (PlayerElo.surface == surface) &
            (PlayerElo.matches >= 1)
        )
        .order_by(PlayerElo.elo.desc())
        .limit(n)
    )
    print(f"\n── Top {n} ELO [{surface}] ────────────────────────────────────────────────")
    print(f"  {'#':>3}  {'ELO':>7}  {'W':>4}  {'M':>4}  {'WR%':>6}  Jugador")
    print("  " + "─" * 65)
    for i, r in enumerate(records, 1):
        wr = r.wins / r.matches * 100 if r.matches > 0 else 0
        name = r.player.nombre
        if len(name) > 38:
            name = name[:35] + "..."
        print(f"  {i:>3}  {r.elo:>7.1f}  {r.wins:>4}  {r.matches:>4}  {wr:>5.1f}%  {name}")
    print()


def show_player(name: str):
    """Muestra el detalle ELO de un jugador por nombre (fuzzy)."""
    import difflib

    from src.database.database import Player, PlayerElo

    all_players = list(Player.select())
    matches_names = [p.nombre for p in all_players]
    close = difflib.get_close_matches(name, matches_names, n=5, cutoff=0.6)
    if not close:
        print(f"  No se encontró jugador parecido a '{name}'")
        return

    print(f"\n── Jugador: {close[0]} ─────────────────────────────────────────────────")
    player = next(p for p in all_players if p.nombre == close[0])

    for surface in ("Overall", "Hard", "Clay", "Grass"):
        try:
            r = PlayerElo.get(
                (PlayerElo.player_id == player.id) &
                (PlayerElo.surface == surface)
            )
            wr = r.wins / r.matches * 100 if r.matches > 0 else 0
            diff = r.elo - ELO_START
            sign = "+" if diff >= 0 else ""
            print(f"  {surface:<8}  ELO={r.elo:>7.1f}  ({sign}{diff:.0f})  "
                  f"W={r.wins}/{r.matches}  WR={wr:.1f}%  "
                  f"último={r.last_match or 'N/A'}")
        except Exception:
            print(f"  {surface:<8}  sin datos")
    print()


def show_surface_summary():
    """Resumen estadístico por superficie."""
    from src.database.database import PlayerElo
    print("\n── Jugadores con datos por superficie ────────────────────────────────────")
    for surface in ("Overall", "Hard", "Clay", "Grass"):
        cnt   = PlayerElo.select().where(
            (PlayerElo.surface == surface) & (PlayerElo.matches >= 1)
        ).count()
        cnt10 = PlayerElo.select().where(
            (PlayerElo.surface == surface) & (PlayerElo.matches >= 3)
        ).count()
        print(f"  {surface:<8}  {cnt:>5} jugadores (≥1 partido)  "
              f"{cnt10:>5} con ≥3 partidos")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Reconstruye ratings ELO desde match history de Kairos"
    )
    parser.add_argument("--no-fix",  action="store_true", help="No reparar ganador_id")
    parser.add_argument("--top",     type=int, default=30, help="Mostrar top N (default: 30)")
    parser.add_argument("--player",  type=str, default=None, help="Detalle de un jugador")
    parser.add_argument("--surface", type=str, default="Overall",
                        help="Superficie para --top (default: Overall)")
    args = parser.parse_args()

    init_db()

    print("\n" + "═" * 60)
    print("  KAIROS — Rebuild ELO")
    print("═" * 60)

    engine = EloEngine()
    processed = engine.rebuild(
        fix_ganador=not args.no_fix,
        verbose=True,
    )

    print(f"\n  Total procesados: {processed}")

    show_surface_summary()

    if args.player:
        show_player(args.player)
    else:
        show_top(args.top, surface=args.surface)
        # Mostrar también Clay y Hard brevemente
        if args.surface == "Overall":
            show_top(15, surface="Clay")
            show_top(15, surface="Hard")

    print("═" * 60)
    print("  ELO rebuild completado.")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    main()
