"""Elo AUTO-CALCULADO a partir de TML-Database, para resolver el
problema de lookahead identificado en `favorite_by_historical_rank_backtest.py`
(`model_fair_price_calibration`) -- pedido explícito del usuario
2026-08-03: "habría que construir un modelo que pueda aproximar las
probabilidades pre-match... investigar si hay más data de jugadores
antiguos".

HALLAZGO DE LA INVESTIGACIÓN (2026-08-03): NO existe una fuente pública
de Elo HISTÓRICO point-in-time de Tennis Abstract/Jeff Sackmann (su
página solo publica el snapshot ACTUAL, ver `tennisabstract_elo.py`) --
buscado explícitamente, no encontrado (ver memoria de esta sesión).
PERO no hace falta: TML-Database YA cubre 1968-2026 (no solo 2016-2026,
el recorte que usa `favorite_by_historical_rank_backtest.py` por
alcance de la pregunta original) -- eso es MÁS que suficiente data
cruda para calcular nuestro PROPIO Elo, partido a partido, en orden
cronológico real. Un Elo auto-calculado así es POR CONSTRUCCIÓN
point-in-time correcto (el rating de un jugador en la fecha X solo usa
resultados ANTERIORES a X) -- cero riesgo de lookahead, sin necesitar
ninguna fuente ni bio de jugador adicional. La "data de jugadores
antiguos" que pedía investigar el usuario YA la tenemos, en la misma
fuente que ya usamos (TML-Database), simplemente no la estábamos
aprovechando por empezar el backtest en 2016.

DISEÑO:
- Warm-up: procesa partidos desde `warmup_start_year` (default 2000,
  16 años de historia antes de la ventana de evaluación) para que los
  jugadores ya tengan un Elo acumulado real al llegar a 2016 -- sin
  warm-up, todos empezarían parejos en 1500 justo cuando arranca la
  ventana de evaluación, lo cual sería un cold-start artificial.
- Evaluación: igual que el backtest original, 2016-2026, mismos niveles
  de torneo (250+).
- Fórmula Elo estándar (K=32, logística base 400 -- misma constante que
  ya usa `tennisabstract_elo.py::elo_implied_win_prob`, reutilizada acá
  para no duplicar la fórmula).
- Compara 3 fair prices por partido en la ventana de evaluación:
  1. `base_fair_favorite`: motor Markov puro (ranking+superficie), igual
     que `_model_fair_price_calibration`.
  2. `elo_fair_favorite`: SOLO el Elo auto-calculado.
  3. `blended_fair_favorite`: blend 70% base / 30% Elo -- mismo peso
     relativo que `TENNISABSTRACT_ELO_WEIGHT` en `TennisFModel`, para
     comparar manzanas con manzanas contra el diseño real de producción.

Jugadores sin historial previo dentro de la ventana de warm-up (debut)
arrancan en `_INITIAL_ELO` (1500) -- sesgo conocido y aceptado (mismo
tipo de cold-start que cualquier sistema Elo real tiene para debutantes,
no es específico de este backtest)."""

from __future__ import annotations

import json
from typing import Any

from outer_apis.scrapers.tennis.tml_database_scraper import get_matches_range, TOUR_LEVELS_250_PLUS
from outer_apis.sports.tennis.tennisabstract_elo import elo_implied_win_prob
from models.volatility.sports.tennis.tennis_markov import pre_match_win_prob

_WARMUP_START_YEAR = 2000
_EVAL_START_YEAR = 2016
_DEFAULT_END_YEAR = 2026

_INITIAL_ELO = 1500.0
_K_FACTOR = 32.0

# Mismo peso que TENNISABSTRACT_ELO_WEIGHT en
# models/fairprice/sports/tennis/tennisfmodel.py -- comparación directa
# contra el diseño real de producción, no un número nuevo inventado.
_ELO_BLEND_WEIGHT = 0.30

_FAIR_PRICE_BUCKETS = [
    (0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 0.75),
    (0.75, 0.80), (0.80, 0.85), (0.85, 0.90), (0.90, 1.01),
]


def _elo_update(elo_winner: float, elo_loser: float, k: float = _K_FACTOR) -> tuple[float, float]:
    """Fórmula Elo estándar: el ganador siempre suma, el perdedor
    siempre resta, en cantidades espejadas (suma cero)."""
    expected_winner = elo_implied_win_prob(elo_winner, elo_loser)
    delta = k * (1.0 - expected_winner)
    return elo_winner + delta, elo_loser - delta


def _bucket_label(low: float, high: float) -> str:
    return f"{low:.2f}-{min(high, 1.0):.2f}"


def _empty_buckets() -> dict[str, dict[str, Any]]:
    return {
        _bucket_label(low, high): {"n": 0, "hits": 0, "fair_sum": 0.0}
        for low, high in _FAIR_PRICE_BUCKETS
    }


