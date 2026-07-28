"""
Periodic worker: fetches real completed MLS games (ESPN), computes Elo,
persists into TeamRating (source="mls_elo") -- see outer_apis/mls_ratings.py.

Same env-var-gated loop pattern as kairos.py: if MLS_REFRESH_INTERVAL_SECONDS
is set, runs forever with a try/except per iteration (one failure doesn't
kill the process); otherwise runs once and exits.
"""

import os
import time

from outer_apis.sports.soccer import mls_ratings

_REFRESH_INTERVAL_S = os.getenv("MLS_REFRESH_INTERVAL_SECONDS")
_DEFAULT_INTERVAL_S = 21600  # 6h


def _run_once():
    ratings = mls_ratings.get_team_ratings()
    print(f"[mls_worker] refreshed {len(ratings)} MLS team ratings")
    return ratings


if __name__ == "__main__":
    if _REFRESH_INTERVAL_S:
        interval = int(_REFRESH_INTERVAL_S) if _REFRESH_INTERVAL_S.strip() else _DEFAULT_INTERVAL_S
        print(f"[mls_worker] loop mode, refresh_interval={interval}s")
        while True:
            try:
                _run_once()
            except Exception as e:
                print(f"[mls_worker] refresh error: {e}")
            time.sleep(interval)
    else:
        _run_once()
