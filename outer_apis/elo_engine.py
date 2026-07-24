"""
Generic (sport-agnostic) Elo rating calculator — pure math, no I/O.

Built 2026-07-21 because, unlike tennis (real p_serve calibration) and
football (real ClubElo API), NBA/MLB/NHL have NO free pre-computed Elo
rating source (confirmed via research, see memory
kairos-team-sports-tables-and-research-2026-07-21) but DO have free
official real-time raw-game-result outer_apis (stats.nba.com via curl_cffi,
statsapi.mlb.com, api-web.nhle.com — all confirmed live 2026-07-21). This
module is the shared calculator that `outer_apis/basketball_ratings.py`,
`outer_apis/baseball_ratings.py`, `outer_apis/hockey_ratings.py` each feed real game
results into.

Standard chess-style Elo, no draws (none of these 3 sports allow a
regulation-season draw — unlike football, which is why football didn't
need this and uses ClubElo's pre-computed ratings instead):

    E_a = 1 / (1 + 10^((R_b - R_a) / SCALE))
    R_a' = R_a + K * (S_a - E_a)

`K` and `SCALE` are UNFITTED initial constants (K=20, SCALE=400 — the
same convention `models/fairprice/sports/_base.py` already uses for its
own RATING_SCALE), not calibrated against real outcomes. Consistent with
this project's established pattern (LimitArb's price_adjustment weights,
tennis's p_serve buckets before real calibration): documented honestly as
a starting point, not a final tuned model.
"""

DEFAULT_RATING = 1500.0
K_FACTOR = 20.0
RATING_SCALE = 400.0  # matches models/fairprice/sports/_base.py's RATING_SCALE convention


def expected_score(rating_a: float, rating_b: float) -> float:
    """P(A wins), per the standard Elo logistic curve."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / RATING_SCALE))


def update_ratings(rating_a: float, rating_b: float, a_won: bool, k: float = K_FACTOR) -> tuple[float, float]:
    """Returns (new_rating_a, new_rating_b) after one game. `a_won`:
    True if A won, False if B won (no draw support -- see module docstring
    for why none of NBA/MLB/NHL need it)."""
    score_a = 1.0 if a_won else 0.0
    e_a = expected_score(rating_a, rating_b)
    new_a = rating_a + k * (score_a - e_a)
    new_b = rating_b + k * ((1.0 - score_a) - (1.0 - e_a))
    return new_a, new_b
