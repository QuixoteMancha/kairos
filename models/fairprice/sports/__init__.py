"""
Fair-price models for traditional-sports-style binary prediction markets
on Kalshi (tennis, soccer, basketball, baseball, hockey), one file per
model.

Re-exported here so `from models.fairprice.sports import TennisFModel`
(etc.) keeps working exactly as before this package split.
"""

from .tennisfmodel import TennisFModel
from .soccerfmodel import SoccerFModel
from .basketballfmodel import BasketballFModel
from .baseballfmodel import BaseballFModel
from .hockeyfmodel import HockeyFModel

__all__ = [
    "TennisFModel",
    "SoccerFModel",
    "BasketballFModel",
    "BaseballFModel",
    "HockeyFModel",
]
