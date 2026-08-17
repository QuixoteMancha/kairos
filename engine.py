import os

from strategies.limit_arb import LimitArb
from strategies.market_arb import MarketArb
from strategies.bet_on_favourite import BetOnFavourite
from outer_apis.kalshi.client import KalshiAPI
from outer_apis.kalshi.markets.tennis import build_tennis_markets
from database.historical.tennis.matchtracking import register_scanned_matches, filter_new_markets
from database.historical.tennis.player_sync_status import request_priority_sync, is_player_data_fresh
from outer_apis.kalshi.markets.football import build_football_markets
from outer_apis.kalshi.markets.team_sport import build_team_sport_markets

# Kalshi series tickers for ATP/WTA singles matches, fetched separately
# because each match is TWO per-player legs under one event_ticker (see
# outer_apis/kalshi_tennis_markets.py) — they need pairing before LimitArb's
# fair-price/volatility models (which compare player A vs player B) can
# use them. The generic /markets scan below still picks up each leg as
# its own single-ticket yes/no market too, which MarketArb also evaluates
# independently (same-ticket arb is valid on each leg on its own).
# The PAIRED tennis markets go to BOTH LimitArb (fair-price/volatility
# comparison) AND MarketArb (cross-ticket arb: buying "yes" on both
# players' tickers nets a guaranteed payout if underpriced -- see
# strategies/market_arb.py module docstring).
_TENNIS_SERIES = ("KXATPMATCH", "KXWTAMATCH")

# Kalshi series for soccer/football moneyline markets (3-way per event:
# team A / team B / tie). Reduced to a single-leg binary (favorite's own
# yes/no) per market — see outer_apis/kalshi_football_markets.py module
# docstring for the confirmed design decision. Only feeds LimitArb (needs
# the real Elo-enriched dict); MarketArb doesn't need this at all, every
# leg already flows through the generic scan below on its own.
_FOOTBALL_SERIES = ("KXMLSGAME", "KXUEFAGAME")

# Kalshi series for the 4 two-way team sports (MLB/NBA/NHL/NFL, no draws
# -- unlike soccer, these pair cross-ticket exactly like tennis, see
# outer_apis/kalshi_team_sport_markets.py module docstring). Feeds BOTH
# LimitArb and MarketArb, same as tennis. KXNBAGAME/KXNHLGAME had zero
# open markets at verification time (NBA/NHL off-season in July) --
# built and wired anyway, will activate automatically once those seasons
# open, not independently verified live yet.
_TEAM_SPORT_SERIES = ("KXMLBGAME", "KXNBAGAME", "KXNHLGAME", "KXNFLGAME")

# Kalshi series para los mercados crypto de 15 minutos (2026-08-02,
# pedido explícito del usuario: "escaneemos los mercados crypto de 15
# minutos a ver si aparecen oportunidades para market arb, incorporalo
# al sistema"). Verificado en vivo: cada símbolo tiene UN solo mercado
# abierto a la vez ("¿sube el precio en los próximos 15 min?"),
# same-ticket puro (current_a_price/current_b_price del mismo mercado,
# sin ticker_a/ticker_b como tenis) -- MarketArb ya soporta este shape
# sin cambios. SOLO MarketArb, no LimitArb (fuera de alcance de lo
# pedido -- LimitArb necesita fair-price/volatilidad por categoría, acá
# no se pidió). Investigación previa: 0 oportunidades reales en 6h de
# logs de tenis/fútbol/team-sports con esta misma estrategia -- se
# espera el mismo resultado acá, pero se escanea igual por pedido
# explícito.
_CRYPTO_15M_SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M")


