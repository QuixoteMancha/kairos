# TODO: still the generic Elo-style _SportFModel heuristic — open decision
# (see kairos-tennis-markov-volatility-rebuild-2026-07 memory) on whether
# this should instead use pre_match_win_prob() from
# models/volatility/sports/tennis_markov.py, which already derives a real
# win probability from rankings via the Markov engine. Not implemented yet.

from ._base import _SportFModel


class TennisFModel(_SportFModel):
    pass
