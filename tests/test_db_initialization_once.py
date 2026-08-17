from unittest.mock import patch

import database.db as database_module


def test_historical_schema_initialization_runs_once_per_process():
    original = database_module._historical_initialized
    database_module._historical_initialized = False
    try:
        with patch.object(database_module, "_initialize_historical_db") as initialize:
            database_module.init_db_historical()
            database_module.init_db_historical()
        initialize.assert_called_once_with()
    finally:
        database_module._historical_initialized = original
