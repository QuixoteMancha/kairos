"""Índice combinado (rendimiento reciente + fatiga acumulada) para los
partidos que quedan del US Open 2026 -- 2026-09-05, pedido explícito del
usuario ("me interesa tener este índice + la fatiga, obteniendo una
expectativa general más precisa basada en data, para los próximos matches
que faltan del US Open").

DOS COMPONENTES, cada uno reportado por separado (NO se fabrica una
probabilidad calibrada única -- no hay una regresión real detrás que
mapee estos z-scores a win rate, sería inventar precisión que no existe):

1. "Rendimiento reciente" -- z-score compuesto (relativo a la población
   del mismo tour) de los últimos `_RECENT_N` partidos con `MatchStats`
   real: diferencial winners-UE, % de puntos ganados al saque (promedio
   simple 1er/2do servicio), aces, y break points convertidos (pedido
   explícito del usuario: "comparar winners vs UE, rendimiento de
   servicio y puntos ganados de servicio + aces" + break points
   convertidos, sugerido y aceptado en esta misma sesión).

2. "Fatiga acumulada" -- minutos acumulados jugados por cada jugador en
   ESTE torneo hasta ahora, fetcheados EN VIVO vía
   `flashscore_scraper.get_match_duration()` sobre sus partidos YA
   resueltos del propio `Match` tracker (`tennis_scanned_match`) dentro
   de los últimos `_TOURNAMENT_WINDOW_DAYS` días (heurística: no hay
   forma confiable de identificar "este torneo" por `Match.tournament`,
   que viene NULL la mayoría de las veces en partidos del Grand Slam
   actual -- ver docstring de `_find_prior_tournament_matches`). El
   "penalty" en puntos porcentuales de WR es una interpolación de la
   curva empírica encontrada en esta misma sesión sobre `MatchHistory`
   real (favoritos por ranking, WR cae de ~67% a ~58% entre 0 y 400+
   minutos acumulados) -- una heurística basada en datos reales, NO una
   regresión ajustada.

Fail-closed en todo: un jugador sin suficiente historial de stats reales
(`_RECENT_N` no alcanzado) reporta `None` en `recent_form` en vez de un
promedio con muestra insuficiente; sin duración conocida de un partido
previo, ese partido simplemente no suma a la fatiga (no se descarta el
jugador entero, a diferencia del backtest de fatiga que sí exige el
acumulado COMPLETO -- acá el objetivo es una estimación hacia adelante,
no una serie point-in-time limpia)."""

from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from peewee import JOIN

from database.historical.tennis.models import Match, MatchHistory, MatchStats
from outer_apis.scrapers.tennis.flashscore_scraper import get_match_duration

_RECENT_N = 10
_TOURNAMENT_WINDOW_DAYS = 14
# Estadísticas usadas por jugador, en el nombre real de la columna
# tipada de `MatchStats` (sufijo _a/_b según orientación).
_METRICS = ("winner_ue_diff", "serve_pts_won_avg", "aces", "break_points_won")

# Interpolación lineal por tramos de la curva empírica real (favoritos
# por ranking, ver sesión 2026-09-05): (minutos_acumulados, penalty_pp).
_FATIGUE_CURVE = [
    (0, 0.0), (60, 3.2), (120, 5.7), (180, 6.1),
    (240, 9.0), (300, 8.5), (400, 9.0), (10_000, 9.0),
]


def _fatigue_penalty_points(cum_minutes: float) -> float:
    """Puntos porcentuales de WR esperables de "perder" por fatiga
    acumulada, interpolados linealmente sobre `_FATIGUE_CURVE`."""
    for (x0, y0), (x1, y1) in zip(_FATIGUE_CURVE, _FATIGUE_CURVE[1:]):
        if x0 <= cum_minutes <= x1:
            if x1 == x0:
                return y0
            frac = (cum_minutes - x0) / (x1 - x0)
            return y0 + frac * (y1 - y0)
    return _FATIGUE_CURVE[-1][1]


@dataclass
class PlayerSnapshot:
    player_id: int
    name: str
    recent_form: dict[str, float] | None = None
    recent_form_n: int = 0
    form_z: float | None = None
    cum_minutes: float = 0.0
    fatigue_matches_used: int = 0
    fatigue_matches_missing_duration: int = 0
    fatigue_penalty: float = 0.0


