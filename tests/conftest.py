"""Aísla los tests de las bases de datos reales del repo.

`DB_PATH`/`DB_HISTORICAL_PATH` se leen una sola vez al importar
`database/db.py`, así que deben fijarse ANTES de cualquier import del
proyecto -- por eso vive en conftest.py a nivel de módulo (pytest lo
carga antes de los test_*.py)."""
from __future__ import annotations

import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="kairos_test_")
os.environ.setdefault("DB_PATH", os.path.join(_TEST_DIR, "kairos_test.db"))
os.environ.setdefault("DB_HISTORICAL_PATH", os.path.join(_TEST_DIR, "kairos_test_historical.db"))
