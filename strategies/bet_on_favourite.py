# This strategy is simple as the name of the file says, just bet on the favourite and hold the position of the match.
# We add the filters ranking difference (20 for atp and 30 for wta), Favourite with better winrate on the surface of the match

"""Directional strategy: bet on the favourite when strict filters pass.

2026-08-11, pedido explícito del usuario ("arma el código de la
estrategia bet_on_favourite.py") -- el archivo estaba roto (docstring
sin cerrar tragándose todo el módulo como texto plano, más una cola
duplicada/incompleta al final con `check_filters`/`bet_on_favourite`
redefinidos sin cuerpo real y una importación a `TennisPlayerBio`, ya
eliminado esta misma sesión). La lógica de arriba de esa cola (líneas
originales 1-159) estaba bien formada y coherente con la estrategia
descripta en los comentarios -- se reconstruyó cerrando el docstring,
agregando los imports que faltaban (`Optional`, `datetime`/`timezone`)
y eliminando la cola rota, sin reescribir la lógica desde cero.

FILTROS (ambos deben pasar, "strict filters"):
  1. Diferencia de ranking real (ATP >= 20, WTA >= 30, umbrales ya
     usados en otras estrategias de esta sesión, ver
     `kairos-atp-surface-winrate-rank-priority-2026-08-04`).
  2. El favorito debe tener mejor win rate que el underdog tanto en la
     superficie durante las últimas 52 semanas como en sus últimos 10
     partidos (DB-first vía TennisExplorer).

SIZING: 1% del balance real de la wallet de Kalshi, con un tope diario
del 50% del balance ya gastado en compras (`get_portfolio_fills`).

LÍNEA ROJA DEL PROYECTO, SIGUE VIGENTE: esta función SOLO devuelve una
PROPUESTA (dict) o `None` -- no coloca ninguna orden real. Kairos no
tiene wiring de ejecución real en ningún lado (ver `kairos-no-real-
execution` en memoria); el cálculo de `spent_today` de acá abajo asume
campos reales de `GET /portfolio/fills` (`action`, `side`, `count_fp`)
consistentes con el resto del proyecto (ver `outer_apis/kalshi/
ticks_collector.py`, que sí usa `yes_price_dollars`/`no_price_dollars`/
`count_fp` confirmados), pero NO pudieron verificarse contra un
portfolio real con fills reales (no existen -- cero ejecución real
hasta hoy). Fail-closed por fila: cualquier fill con forma inesperada
se salta sin romper el cálculo del resto."""

from datetime import datetime, timezone
from typing import Optional

from outer_apis.sports.tennis.surface_form import get_recent_form, get_recent_form_by_surface
from outer_apis.kalshi.portfolio import KalshiPortfolioAPI
from database.db import init_db_historical
from database.historical.tennis import TennisEloCalibration, TennisPlayerStrength, Player
from models.volatility.sports.tennis.weighted_elo import (
    calibrate, combined_probability, regress_for_inactivity,
)
from scripts.tennis.compute_weighted_elo import normalize_surface
from outer_apis.sports.tennis.injury_risk import get_post_injury_performance_penalty

ATP_THRESHOLD = 20
WTA_THRESHOLD = 30
STAKE_FRACTION = 0.01
DAILY_CAP_FRACTION = 0.5
MIN_MODEL_EDGE = 0.03


def _relative_injury_adjustment(
    probability: float, favourite_penalty: float, underdog_penalty: float,
) -> float:
    """Reduce la masa probabilística del lado lesionado y renormaliza."""
    fav_mass = probability * (1.0 - max(0.0, min(0.10, favourite_penalty)))
    dog_mass = (1.0 - probability) * (1.0 - max(0.0, min(0.10, underdog_penalty)))
    total = fav_mass + dog_mass
    return fav_mass / total if total > 0 else probability


def _player_surface_winpct(player_name: str, surface: str, tour: str | None = None) -> Optional[float]:
    """Winrate point-in-time en superficie durante las últimas 52 semanas."""
    try:
        result = get_recent_form_by_surface(player_name, surface, tour=tour)
        if result is not None:
            return float(result["win_rate"])
    except Exception:
        return None
    return None


