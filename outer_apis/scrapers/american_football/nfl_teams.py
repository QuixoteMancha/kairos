"""
Static NFL team abbreviation -> full name mapping (32 teams, stable/
well-known). Needed because nflverse's `games.csv` (see
nflverse_scraper.py) identifies teams by code ("SEA", "LA") while Kalshi's
market data gives short city names ("Seattle") — unlike MLB/NBA/NHL,
where both our scraped data and Kalshi's naming are prefix-compatible
full/partial names, NFL has no prefix relationship between a code and a
city name at all, so this needs an explicit static table instead of the
generic `database/team_elo_writer.py::find_team_rating` prefix matcher.

"LA" (not "LAR") for the Rams and "LAC" for the Chargers matches
nflverse's actual convention, confirmed against the real rescued
`american_football`/`nflverse` TeamRating rows found in production
(top rating was "LA" at 1668.68, i.e. the Rams).
"""

CODE_TO_NAME: dict[str, str] = {
    "ARI": "Arizona Cardinals",
    "ATL": "Atlanta Falcons",
    "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers",
    "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals",
    "CLE": "Cleveland Browns",
    "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos",
    "DET": "Detroit Lions",
    "GB": "Green Bay Packers",
    "HOU": "Houston Texans",
    "IND": "Indianapolis Colts",
    "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs",
    "LV": "Las Vegas Raiders",
    "LAC": "Los Angeles Chargers",
    "LA": "Los Angeles Rams",
    "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings",
    "NE": "New England Patriots",
    "NO": "New Orleans Saints",
    "NYG": "New York Giants",
    "NYJ": "New York Jets",
    "PHI": "Philadelphia Eagles",
    "PIT": "Pittsburgh Steelers",
    "SF": "San Francisco 49ers",
    "SEA": "Seattle Seahawks",
    "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans",
    "WAS": "Washington Commanders",
}


def code_to_name(code: str) -> str | None:
    """Fail-closed: None if the code isn't recognized (never guesses a
    team name for an unknown/relocated/renamed code)."""
    return CODE_TO_NAME.get(code)
