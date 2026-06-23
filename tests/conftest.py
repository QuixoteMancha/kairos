"""
Aísla los tests de la base de datos real.

Sin esto, varios tests (p.ej. test_cross_book_settle.py, test_db_helpers.py)
llaman a `init_db()` sin overridear la ruta, y como `DB_PATH` resuelve a
`src/database/kairos.db` por defecto (ver src/core/config.py), terminan
escribiendo filas de fixtures (bet_id 'w1', 'cb_kambi2w01', 'Fix t1', etc.)
en la MISMA base que usa el sistema real — contaminando estadísticas y
herramientas de auditoría como scripts/validate_resolution.py.

`DB_PATH` se lee una sola vez al importar `src/core/config.py`, así que debe
fijarse ANTES de cualquier import del proyecto — por eso esto vive en
conftest.py a nivel de módulo (pytest lo carga antes de los test_*.py).
"""
from __future__ import annotations

import os
import tempfile

_TEST_DB_DIR = tempfile.mkdtemp(prefix="kairos_test_")
os.environ.setdefault("DB_PATH", os.path.join(_TEST_DB_DIR, "kairos_test.db"))
