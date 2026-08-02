"""Recomendación real de entrada por reacción al set 1 -- 2026-08-02,
pedido explícito del usuario: integrar `strategies/set1_favorite_reaction.py`
con el score en vivo real (`outer_apis/scrapers/tennis/
tennisexplorer_livescore_scraper.py`), para automatizar la detección de
"el favorito ganó el set 1" (hasta ahora, confirmación manual del
usuario, ver caso real Pegula/Eala).

Una fila por partido de Kalshi trackeado (`TennisScannedMatch`) una vez
que el set 1 se decide -- idempotente por `market_id`, nunca se
recalcula ni se pisa (el resultado del set 1 ya jugado no cambia).

SOLO REGISTRO/RECOMENDACIÓN -- ninguna orden se coloca, línea roja del
proyecto sigue vigente."""

from datetime import datetime

from peewee import BooleanField, CharField, DateTimeField, FloatField, IntegerField, Model

from database.db import db


class BaseModel(Model):
    class Meta:
        database = db


class Set1EntrySignal(BaseModel):
    class Meta:
        table_name = "set1_entry_signal"
        indexes = ((("market_id",), True),)

    market_id = CharField(unique=True)
    favorite_name = CharField()
    underdog_name = CharField()
    favorite_rank = IntegerField(null=True)
    underdog_rank = IntegerField(null=True)
    rank_gap = IntegerField(null=True)
    favorite_won_set1 = BooleanField()
    size_pct = FloatField()  # 0.0 si favorite_won_set1=False (ver strategies/set1_favorite_reaction.py)
    favorite_win_rate_basis = FloatField(null=True)
    detected_at = DateTimeField(default=datetime.utcnow, index=True)
