"""Baseline migration — DB ya existente.

Esta migración NO altera el schema. Sólo registra el estado actual como
punto de partida para futuras migraciones. Si arrancas con una DB nueva,
peewee creará las tablas automáticamente vía database.init_db().
"""


def migrate(migrator, database, fake=False, **kwargs):
    """Punto de partida — no-op."""
    pass


def rollback(migrator, database, fake=False, **kwargs):
    """No hay nada que revertir en el baseline."""
    pass
