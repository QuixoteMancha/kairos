"""
Fair-price models for traditional-sports-style binary prediction markets
on Kalshi (tennis, soccer, basketball, hockey), one folder per
sport, one file per model.

Re-exported here so `from models.fairprice.sports import TennisFModel`
(etc.) keeps working exactly as before this package split.
"""

from .tennis.tennisfmodel import TennisFModel
from .soccer.soccerfmodel import SoccerFModel
from .basketball.basketballfmodel import BasketballFModel
from .hockey.hockeyfmodel import HockeyFModel
from .american_football.americanfootballfmodel import AmericanFootballFModel

__all__ = [
    "TennisFModel",
    "SoccerFModel",
    "BasketballFModel",
    "HockeyFModel",
    "AmericanFootballFModel",
]
