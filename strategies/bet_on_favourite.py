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
  2. El favorito debe tener MEJOR win_pct real en la superficie del
     partido que el underdog (DB-first vía `get_current_career_splits`/
     `get_all_career_splits_for_surface`, TennisAbstract).

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

from database.historical.tennis.writer import (
    get_current_career_splits,
    get_all_career_splits_for_surface,
)
from outer_apis.kalshi.portfolio import KalshiPortfolioAPI

ATP_THRESHOLD = 20
WTA_THRESHOLD = 30
STAKE_FRACTION = 0.01
DAILY_CAP_FRACTION = 0.5


def _player_surface_winpct(player_name: str, surface: str) -> Optional[float]:
    """Try DB-first sources to obtain player's win_pct on `surface`.

    Returns None on any missing/unstable data.
    """
    try:
        splits = get_current_career_splits(player_name)
        if splits:
            for r in splits:
                if r.get("surface") == surface and r.get("win_pct") is not None:
                    return float(r.get("win_pct"))

        # Bulk mapping fallback: cheaper when many players share the same
        # surface snapshot.
        if surface:
            pool = get_all_career_splits_for_surface(surface)
            if pool and player_name in pool:
                return float(pool[player_name].get("win_pct"))
    except Exception:
        return None
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


def bet_on_favourite(match: dict) -> dict | None:
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
    fav_win = _player_surface_winpct(fav_name, surface)
    dog_win = _player_surface_winpct(dog_name, surface)
    if fav_win is None or dog_win is None:
        return None
    try:
        if float(fav_win) <= float(dog_win):
            return None
    except Exception:
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
        "reason": "ranking_diff_and_surface_winrate",
    }
