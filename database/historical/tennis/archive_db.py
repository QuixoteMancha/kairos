"""Base de datos de ARCHIVO -- SQLite local en el disco externo montado
en la mini-pc (`/mnt/storage2`, confirmado explícitamente por el usuario
2026-07-31), con la MISMA estructura que `TennisScannedMatch`/
`TennisMatchProbabilityTick` en el volumen de datos activo.

Separada de `db_historical` (peewee `SqliteDatabase` distinta, mismo
nombre de tabla) a propósito -- es un archivo físico distinto en un
disco distinto, nunca se escribe simultáneamente en ambas para el mismo
dato (ver `database/historical/tennis/archiver.py`: se archiva ->
se borra de la activa, nunca queda duplicado).

Init perezoso (`_ensure_archive_db_initialized`): el path solo existe
dentro del contenedor si `/mnt/storage2` está montado como volumen (ver
docker-compose.yml, servicio `tennis_history_archiver_worker`) -- si no
está montado (ej. corriendo local sin ese mount), cualquier llamada que
lo necesite falla cerrado explícitamente en vez de escribir en un path
que no es el disco externo real."""

from __future__ import annotations

import os

from peewee import CharField, DateTimeField, FloatField, ForeignKeyField, IntegerField, Model, SqliteDatabase

ARCHIVE_DB_PATH = os.getenv("TENNIS_ARCHIVE_DB_PATH", "/archive/kairos_tennis_archive.db")

db_archive = SqliteDatabase(None)  # init perezoso, ver _ensure_archive_db_initialized


class ArchiveBaseModel(Model):
    class Meta:
        database = db_archive


class ArchivedTennisScannedMatch(ArchiveBaseModel):
    """Espejo exacto de `TennisScannedMatch` (mismos nombres de columna,
    mismo `table_name` -- son DBs físicas distintas, no hay ambigüedad).
    Ver ese modelo para el detalle de cada campo."""

    class Meta:
        table_name = "tennis_scanned_match"

    market_id = CharField(unique=True)
    ticker_a = CharField()
    ticker_b = CharField()
    tour = CharField()
    surface = CharField(null=True)
    player_a_name = CharField()
    player_b_name = CharField()
    player_a_rank_at_match = IntegerField(null=True)
    player_b_rank_at_match = IntegerField(null=True)
    best_of = IntegerField()
    close_time = CharField(null=True)
    detected_at = DateTimeField()
    winner = CharField(null=True)
    settled_at = DateTimeField(null=True)


class ArchivedTennisMatchProbabilityTick(ArchiveBaseModel):
    """Espejo exacto de `TennisMatchProbabilityTick`."""

    class Meta:
        table_name = "tennis_match_probability_tick"
        indexes = ((("match", "observed_at"), False),)

    match = ForeignKeyField(ArchivedTennisScannedMatch, backref="ticks", on_delete="CASCADE")
    observed_at = DateTimeField()
    market_price_a = FloatField(null=True)
    model_fair_a = FloatField(null=True)


class ArchiveNotMountedError(Exception):
    """`TENNIS_ARCHIVE_DB_PATH` no es escribible -- típicamente porque
    `/mnt/storage2` no está montado como volumen en este contenedor.
    Fail-closed: nunca se archiva "en cualquier lado", solo en el disco
    externo real."""


def _ensure_archive_db_initialized(create_if_missing: bool = True) -> None:
    """`create_if_missing=False` (usado por los LECTORES de solo consulta,
    ver `outer_apis/sports/tennis/volatility_calibration.py`): si el
    archivo de la DB de archivo todavía no existe, levanta
    `ArchiveNotMountedError` SIN intentar crearlo -- bug real encontrado
    2026-07-31: el contenedor `kairos` monta `/archive` como READ-ONLY
    (correcto, solo `tennis_history_archiver_worker` debe escribir ahí),
    así que cualquier intento de `sqlite3.connect()` en modo creación
    sobre un mount read-only fallaba con "unable to open database file"
    en vez de devolver limpiamente "todavía no hay nada archivado".

    `create_if_missing=True` (default, usado por el archivador -- el
    único servicio con permiso de escritura real): comportamiento
    original, crea el archivo/tablas si no existen."""
    if db_archive.database is not None:
        return

    if not create_if_missing and not os.path.exists(ARCHIVE_DB_PATH):
        raise ArchiveNotMountedError(
            f"{ARCHIVE_DB_PATH} todavía no existe -- el archivador no corrió todavía "
            f"(normal si la DB activa nunca llegó al umbral de archivado)."
        )

    parent = os.path.dirname(ARCHIVE_DB_PATH)
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as exc:
        raise ArchiveNotMountedError(
            f"No se pudo crear/acceder a {parent} -- ¿está montado el disco externo "
            f"(/mnt/storage2 en la mini-pc) como volumen en este contenedor?"
        ) from exc

    db_archive.init(ARCHIVE_DB_PATH, pragmas={"journal_mode": "wal"})
    db_archive.connect(reuse_if_open=True)
    db_archive.create_tables([ArchivedTennisScannedMatch, ArchivedTennisMatchProbabilityTick], safe=True)
