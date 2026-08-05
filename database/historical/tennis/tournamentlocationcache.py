from peewee import CharField, DateTimeField, FloatField, Model

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class TournamentLocationCache(HistoricalBaseModel):
    """Caché de ubicaciones geocodeadas vía Nominatim (OpenStreetMap) --
    2026-08-04, pedido explícito del usuario: "investiga de donde se
    pueden sacar más datos de itf/challenger para completar la data" --
    complementa la tabla estática `tournament_location.py` (curada a
    mano, torneos ATP/WTA grandes con sede fija) para la COLA LARGA de
    torneos challenger/ITF individuales, cada uno con su propia ciudad
    real, demasiados para catalogar uno por uno a mano.

    `city_candidate` = nombre de ciudad extraído del string de torneo
    real (ver `scripts/geocode_missing_tournament_locations.py`), NO el
    string crudo del torneo -- varios torneos distintos en la misma
    ciudad ("Cluj-Napoca 2 WTA", "Cluj-Napoca WTA") comparten una sola
    fila de caché.

    `lat`/`lon` NULL = geocoding intentado pero SIN resultado real
    (nunca se reintenta indefinidamente el mismo nombre que ya falló) --
    fail-closed, distinto de "nunca se intentó" (fila inexistente)."""

    class Meta:
        table_name = "tournament_location_cache"
        indexes = (
            (("city_candidate",), True),
        )

    city_candidate = CharField()
    lat = FloatField(null=True)
    lon = FloatField(null=True)
    source = CharField()  # "nominatim"
    fetched_at = DateTimeField()