def _enabled(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


class Kairos:

    @staticmethod
    def run_service():
        # Initialize the Kalshi API
        kalshi_api = KalshiAPI()

        kalshi_api.initialize()
        markets = kalshi_api.get_markets()

        tennis_legs = []
        for series in _TENNIS_SERIES:
            tennis_legs.extend(kalshi_api.get_markets(series_ticker=series))
        tennis_markets = build_tennis_markets(tennis_legs)

        # Detectar cuáles partidos son GENUINAMENTE nuevos (no registrados
        # todavía) ANTES de registrarlos -- tiene que ser antes, porque
        # register_scanned_matches es idempotente y después de correrla
        # ya no se puede distinguir "nuevo" de "ya conocido". Ver
        # database/historical/tennis/matchtracking.py::filter_new_markets
        # para el trade-off completo (pedido explícito del usuario
        # 2026-08-01: solo hacer fetch de Flashscore para partidos nuevos,
        # no para todos los abiertos -- acepta forma/H2H potencialmente
        # desactualizada para partidos ya conocidos, a cambio de bajar el
        # consumo real de BrightData).
        new_tennis_markets = filter_new_markets(tennis_markets)

        # Registro de TODO partido de tenis detectado en este scan, sin
        # importar si LimitArb encuentra edge o no (2026-07-31, pedido
        # explícito del usuario -- ver database/historical/tennis/
        # tennisscannedmatch.py). Antes del filtro de edge a propósito:
        # LimitArbSignal solo loggea cuando ya se superaron los umbrales
        # mínimos, esto es la muestra completa/sin sesgo para calibración
        # futura del modelo de volatilidad. Fail-closed internamente
        # (nunca tumba el scan), idempotente por market_id.
        register_scanned_matches(tennis_markets)

        # 2026-08-09, pedido explícito del usuario: "el engine de scan
        # sólo debe usar la data de la base... debe comunicarse con el
        # worker o verificar que la data... fue actualizada" -- YA NO se
        # hace fetch en vivo de Flashscore acá (reemplaza el prefetch
        # síncrono anterior). Para jugadores de partidos NUEVOS, se pide
        # sync prioritario a `workers/tennis_match_history_sync_worker.py`
        # (vía `TennisPlayerSyncStatus`, la única forma de comunicación
        # entre estos 2 contenedores -- comparten DB, no hay broker).
        tennis_player_names = [
            name
            for market in new_tennis_markets
            for name in (market.get('player_a_name'), market.get('player_b_name'))
            if name
        ]
        for _name in tennis_player_names:
            request_priority_sync(_name)

        # Filtro de frescura -- SOLO para LimitArb (pedido explícito del
        # usuario: "el check de la data solo debería usarse para larb y
        # bet_on_favourite, para el market arb es irrelevante"). LimitArb/
        # bet_on_favourite son estrategias PRE-MATCH: no hace falta
        # decidir en este mismo ciclo, el próximo (60s después) ya puede
        # tener la data sincronizada -- fail-closed, nunca se decide con
        # data potencialmente vieja/ausente. `tennis_markets` (SIN
        # filtrar) sigue yendo completo a MarketArb más abajo, que no
        # depende de historial/ranking de jugador para nada.
        tennis_markets_fresh_for_prematch = [
            market for market in tennis_markets
            if is_player_data_fresh(market.get('player_a_name') or '')
            and is_player_data_fresh(market.get('player_b_name') or '')
        ]

        football_legs = []
        for series in _FOOTBALL_SERIES:
            football_legs.extend(kalshi_api.get_markets(series_ticker=series))
        football_markets = build_football_markets(football_legs)

        team_sport_markets = []
        for series in _TEAM_SPORT_SERIES:
            legs = kalshi_api.get_markets(series_ticker=series)
            team_sport_markets.extend(build_team_sport_markets(legs, series))

        crypto15m_markets = []
        for series in _CRYPTO_15M_SERIES:
            crypto15m_markets.extend(kalshi_api.get_markets(series_ticker=series))

        # LimitArb needs the paired player-A-vs-player-B tennis markets,
        # the Elo-enriched favorite-only football markets, and the paired
        # team-vs-team markets for the fair-price/volatility comparison.
        # 2026-08-09: usa `tennis_markets_fresh_for_prematch` (filtrada),
        # NO `tennis_markets` -- ver bloque de arriba, pedido explícito
        # del usuario.
        limit_arb_opportunities = []
        if _enabled("ENABLE_LIMIT_ARB"):
            larb = LimitArb(api=kalshi_api, markets=markets + tennis_markets_fresh_for_prematch + football_markets + team_sport_markets)
            larb.evaluate_risk_and_return_opportunities()
            larb.execute_order_placement()
            limit_arb_opportunities = larb.opportunities

        # MarketArb gets the generic scan (covers same-ticket arb on every
        # market, tennis/football/team-sport legs included) PLUS the
        # paired tennis and team-sport markets (covers the cross-ticket
        # arb -- see strategies/market_arb.py module docstring). Football
        # is NOT added here: its favorite-only markets aren't a genuine
        # cross-ticket pair (see outer_apis/kalshi_football_markets.py).
        # crypto15m_markets added 2026-08-02 (pedido explícito del
        # usuario) -- same-ticket only, no cross-ticket pairing exists
        # for these (un solo mercado binario por símbolo).
        # 2026-08-09: acá SÍ va `tennis_markets` completo, sin filtrar --
        # MarketArb no depende de historial/ranking de jugador, pedido
        # explícito del usuario ("para el market arb es irrelevante").
        market_arb_opportunities = []
        if _enabled("ENABLE_MARKET_ARB"):
            market_arb = MarketArb(api=kalshi_api, markets=markets + tennis_markets + team_sport_markets + crypto15m_markets)
            market_arb.evaluate_risk_and_return_opportunities()
            market_arb.execute_order_placement()
            market_arb_opportunities = market_arb.opportunities

        bet_on_favourite_opportunities = []
        if _enabled("ENABLE_BET_ON_FAVOURITE"):
            favourite = BetOnFavourite(
                api=kalshi_api,
                markets=tennis_markets_fresh_for_prematch,
            )
            favourite.evaluate_risk_and_return_opportunities()
            bet_on_favourite_opportunities = favourite.opportunities

        return {
            'limit_arb_enabled': _enabled("ENABLE_LIMIT_ARB"),
            'market_arb_enabled': _enabled("ENABLE_MARKET_ARB"),
            'bet_on_favourite_enabled': _enabled("ENABLE_BET_ON_FAVOURITE"),
            'limit_arb_opportunities': limit_arb_opportunities,
            'market_arb_opportunities': market_arb_opportunities,
            'bet_on_favourite_opportunities': bet_on_favourite_opportunities,
        }
