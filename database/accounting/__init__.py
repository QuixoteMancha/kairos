"""
Operational/accounting data — LimitArb signals today, future orders/
fills/PnL. Lives in `db`/`DB_PATH` (the "definitive" volume), separate
from `database/historical/` (reference/calibration data, `db_historical`)
— see database/db.py docstring for the two-database split rationale.
"""

from database.accounting.limitarbsignal import LimitArbSignal
from database.accounting.writer import log_signal, get_recent_signals

__all__ = ["LimitArbSignal", "log_signal", "get_recent_signals"]
