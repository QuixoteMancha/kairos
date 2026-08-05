from peewee import CharField, DateTimeField, FloatField, Model

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class MlbScannedMatch(HistoricalBaseModel):
    """Análogo a `TennisScannedMatch` (ver ese módulo para el
    razonamiento completo) pero para partidos de MLB -- 2026-08-05,
    pedido explícito del usuario: "añade a la captura de probabilidades
    tick by tick, las probabilidades de MLB en el worker que tenemos".

    A diferencia de tenis, MLB YA tenía descubrimiento de mercados
    reales de Kalshi (`outer_apis/kalshi/markets/team_sport.py`, series
    `KXMLBGAME`, usado por `LimitArb`/`MarketArb` cada ciclo de scan en
    `engine.py`) pero SIN registro de tracking -- esta tabla + el
    tracking de abajo agregan esa pieza que faltaba, mismo patrón que
    tenis (registro sin importar si hay edge, para calibración futura
    del modelo de fair price de baseball).

    `team_a_rating_at_match`/`team_b_rating_at_match`: el rating Elo
    real (`outer_apis/sports/baseball/ratings.py::get_team_rating`) AL
    MOMENTO de este scan puntual -- igual que tenis guarda el ranking
    al momento del partido, no el actual."""

    class Meta:
        table_name = "mlb_scanned_match"
        indexes = ((("market_id",), True),)

    market_id = CharField(unique=True)
    ticker_a = CharField()
    ticker_b = CharField()
    team_a_name = CharField()
    team_b_name = CharField()
    team_a_rating_at_match = FloatField(null=True)
    team_b_rating_at_match = FloatField(null=True)
    close_time = CharField(null=True)
    detected_at = DateTimeField()

    winner = CharField(null=True)  # 'a' | 'b' | None (no resuelto todavía)
    settled_at = DateTimeField(null=True)
