from peewee import CharField, DateTimeField, TextField

from database.historical.tennis.tennisscannedmatch import HistoricalBaseModel


class TennisMatchStats(HistoricalBaseModel):
    """Estadísticas reales de un partido ya resuelto (aces, 1er saque%,
    dobles faltas, break points salvados/convertidos, etc.), vía el feed
    `df_st_1` de Flashscore -- pedido explícito del usuario 2026-08-01:
    "cuando se detecte que un partido terminó se debe hacer fetch de las
    stats". Ver `outer_apis/scrapers/tennis/flashscore_scraper.py::
    get_match_statistics` para el formato crudo y
    `database/historical/tennis/matchstats.py` para lectura/escritura.

    Poblada automáticamente al resolver un partido trackeado (ver
    `workers/tennis_match_tick_worker.py`), UNA fila por `market_id`
    (idempotente -- si ya se guardó, no se reintenta).

    Alcance actual: SOLO tenis. Fútbol/basketball/baseball quedan
    pendientes de auditoría de hook de settlement por deporte antes de
    replicar este patrón (no implementado a ciegas, ver conversación
    2026-08-01).

    `raw_stats_json`: {"match": {stat_name: [p1_val, p2_val], ...},
    "set_1": {...}, ...} tal cual lo devuelve
    `flashscore_scraper.get_match_statistics` -- NO se normaliza a
    columnas individuales a propósito, Flashscore no siempre expone el
    mismo conjunto de estadísticas para todo partido (ITF/challenger
    suelen tener menos categorías que Grand Slam/Masters)."""

    class Meta:
        table_name = "tennis_match_stats"
        indexes = ((("market_id",), True),)

    market_id = CharField(unique=True)
    flashscore_match_id = CharField()
    raw_stats_json = TextField()
    fetched_at = DateTimeField()
