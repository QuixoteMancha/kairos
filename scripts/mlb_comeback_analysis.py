"""Análisis de remontadas en MLB -- 2026-08-04, pedido explícito del
usuario: "me interesa principalmente MLB porque pienso que la
probabilidad de remontadas es baja, los jugadores a pesar de ser
profesionales no le atinan mucho con el bate". Arranca por la opción 1
(sin cuotas reales todavía, ver docstring de
`outer_apis/scrapers/baseball/mlb_stats_scraper.py::get_linescore`).

EQUIVALENTE a "ganar/perder el set 1" en tenis: quién va ganando
ACUMULADO al cierre de la 5ta entrada -- checkpoint real y estándar en
MLB (existe un mercado de apuestas real llamado "First 5 Innings" /
"F5" basado justo en este corte), análogo natural al primer set.

FUENTE: `statsapi.mlb.com` oficial, JSON real, sin scraping HTML ni
proxy (ver `mlb_stats_scraper.py`). Cada partido requiere 1 request
extra al linescore -- con miles de partidos por temporada, necesita
throttle explícito (mismo criterio que el resto de backfills masivos
de esta sesión)."""

from __future__ import annotations

import json
import time
from datetime import date
from typing import Any

from outer_apis.scrapers.baseball import mlb_stats_scraper

_PAUSE_BETWEEN_REQUESTS_S = 0.3
_F5_CHECKPOINT_INNING = 5


def _cumulative_runs_through_inning(linescore: dict[str, Any], inning: int) -> tuple[int, int] | None:
    """(home_runs, away_runs) acumulados hasta el cierre de `inning`
    inclusive -- fail-closed: None si el partido no llegó a jugar esa
    entrada completa (ej. terminó antes por lluvia/formato acortado,
    caso real que hay que descartar, no adivinar)."""
    home = away = 0
    innings_seen = set()
    for inn in linescore["innings"]:
        if inn["num"] is None or inn["num"] > inning:
            continue
        if inn["home_runs"] is None or inn["away_runs"] is None:
            return None
        home += inn["home_runs"]
        away += inn["away_runs"]
        innings_seen.add(inn["num"])
    if inning not in innings_seen:
        return None
    return home, away


def collect_games_with_linescore(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Partidos reales en el rango, CADA UNO con su linescore real
    (throttle explícito -- ver `_PAUSE_BETWEEN_REQUESTS_S`). Fail-closed
    por partido: si el linescore falla o no llega a la 5ta entrada
    completa, ese partido se descarta (no se fabrica)."""
    games = mlb_stats_scraper.get_games_in_range(start_date, end_date)
    result = []
    for g in games:
        game_pk = g.get("game_pk")
        if game_pk is None:
            continue
        linescore = mlb_stats_scraper.get_linescore(game_pk)
        time.sleep(_PAUSE_BETWEEN_REQUESTS_S)
        if linescore is None:
            continue
        through_f5 = _cumulative_runs_through_inning(linescore, _F5_CHECKPOINT_INNING)
        if through_f5 is None:
            continue
        result.append({
            "date": g["date"], "home_team": g["home_team"], "away_team": g["away_team"],
            "home_won": g["home_won"],
            "home_runs_f5": through_f5[0], "away_runs_f5": through_f5[1],
            "home_runs_final": linescore["home_runs_total"], "away_runs_final": linescore["away_runs_total"],
        })
    return result


def analyze_comeback_rate(games: list[dict[str, Any]]) -> dict[str, Any]:
    """Equivalente al análisis de tenis: de los partidos donde alguien
    iba PERDIENDO al cierre de la 5ta entrada, ¿qué % remonta y termina
    ganando el partido? Empates al cierre de la 5ta se excluyen (no hay
    'perdedor' definido en ese checkpoint, fail-closed)."""
    trailing_cases = []
    for g in games:
        if g["home_runs_f5"] == g["away_runs_f5"]:
            continue  # empatados a la F5, no hay quién esté "perdiendo"
        home_trailing = g["home_runs_f5"] < g["away_runs_f5"]
        trailing_won = (g["home_won"]) if home_trailing else (not g["home_won"])
        trailing_cases.append({**g, "trailing_won": trailing_won})

    n = len(trailing_cases)
    comebacks = sum(1 for c in trailing_cases if c["trailing_won"])

    # desglose por margen de diferencia a la F5 -- ¿importa cuánto perdía?
    by_margin: dict[str, dict[str, Any]] = {}
    for c in trailing_cases:
        margin = abs(c["home_runs_f5"] - c["away_runs_f5"])
        label = f"{margin}" if margin < 4 else "4+"
        b = by_margin.setdefault(label, {"n": 0, "comebacks": 0})
        b["n"] += 1
        b["comebacks"] += 1 if c["trailing_won"] else 0
    for label, b in by_margin.items():
        b["comeback_rate_pct"] = round(b["comebacks"] / b["n"] * 100, 2) if b["n"] else None

    return {
        "total_games": len(games),
        "games_tied_at_f5_excluded": len(games) - n,
        "trailing_at_f5_n": n,
        "comeback_wins": comebacks,
        "comeback_rate_pct": round(comebacks / n * 100, 2) if n else None,
        "by_margin_at_f5": by_margin,
        "caveat": (
            "Checkpoint = carreras acumuladas al CIERRE de la 5ta entrada "
            "(equivalente al 'set 1' de tenis, mismo corte que el mercado "
            "real 'First 5 Innings'). Sin precio de mercado real todavía "
            "(Opción 1 -- ver docstring del módulo); esto es solo la tasa "
            "de remontada real observada, no un backtest de estrategia."
        ),
    }


if __name__ == "__main__":
    import sys

    start = sys.argv[1] if len(sys.argv) > 1 else f"{date.today().year - 1}-01-01"
    end = sys.argv[2] if len(sys.argv) > 2 else f"{date.today().year - 1}-12-31"
    games = collect_games_with_linescore(start, end)
    result = analyze_comeback_rate(games)
    print(json.dumps(result, indent=2, ensure_ascii=False))