def _population_stats(tour: str) -> dict[str, tuple[float, float]]:
    """Media/desvío de cada métrica sobre TODA la población de ese tour
    con `MatchStats` real -- para poder z-scorear un jugador puntual de
    forma comparable. Cada partido aporta 2 observaciones (A y B)."""
    rows = (MatchStats
            .select(MatchStats, MatchHistory.tour)
            .join(MatchHistory, JOIN.INNER, on=(MatchStats.match_history == MatchHistory.id))
            .where(MatchHistory.tour == tour))

    values: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        for suffix in ("a", "b"):
            m = _metrics_for_side(row, suffix)
            for k, v in m.items():
                if v is not None:
                    values[k].append(v)

    stats: dict[str, tuple[float, float]] = {}
    for metric, vals in values.items():
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        stats[metric] = (mean, var ** 0.5)
    return stats


def _metrics_for_side(row: MatchStats, suffix: str) -> dict[str, float | None]:
    winners = getattr(row, f"winners_{suffix}")
    ue = getattr(row, f"unforced_errors_{suffix}")
    first_spw = getattr(row, f"first_serve_points_won_pct_{suffix}")
    second_spw = getattr(row, f"second_serve_points_won_pct_{suffix}")
    aces = getattr(row, f"aces_{suffix}")
    bp_won = getattr(row, f"break_points_won_{suffix}")

    winner_ue_diff = (winners - ue) if (winners is not None and ue is not None) else None
    spw_vals = [v for v in (first_spw, second_spw) if v is not None]
    serve_pts_won_avg = (sum(spw_vals) / len(spw_vals)) if spw_vals else None

    return {
        "winner_ue_diff": winner_ue_diff,
        "serve_pts_won_avg": serve_pts_won_avg,
        "aces": float(aces) if aces is not None else None,
        "break_points_won": float(bp_won) if bp_won is not None else None,
    }


def _recent_form(player_id: int, tour: str) -> tuple[dict[str, float] | None, int]:
    """Promedio de los últimos `_RECENT_N` partidos CON `MatchStats`
    real de `player_id`, orientado a/b según corresponda. Fail-closed:
    `None` si no hay ni un partido usable."""
    rows = (MatchStats
            .select(MatchStats, MatchHistory)
            .join(MatchHistory, JOIN.INNER, on=(MatchStats.match_history == MatchHistory.id))
            .where(((MatchHistory.player_a == player_id) | (MatchHistory.player_b == player_id))
                   & MatchHistory.match_date.is_null(False))
            .order_by(MatchHistory.match_date.desc())
            .limit(_RECENT_N))

    per_metric: dict[str, list[float]] = defaultdict(list)
    n_used = 0
    for row in rows:
        suffix = "a" if row.match_history.player_a_id == player_id else "b"
        m = _metrics_for_side(row, suffix)
        if all(v is None for v in m.values()):
            continue
        n_used += 1
        for k, v in m.items():
            if v is not None:
                per_metric[k].append(v)

    if n_used == 0:
        return None, 0

    avg = {k: sum(v) / len(v) for k, v in per_metric.items() if v}
    return avg, n_used


def _composite_z(avg: dict[str, float], pop_stats: dict[str, tuple[float, float]]) -> float | None:
    zs = []
    for metric in _METRICS:
        if metric not in avg or metric not in pop_stats:
            continue
        mean, std = pop_stats[metric]
        if std == 0:
            continue
        zs.append((avg[metric] - mean) / std)
    return sum(zs) / len(zs) if zs else None