def _player_recent_winpct(player_name: str, tour: str) -> Optional[float]:
    try:
        result = get_recent_form(player_name, tour=tour)
        return float(result["win_rate"]) if result is not None else None
    except Exception:
        return None


def _rank_difference_ok(fav_rank: Optional[int], dog_rank: Optional[int], tour: str) -> bool:
    if fav_rank is None or dog_rank is None:
        return False
    try:
        diff = dog_rank - fav_rank  # lower ranking number == better player
        if tour == "ATP":
            return diff >= ATP_THRESHOLD
        if tour == "WTA":
            return diff >= WTA_THRESHOLD
    except Exception:
        return False
    return False


def _today_start_unix() -> int:
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


def _model_probability(favourite: str, underdog: str, tour: str, surface: str) -> float | None:
    """Probabilidad Weighted Elo calibrada; fail-closed si falta un rating."""
    normalized_surface = normalize_surface(surface)
    if normalized_surface is None or tour not in {"ATP", "WTA"}:
        return None
    try:
        init_db_historical()
        players = {
            row.canonical_name: row
            for row in Player.select().where(Player.canonical_name.in_((favourite, underdog)))
        }
        if favourite not in players or underdog not in players:
            return None
        strengths = {
            row.player_id: row
            for row in TennisPlayerStrength.select().where(
                (TennisPlayerStrength.tour == tour)
                & TennisPlayerStrength.player.in_((players[favourite].id, players[underdog].id))
            )
        }
        fav = strengths.get(players[favourite].id)
        dog = strengths.get(players[underdog].id)
        if fav is None or dog is None:
            return None
        attr = f"{normalized_surface.lower()}_elo"
        fav_surface, dog_surface = getattr(fav, attr), getattr(dog, attr)
        if fav_surface is None or dog_surface is None:
            return None
        today = datetime.now(timezone.utc).date()
        fav_overall = regress_for_inactivity(
            fav.overall_elo, (today - fav.last_match_date).days if fav.last_match_date else 0,
        )
        dog_overall = regress_for_inactivity(
            dog.overall_elo, (today - dog.last_match_date).days if dog.last_match_date else 0,
        )
        raw = combined_probability(fav_overall, dog_overall, fav_surface, dog_surface)
        fitted = TennisEloCalibration.get_or_none(
            (TennisEloCalibration.tour == tour)
            & (TennisEloCalibration.surface == normalized_surface)
        )
        if fitted is None or fitted.sample_count < 100:
            return None
        return calibrate(raw, fitted.intercept, fitted.slope)
    except Exception:
        return None