def _add_to_bucket(buckets: dict[str, dict[str, Any]], fair_a: float, hit: bool) -> None:
    for low, high in _FAIR_PRICE_BUCKETS:
        if low <= fair_a < high:
            b = buckets[_bucket_label(low, high)]
            b["n"] += 1
            b["hits"] += 1 if hit else 0
            b["fair_sum"] += fair_a
            return


def _finalize_buckets(buckets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for label, b in buckets.items():
        n = b["n"]
        result[label] = {
            "n": n,
            "avg_fair_a": round(b["fair_sum"] / n, 4) if n else None,
            "actual_hit_rate": round(b["hits"] / n, 4) if n else None,
        }
    return result


def run_self_computed_elo_backtest(
    warmup_start_year: int = _WARMUP_START_YEAR,
    eval_start_year: int = _EVAL_START_YEAR,
    end_year: int = _DEFAULT_END_YEAR,
) -> dict[str, Any]:
    matches = get_matches_range(warmup_start_year, end_year, levels=TOUR_LEVELS_250_PLUS)
    matches.sort(key=lambda m: m["date"])  # ISO -> orden lexicográfico = orden cronológico real

    elo: dict[str, float] = {}
    eval_cutoff = f"{eval_start_year}-01-01"

    base_buckets = _empty_buckets()
    elo_buckets = _empty_buckets()
    blended_buckets = _empty_buckets()
    eval_n = warmup_only_n = skipped_no_rank = skipped_no_bestof = 0

    for m in matches:
        winner_name, loser_name = m["winner_name"], m["loser_name"]
        elo_w = elo.get(winner_name, _INITIAL_ELO)
        elo_l = elo.get(loser_name, _INITIAL_ELO)

        if m["date"] < eval_cutoff:
            warmup_only_n += 1
        else:
            if m["winner_rank"] is None or m["loser_rank"] is None:
                skipped_no_rank += 1
            else:
                favorite_is_winner = m["winner_rank"] < m["loser_rank"]
                favorite_rank = min(m["winner_rank"], m["loser_rank"])
                underdog_rank = max(m["winner_rank"], m["loser_rank"])
                elo_favorite = elo_w if favorite_is_winner else elo_l
                elo_underdog = elo_l if favorite_is_winner else elo_w
                hit = favorite_is_winner

                elo_fair_favorite = elo_implied_win_prob(elo_favorite, elo_underdog)
                _add_to_bucket(elo_buckets, elo_fair_favorite, hit)

                best_of = m.get("best_of") or 3
                if best_of not in (3, 5):
                    skipped_no_bestof += 1
                else:
                    base_fair_favorite = pre_match_win_prob(
                        favorite_rank, underdog_rank, best_of=best_of,
                        tour="ATP", surface=m.get("surface"),
                    )
                    blended_fair_favorite = (
                        (1 - _ELO_BLEND_WEIGHT) * base_fair_favorite + _ELO_BLEND_WEIGHT * elo_fair_favorite
                    )
                    _add_to_bucket(base_buckets, base_fair_favorite, hit)
                    _add_to_bucket(blended_buckets, blended_fair_favorite, hit)
                    eval_n += 1

        # Actualizar Elo DESPUÉS de usarlo para el fair price de arriba
        # -- point-in-time correcto: este resultado recién ahora pasa a
        # formar parte del rating "conocido" para partidos futuros.
        new_w, new_l = _elo_update(elo_w, elo_l)
        elo[winner_name] = new_w
        elo[loser_name] = new_l

    return {
        "warmup_years": f"{warmup_start_year}-{eval_start_year - 1}",
        "eval_years": f"{eval_start_year}-{end_year}",
        "warmup_matches": warmup_only_n, "eval_matches_with_full_data": eval_n,
        "eval_matches_skipped_no_rank": skipped_no_rank,
        "eval_matches_skipped_invalid_best_of": skipped_no_bestof,
        "elo_blend_weight_used": _ELO_BLEND_WEIGHT,
        "base_model_calibration": _finalize_buckets(base_buckets),
        "elo_only_calibration": _finalize_buckets(elo_buckets),
        "blended_calibration": _finalize_buckets(blended_buckets),
        "caveat": (
            "Elo 100% AUTO-CALCULADO desde TML-Database (K=32, logística "
            "base 400) -- point-in-time correcto por construcción, sin "
            "ninguna fuente/bio externa nueva. Jugadores sin historial en "
            "la ventana de warm-up arrancan en 1500 (cold-start conocido, "
            "sesga levemente a debutantes hacia 50/50). No confundir con "
            "el Elo de Tennis Abstract (`tennisabstract_elo.py`), que es "
            "un rating distinto, calculado por otra metodología, y solo "
            "disponible como snapshot ACTUAL (por eso no se usa para "
            "backtesting histórico, ver `_model_fair_price_calibration`)."
        ),
    }


if __name__ == "__main__":
    print(json.dumps(run_self_computed_elo_backtest(), indent=2, ensure_ascii=False))
