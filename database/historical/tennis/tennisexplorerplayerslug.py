from peewee import CharField, DateTimeField, Model

from database.db import db_historical


class HistoricalBaseModel(Model):
    class Meta:
        database = db_historical


class TennisExplorerPlayerSlug(HistoricalBaseModel):
    """Cache de slugs REALES de TennisExplorer, cosechados de los `href`
    que ya vienen en cada fila de partido descargada (sin request
    extra) -- 2026-08-06, pedido explícito del usuario: "corrige la
    data de matches por confusión de nombres". Supera la limitación de
    `guess_player_slug` (adivina por apellido, colisiona con
    hermanos/parientes homónimos del circuito -- Osaka/Williams/Zverev/
    Tsitsipas confirmados en vivo).

    `surname_key`: apellido normalizado (minúsculas, guion->espacio,
    mismo criterio que `_norm_surname` en
    `wta_favorite_surface_winrate_filter_backtest.py`) del nombre
    ABREVIADO tal cual aparece en el link (ej. "Osaka N." -> "osaka").
    `slug`: el slug REAL visto en el `href` (ej. "osaka-fa603") --
    nunca adivinado, siempre una URL real observada en vivo.

    Puede haber MÚLTIPLES slugs reales para el mismo `surname_key`
    (ej. "osaka" y "osaka-fa603" para las 2 hermanas) -- eso es
    justamente lo que permite desambiguar: `resolve_player_slug`
    prueba cada uno y se queda con el que verifique contra el nombre
    completo real pedido."""

    class Meta:
        table_name = "tennisexplorer_player_slug"
        indexes = (
            (("surname_key", "slug"), True),
        )

    surname_key = CharField()
    slug = CharField()
    discovered_at = DateTimeField()