def _evaluate_match(match: dict) -> dict | None:
    """Evaluate a single paired tennis `match` (as returned by
    `outer_apis.kalshi.markets.tennis.build_tennis_markets`).
    Returns a proposal dict or None.
    """
    # Basic sanity
    if not match or match.get("type") != "tennis":
        return None

    a_price = match.get("current_a_price")
    b_price = match.get("current_b_price")
    if a_price is None or b_price is None:
        return None

    # Determine favourite vs underdog by higher yes-price
    if a_price >= b_price:
        fav_side = "a"
        fav_name = match.get("player_a_name")
        fav_rank = match.get("player_a_rank")
        fav_ticker = match.get("ticker_a")
        dog_name = match.get("player_b_name")
        dog_rank = match.get("player_b_rank")
    else:
        fav_side = "b"
        fav_name = match.get("player_b_name")
        fav_rank = match.get("player_b_rank")
        fav_ticker = match.get("ticker_b")
        dog_name = match.get("player_a_name")
        dog_rank = match.get("player_a_rank")

    tour = match.get("tour")
    surface = match.get("surface")

    # Ranking threshold
    if not _rank_difference_ok(fav_rank, dog_rank, tour):
        return None

    # Winrate on surface: favourite must have higher win_pct than underdog
    fav_win = _player_surface_winpct(fav_name, surface, tour)
    dog_win = _player_surface_winpct(dog_name, surface, tour)
    if fav_win is None or dog_win is None:
        return None
    try:
        if float(fav_win) <= float(dog_win):
            return None
    except Exception:
        return None

    fav_recent = _player_recent_winpct(fav_name, tour)
    dog_recent = _player_recent_winpct(dog_name, tour)
    if fav_recent is None or dog_recent is None or fav_recent <= dog_recent:
        return None

    model_probability = _model_probability(fav_name, dog_name, tour, surface)
    try:
        market_probability = float(a_price if fav_side == "a" else b_price)
    except (TypeError, ValueError):
        return None
    if model_probability is None or not 0 < market_probability < 1:
        return None
    fav_injury = get_post_injury_performance_penalty(fav_name, surface=surface)
    dog_injury = get_post_injury_performance_penalty(dog_name, surface=surface)
    fav_injury_penalty = float((fav_injury or {}).get("penalty", 0.0))
    dog_injury_penalty = float((dog_injury or {}).get("penalty", 0.0))
    model_probability_before_injury = model_probability
    model_probability = _relative_injury_adjustment(
        model_probability, fav_injury_penalty, dog_injury_penalty,
    )
    model_edge = model_probability - market_probability
    if model_edge < MIN_MODEL_EDGE:
        return None

    # Sizing: 1% of Kalshi wallet balance, capped by daily 50% spent
    client = KalshiPortfolioAPI()
    try:
        bal = client.get_portfolio_balance()
        bal_dollars = float(bal.get("balance_dollars") or 0.0)
    except Exception:
        return None

    proposed_stake = round(bal_dollars * STAKE_FRACTION, 2)
    if proposed_stake <= 0.0:
        return None

    # Compute already spent today (sum of buys' price * count_fp)
    try:
        day_start = _today_start_unix()
        fills = client.get_portfolio_fills(min_ts=day_start)
        spent_today = 0.0
        for f in fills:
            action = f.get("action")
            if action and action.lower() != "buy":
                continue
            # price depends on side
            side = f.get("side")
            price = None
            if side == "yes":
                price = f.get("yes_price_dollars")
            else:
                price = f.get("no_price_dollars")
            try:
                price_f = float(price) if price is not None else 0.0
                count = float(f.get("count_fp") or f.get("count") or 1.0)
                spent_today += price_f * count
            except Exception:
                # skip malformed fill entries
                continue
    except Exception:
        return None

    daily_allowed = bal_dollars * DAILY_CAP_FRACTION
    remaining = max(0.0, daily_allowed - spent_today)
    final_stake = min(proposed_stake, round(remaining, 2))
    if final_stake <= 0.0:
        return None

    return {
        "market_id": match.get("id"),
        "ticker": fav_ticker,
        "side": "yes",
        "stake_usd": final_stake,
        "fav_name": fav_name,
        "dog_name": dog_name,
        "tour": tour,
        "surface": surface,
        "reason": "rank_gap_surface_form_elo_edge_and_post_injury_performance",
        "model_probability": round(model_probability, 4),
        "model_probability_before_injury": round(model_probability_before_injury, 4),
        "market_probability": round(market_probability, 4),
        "model_edge": round(model_edge, 4),
        "favorite_surface_52w_winrate": round(fav_win, 4),
        "underdog_surface_52w_winrate": round(dog_win, 4),
        "favorite_recent_10_winrate": round(fav_recent, 4),
        "underdog_recent_10_winrate": round(dog_recent, 4),
        "favorite_injury_penalty": round(fav_injury_penalty, 4),
        "underdog_injury_penalty": round(dog_injury_penalty, 4),
        "favorite_injury_signal": fav_injury,
        "underdog_injury_signal": dog_injury,
    }


class BetOnFavourite:
    """Evalúa una colección de mercados con la interfaz de las estrategias.

    La clase solo produce propuestas; no coloca órdenes reales.
    """

    def __init__(self, api, markets):
        self.api = api
        self.markets = markets
        self.opportunities = []

    def evaluate_risk_and_return_opportunities(self):
        self.opportunities = []
        for market in self.markets:
            proposal = _evaluate_match(market)
            if proposal is not None:
                self.opportunities.append(proposal)
        return self.opportunities


def bet_on_favourite(match: dict) -> dict | None:
    """Compatibilidad con callers antiguos que evalúan un solo partido."""
    return _evaluate_match(match)
