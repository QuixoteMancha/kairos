"""
Static ESPN-displayName -> canonical-full-name mapping for MLS teams,
same role as `outer_apis/scrapers/american_football/nfl_teams.py`'s
`code_to_name` for NFL.

WHY THIS EXISTS: Kalshi's `yes_sub_title` for KXMLSGAME markets (verified
live 2026-07-21) gives short names that are NOT always a prefix of
ESPN's `displayName` -- e.g. Kalshi gives "Los Angeles G" for LA Galaxy
(ESPN: "LA Galaxy", NOT prefix-compatible) and "Los Angeles F" for LAFC
(ESPN: "LAFC", NOT prefix-compatible), breaking the generic
`find_team_rating` prefix matcher used by every other team sport here.
Also: Kalshi drops periods ("DC United" vs ESPN's "D.C. United"), uses
"Saint Louis" vs ESPN's "St. Louis CITY SC", and "New York RB" vs ESPN's
"Red Bull New York".

Fix: translate ESPN's real team name to a CANONICAL full name (chosen so
every real Kalshi short name seen live is a case-insensitive prefix of
it) before persisting into TeamRating, mirroring the NFL code->city-name
translation. The canonical names are cosmetic re-orderings of the real
team identity, not fabricated teams.

Full list of real Kalshi yes_sub_title values seen live 2026-07-21 (29
teams + "Tie"): Atlanta, Austin, Charlotte, Chicago Fire, Cincinnati,
Colorado, Columbus, DC United, Dallas, Houston, Kansas City, Los Angeles
F, Los Angeles G, Miami, Minnesota, Montreal, Nashville, New England,
New York City, New York RB, Orlando, Philadelphia, Portland, Saint
Louis, Salt Lake, San Diego FC, San Jose, Seattle, Toronto, Vancouver.
"""

# ESPN displayName -> canonical name (prefix-compatible with Kalshi's short name)
ESPN_NAME_TO_CANONICAL: dict[str, str] = {
    "Atlanta United FC": "Atlanta United FC",
    "Austin FC": "Austin FC",
    "CF Montréal": "Montreal CF",
    "Charlotte FC": "Charlotte FC",
    "Chicago Fire FC": "Chicago Fire FC",
    "Colorado Rapids": "Colorado Rapids",
    "Columbus Crew": "Columbus Crew",
    "D.C. United": "DC United",
    "FC Cincinnati": "Cincinnati FC",
    "FC Dallas": "Dallas FC",
    "Houston Dynamo FC": "Houston Dynamo FC",
    "Inter Miami CF": "Miami Inter CF",
    "LA Galaxy": "Los Angeles Galaxy",
    "LAFC": "Los Angeles FC",
    "Minnesota United FC": "Minnesota United FC",
    "Nashville SC": "Nashville SC",
    "New England Revolution": "New England Revolution",
    "New York City FC": "New York City FC",
    "Orlando City SC": "Orlando City SC",
    "Philadelphia Union": "Philadelphia Union",
    "Portland Timbers": "Portland Timbers",
    "Real Salt Lake": "Salt Lake Real",
    "Red Bull New York": "New York RB",
    "San Diego FC": "San Diego FC",
    "San Jose Earthquakes": "San Jose Earthquakes",
    "Seattle Sounders FC": "Seattle Sounders FC",
    "Sporting Kansas City": "Kansas City Sporting",
    "St. Louis CITY SC": "Saint Louis City SC",
    "Toronto FC": "Toronto FC",
    "Vancouver Whitecaps": "Vancouver Whitecaps",
}


def canonical_name(espn_name: str) -> str | None:
    """Translates a real ESPN team displayName to its canonical stored
    name. Fail-closed: None if the name isn't recognized (never guesses
    -- e.g. an ESPN rebrand/expansion team not yet added here)."""
    return ESPN_NAME_TO_CANONICAL.get(espn_name)
