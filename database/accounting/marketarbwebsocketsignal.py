from datetime import datetime

from peewee import CharField, DateTimeField, FloatField, Model

from database.db import db


class BaseModel(Model):
    class Meta:
        database = db


class MarketArbWebsocketSignal(BaseModel):
    """Una oportunidad de MarketArb (same-ticket o cross-ticket)
    detectada por el scanner websocket -- 2026-08-02, pedido explícito
    del usuario: "que el scan vaya guardando las oportunidades market
    arb detectadas, mediante websocket, en base... para hacer una
    revisión en los próximos días".

    Alcance actual (pedido explícito): deportes (tenis/fútbol/team-
    sports, ya construidos) + clima (KXHIGH{ciudad}) + crypto 15min
    (KXBTC15M/KXETH15M/KXSOL15M) -- NO todos los mercados binarios
    todavía (eso queda para una fase futura, "de momento sólo será
    deportes + clima + crypto").

    Solo registro/lectura -- ninguna orden se coloca a partir de esto,
    línea roja del proyecto sigue vigente."""

    class Meta:
        table_name = "market_arb_websocket_signal"

    market_id = CharField(index=True)
    category = CharField()  # "tennis" | "football" | "team_sport" | "weather" | "crypto"
    ticker_a = CharField()
    ticker_b = CharField()
    price_a = FloatField()
    price_b = FloatField()
    return_opportunity = FloatField()  # mismo cálculo que MarketArb._build_order_plan
    detected_at = DateTimeField(default=datetime.utcnow, index=True)
