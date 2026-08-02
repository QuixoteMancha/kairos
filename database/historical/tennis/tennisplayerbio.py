from peewee import CharField, DateField, DateTimeField

from database.historical.tennis.tennisscannedmatch import HistoricalBaseModel


class TennisPlayerBio(HistoricalBaseModel):
    """Fecha de nacimiento real por jugador/a (Tennis Abstract, ver
    `outer_apis/scrapers/tennis/tennisabstract_player_scraper.py::
    get_date_of_birth`) -- pedido explícito del usuario 2026-08-01 para
    el índice de fatiga (edad como peso: jugadores 30+ toleran peor la
    fatiga acumulada).

    Cache PERMANENTE a propósito -- a diferencia del resto de datos de
    tenis (forma reciente, ranking, etc.), la fecha de nacimiento de un
    jugador NUNCA cambia, así que no hay ventana de frescura: una vez
    fetcheada, se sirve para siempre sin volver a pedirla (ahorro real de
    requests, cero costo de mantenerla actualizada)."""

    class Meta:
        table_name = "tennis_player_bio"
        indexes = ((("player_name",), True),)

    player_name = CharField(unique=True)
    date_of_birth = DateField()
    fetched_at = DateTimeField()
