"""Tennis volatility via a point-level Markov chain + Monte Carlo (see
`tennis_markov.py` in this package) instead of the generic price/time
heuristic used by the other sports (see `_base.py`) — tennis is the one
sport in this package with a mathematically tractable scoring structure
(game/set/match derived analytically from p_serve), so it gets a dedicated
model rather than sharing `_SportVModelBase`.

EMPIRICAL VOLATILITY (2026-07-31, pedido explícito del usuario): con
`TennisScannedMatch`/`TennisMatchProbabilityTick` (ver
`database/historical/tennis/matchtracking.py` y el worker
`tennis_match_tick_worker.py`) ya registrando TODO partido escaneado con
su camino de precio real tick-by-tick, ahora se puede calcular
volatilidad HISTÓRICA REAL (no simulada) para partidos con perfil similar
(mismo tour+superficie+bucket de ranking favorita/no-favorita, ver
`outer_apis/sports/tennis/volatility_calibration.py`), en vez de asumir
siempre la simulación Markov/Monte Carlo.

Se intenta PRIMERO el camino empírico; si no hay `MIN_MATCHES_FOR_STAT`
partidos históricos reales con ese perfil exacto (fail-closed, esperable
casi siempre al principio -- la colección de ticks recién arrancó
2026-07-31), cae a la simulación de siempre, sin cambios. Nunca deja de
producir un número por falta de historia real."""

from models.volatility import safe_price, clamp01
from .tennis_markov import p_serve_for_rank, simulate_win_prob_volatility


class TennisVModel:
    """Does NOT inherit `_SportVModelBase`: it has no time-to-resolution
    input and no TIME_HORIZON_HOURS concept — the Markov/Monte Carlo
    engine's output already encodes "how much can the win probability
    move", derived from score-state dynamics, not from a generic
    time-decay heuristic."""

    def calculate_volatility(self, market: dict) -> float:
        price_a = safe_price(market, "current_a_price")
        price_b = safe_price(market, "current_b_price")
        if price_a is None or price_b is None:
            return 0.0

        rank_a = market.get("player_a_rank")
        rank_b = market.get("player_b_rank")
        # Fail-closed: rankings must be present and positive integers. Never
        # fabricate a ranking for a missing/invalid value.
        if not isinstance(rank_a, int) or isinstance(rank_a, bool) or rank_a <= 0:
            return 0.0
        if not isinstance(rank_b, int) or isinstance(rank_b, bool) or rank_b <= 0:
            return 0.0

        best_of = market.get("best_of", 3)
        if best_of not in (3, 5):
            return 0.0

        # Optional: "ATP" or "WTA" selects the real-calibrated p_serve
        # table for that tour (see tennis_markov.py); missing/unrecognized
        # falls back to the generic static placeholder rather than
        # guessing which tour's curve applies.
        tour = market.get("tour")
        surface = market.get("surface")

        empirical = self._empirical_volatility(rank_a, rank_b, surface, tour)
        if empirical is not None:
            return clamp01(empirical)

        # Bug real encontrado y arreglado 2026-07-31: esta llamada nunca
        # pasaba `surface` pese a que p_serve_for_rank ya lo soporta desde
        # el 2026-07-30 (fallback surface -> tour-genérico -> estático) --
        # quedó desalineada al extender esa firma. Ahora sí se pasa.
        pa_serve = p_serve_for_rank(rank_a, tour=tour, surface=surface)
        pb_serve = p_serve_for_rank(rank_b, tour=tour, surface=surface)
        volatility = simulate_win_prob_volatility(pa_serve, pb_serve, best_of=best_of)
        return clamp01(volatility)

    @staticmethod
    def _empirical_volatility(rank_a: int, rank_b: int, surface: str | None, tour: str | None) -> float | None:
        """Fail-closed a None ante CUALQUIER problema (incluida la
        importación misma, que toca DB) -- la volatilidad simulada nunca
        debe dejar de calcularse por un fallo en la consulta empírica."""
        try:
            from outer_apis.sports.tennis.volatility_calibration import get_empirical_volatility_for_matchup

            result = get_empirical_volatility_for_matchup(rank_a, rank_b, surface, tour)
            return result["mean_volatility"] if result is not None else None
        except Exception:
            return None
