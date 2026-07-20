"""
Volatility models for traditional-sports-style binary prediction markets
(tennis, soccer, basketball, baseball, hockey), one file per model.

NOTE: Kairos no longer bets on traditional sports directly (pivoted to
Kalshi-only prediction markets) — these classes exist because
models/basemodel.py still registers a volatility model per market
`type` key that may appear on Kalshi (e.g. sports-outcome markets).

Re-exported here so `from models.volatility.sports import TennisVModel`
(etc.) keeps working exactly as before this package split.
"""

from .tennisvmodel import TennisVModel
from .soccervmodel import SoccerVModel
from .basketballvmodel import BasketballVModel
from .baseballvmodel import BaseballVModel
from .hockeyvmodel import HockeyVModel

__all__ = [
    "TennisVModel",
    "SoccerVModel",
    "BasketballVModel",
    "BaseballVModel",
    "HockeyVModel",
]
