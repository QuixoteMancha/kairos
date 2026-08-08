"""Tests del archivado al disco externo (2026-07-31, pedido explícito
del usuario: monitorear tamaño de DB, archivar la mitad más vieja al
superar 40% del disco, borrar de la activa solo tras confirmar el
archivo). Usa una DB de archivo temporal real (SQLite en /tmp), nunca
pega al disco externo real de la mini-pc."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.models import Match as TennisScannedMatch
from database.historical.tennis.models import ProbabilityTick as TennisMatchProbabilityTick
from database.historical.tennis import archive_db
from database.historical.tennis.archiver import should_archive, archive_oldest_half, ARCHIVE_TRIGGER_RATIO


@pytest.fixture(autouse=True)
def clean_db_and_archive():
    init_db_historical()
    with db_historical.connection_context():
        TennisMatchProbabilityTick.delete().execute()
        TennisScannedMatch.delete().execute()

    # DB de archivo temporal real (no el disco externo de prod) -- se
    # resetea el estado lazy-init del módulo entre tests.
    archive_db.db_archive._state.reset()
    archive_db.db_archive.database = None
    tmp_dir = tempfile.mkdtemp()
    tmp_path = os.path.join(tmp_dir, "test_archive.db")

    yield tmp_path

    with db_historical.connection_context():
        TennisMatchProbabilityTick.delete().execute()
        TennisScannedMatch.delete().execute()
    if archive_db.db_archive.database is not None:
        archive_db.db_archive.close()
    archive_db.db_archive._state.reset()
    archive_db.db_archive.database = None


def _insert_match(market_id, detected_at, n_ticks=3):
    m = TennisScannedMatch.create(
        market_id=market_id, ticker_a=f"{market_id}-A", ticker_b=f"{market_id}-B",
        tour="ATP", surface="Hard", player_a_name="A", player_b_name="B",
        player_a_rank_at_match=10, player_b_rank_at_match=20, best_of=3,
        close_time="2026-08-01T00:00:00Z", detected_at=detected_at,
    )
    for i in range(n_ticks):
        TennisMatchProbabilityTick.create(
            match=m, observed_at=detected_at + timedelta(minutes=i),
            market_price_a=0.5 + i * 0.01, model_fair_a=0.52,
        )
    return m


def test_should_archive_false_when_db_file_missing():
    with patch("database.historical.tennis.archiver.DB_HISTORICAL_PATH", "/nonexistent/path.db"):
        assert should_archive() is False


def test_should_archive_true_when_size_ratio_exceeds_threshold():
    with patch("database.historical.tennis.archiver.os.path.exists", return_value=True), \
         patch("database.historical.tennis.archiver.os.path.getsize", return_value=45), \
         patch("database.historical.tennis.archiver.shutil.disk_usage", return_value=(100, 45, 55)):
        assert should_archive() is True  # 45/100 = 0.45 >= 0.40


def test_should_archive_false_when_below_threshold():
    with patch("database.historical.tennis.archiver.os.path.exists", return_value=True), \
         patch("database.historical.tennis.archiver.os.path.getsize", return_value=30), \
         patch("database.historical.tennis.archiver.shutil.disk_usage", return_value=(100, 30, 70)):
        assert should_archive() is False  # 30/100 = 0.30 < 0.40


def test_archive_oldest_half_skips_when_disk_not_mounted(clean_db_and_archive):
    _insert_match("M1", datetime.now(timezone.utc))
    with patch("database.historical.tennis.archive_db.ARCHIVE_DB_PATH", "/root/no_permission/archive.db"), \
         patch("database.historical.tennis.archiver.ArchiveNotMountedError", archive_db.ArchiveNotMountedError):
        # forzar que os.makedirs falle simulando disco no montado
        with patch("database.historical.tennis.archive_db.os.makedirs", side_effect=OSError("no such disk")):
            result = archive_oldest_half()

    assert result["skipped"] is True
    # nada se borró de la activa -- fail-closed real.
    assert TennisScannedMatch.select().count() == 1


def test_archive_oldest_half_moves_oldest_half_and_deletes_from_active(clean_db_and_archive):
    tmp_path = clean_db_and_archive
    now = datetime.now(timezone.utc)
    # 4 partidos, detected_at creciente -- los 2 más viejos deben archivarse.
    _insert_match("OLD_1", now - timedelta(days=10))
    _insert_match("OLD_2", now - timedelta(days=9))
    _insert_match("NEW_1", now - timedelta(days=1))
    _insert_match("NEW_2", now)

    with patch("database.historical.tennis.archive_db.ARCHIVE_DB_PATH", tmp_path):
        result = archive_oldest_half()

    assert result["skipped"] is False
    assert result["archived_matches"] == 2
    assert result["archived_ticks"] == 6  # 3 ticks x 2 partidos

    # Los 2 más viejos ya NO están en la DB activa.
    remaining = {m.market_id for m in TennisScannedMatch.select()}
    assert remaining == {"NEW_1", "NEW_2"}
    assert TennisMatchProbabilityTick.select().count() == 6  # solo quedan los ticks de NEW_1/NEW_2

    # Y SÍ están en el archivo.
    from database.historical.tennis.archive_db import ArchivedTennisScannedMatch, ArchivedTennisMatchProbabilityTick
    archived_ids = {m.market_id for m in ArchivedTennisScannedMatch.select()}
    assert archived_ids == {"OLD_1", "OLD_2"}
    assert ArchivedTennisMatchProbabilityTick.select().count() == 6


def test_archive_oldest_half_is_idempotent_via_get_or_create(clean_db_and_archive):
    """Correr el archivado 2 veces sobre el mismo partido (ej. si el
    worker se reinicia a mitad de un ciclo) no debe duplicar filas en el
    archivo -- get_or_create por market_id/observed_at."""
    tmp_path = clean_db_and_archive
    now = datetime.now(timezone.utc)
    _insert_match("OLD_1", now - timedelta(days=10))
    _insert_match("NEW_1", now)

    with patch("database.historical.tennis.archive_db.ARCHIVE_DB_PATH", tmp_path):
        archive_oldest_half()
        # Reinsertar el mismo partido viejo en la activa (simula un
        # reintento antes de que el borrado se haya confirmado en un
        # escenario real) y volver a archivar.
        _insert_match("OLD_1", now - timedelta(days=10))
        archive_oldest_half()

        from database.historical.tennis.archive_db import ArchivedTennisScannedMatch
        assert ArchivedTennisScannedMatch.select().where(ArchivedTennisScannedMatch.market_id == "OLD_1").count() == 1