def _find_prior_tournament_matches(player_id: int, player_name: str) -> list[Match]:
    """Partidos YA resueltos de este jugador en el `Match` tracker
    dentro de la ventana de `_TOURNAMENT_WINDOW_DAYS` días -- heurística
    para "sus partidos previos de este mismo torneo" dado que
    `Match.tournament` viene NULL la mayoría de las veces para el Grand
    Slam en curso (verificado en vivo 2026-09-05: 0 de los 151 partidos
    abiertos tenían `tournament` poblado). Se filtra por
    `match_history.player_a/b_id` (FK ya resuelta, no matching fuzzy de
    nombre) -- todos los `Match` abiertos verificados tienen
    `match_history` seteado."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_TOURNAMENT_WINDOW_DAYS)
    rows = (Match
            .select(Match, MatchHistory)
            .join(MatchHistory, JOIN.INNER, on=(Match.match_history == MatchHistory.id))
            .where(Match.winner.is_null(False)
                   & ((MatchHistory.player_a == player_id) | (MatchHistory.player_b == player_id))
                   & (Match.detected_at >= cutoff)))
    return list(rows)


def _fatigue(player_id: int, player_name: str, duration_cache: dict[str, int | None]) -> tuple[float, int, int]:
    prior = _find_prior_tournament_matches(player_id, player_name)
    cum_minutes = 0.0
    used = 0
    missing = 0
    for m in prior:
        fsid = m.flashscore_match_id
        if not fsid:
            missing += 1
            continue
        if fsid not in duration_cache:
            result = get_match_duration(fsid)
            duration_cache[fsid] = result["total_minutes"] if result else None
        minutes = duration_cache[fsid]
        if minutes is None:
            missing += 1
            continue
        cum_minutes += minutes
        used += 1
    return cum_minutes, used, missing


def build_snapshot(player_id: int, name: str, tour: str,
                    pop_stats_cache: dict[str, dict], duration_cache: dict) -> PlayerSnapshot:
    snap = PlayerSnapshot(player_id=player_id, name=name)

    avg, n_used = _recent_form(player_id, tour)
    snap.recent_form = avg
    snap.recent_form_n = n_used
    if avg is not None:
        if tour not in pop_stats_cache:
            pop_stats_cache[tour] = _population_stats(tour)
        snap.form_z = _composite_z(avg, pop_stats_cache[tour])

    cum_minutes, used, missing = _fatigue(player_id, name, duration_cache)
    snap.cum_minutes = cum_minutes
    snap.fatigue_matches_used = used
    snap.fatigue_matches_missing_duration = missing
    snap.fatigue_penalty = _fatigue_penalty_points(cum_minutes)

    return snap


def _slam_open_matches() -> list[Match]:
    """Partidos del Grand Slam actualmente en curso que aún no se
    resuelven -- heurística por ronda (Round Of 128/64/32/16,
    Quarterfinal, Semifinal, Final SOLO existen en cuadros de Slam;
    verificado en vivo que hoy es el único Slam activo). `Match.tournament`
    NO se usa como filtro -- viene NULL para casi todos estos partidos."""
    slam_rounds = ("Round Of 128", "Round Of 64", "Round Of 32", "Round Of 16",
                   "Quarterfinal", "Semifinal", "Final")
    return list(Match
                .select(Match, MatchHistory)
                .join(MatchHistory, JOIN.INNER, on=(Match.match_history == MatchHistory.id))
                .where(Match.winner.is_null(True) & Match.round.in_(slam_rounds)))


def main() -> None:
    matches = _slam_open_matches()
    print(f"Partidos abiertos del Slam en curso (heurística por ronda): {len(matches)}\n")

    pop_stats_cache: dict[str, dict] = {}
    duration_cache: dict[str, int | None] = {}
    snapshot_cache: dict[int, PlayerSnapshot] = {}

    for m in matches:
        mh = m.match_history
        pa_id, pb_id = mh.player_a_id, mh.player_b_id
        if pa_id is None or pb_id is None:
            continue

        for pid, pname in ((pa_id, m.player_a_name), (pb_id, m.player_b_name)):
            if pid not in snapshot_cache:
                snapshot_cache[pid] = build_snapshot(pid, pname, m.tour, pop_stats_cache, duration_cache)

        sa, sb = snapshot_cache[pa_id], snapshot_cache[pb_id]

        print(f"=== {m.player_a_name} vs {m.player_b_name} ({m.tour}, {m.round}) ===")
        for s, label in ((sa, m.player_a_name), (sb, m.player_b_name)):
            form_str = f"{s.form_z:+.2f}z (n={s.recent_form_n})" if s.form_z is not None else f"sin datos (n={s.recent_form_n})"
            print(f"  {label:<22} forma_reciente={form_str:<22} "
                  f"fatiga={s.cum_minutes:>5.0f}min (n={s.fatigue_matches_used}, "
                  f"sin_duracion={s.fatigue_matches_missing_duration})  penalty≈-{s.fatigue_penalty:.1f}pp")

        form_diff = None
        if sa.form_z is not None and sb.form_z is not None:
            form_diff = sa.form_z - sb.form_z
        fatigue_diff_pp = sb.fatigue_penalty - sa.fatigue_penalty  # positivo = A más fresco que B

        if form_diff is None:
            reading = "sin suficiente historial de stats para comparar forma reciente"
        else:
            form_favors = m.player_a_name if form_diff > 0 else m.player_b_name
            fatigue_favors = m.player_a_name if fatigue_diff_pp > 0 else m.player_b_name
            if abs(form_diff) < 0.1:
                reading = "forma reciente pareja"
            elif form_favors == fatigue_favors or abs(fatigue_diff_pp) < 1.0:
                reading = f"ambas señales (o fatiga despreciable) favorecen a {form_favors}"
            else:
                reading = (f"señales MIXTAS: forma reciente favorece a {form_favors}, "
                           f"fatiga favorece a {fatigue_favors}")
        print(f"  -> {reading}\n")


if __name__ == "__main__":
    main()
