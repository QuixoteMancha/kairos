from peewee import CharField, DateTimeField, FloatField, IntegerField

from database.base_models import HistoricalBaseModel


class MlbHistoricalOdds(HistoricalBaseModel):
    """Odds PRE-PARTIDO reales de MLB (moneyline apertura/cierre),
    fuente SportsbookReviewsOnline -- 2026-08-05, pedido explícito del
    usuario. Ver
    `outer_apis/scrapers/baseball/sportsbookreviewsonline_scraper.py`
    para el razonamiento completo (cobertura real 2010-2021, el sitio
    no se actualiza más allá de eso).

    Poblada SOLO por `scripts/baseball/mlb_historical_odds_backfill.py`
    (fetch de red deliberado, fuera del loop de análisis) -- mismo
    criterio DB-first que el resto de esta sesión: cualquier backtest
    futuro que use esto debe leer de acá, nunca tocar la red."""

    class Meta:
        table_name = "mlb_historical_odds"
        indexes = (
            (("date", "home_team", "away_team"), True),
        )

    date = CharField()  # "YYYY-MM-DD"
    home_team = CharField()
    away_team = CharField()
    home_moneyline_open = IntegerField(null=True)
    home_moneyline_close = IntegerField(null=True)
    away_moneyline_open = IntegerField(null=True)
    away_moneyline_close = IntegerField(null=True)
    home_implied_prob_open = FloatField(null=True)
    home_implied_prob_close = FloatField(null=True)
    away_implied_prob_open = FloatField(null=True)
    away_implied_prob_close = FloatField(null=True)
    source = CharField(default="sportsbookreviewsonline")
    fetched_at = DateTimeField()
