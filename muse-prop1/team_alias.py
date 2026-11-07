"""Explicit NFL and NBA name aliases. Not a fuzzy matcher.

Abbreviations are the public codes already used in this repo for NFL
(`marv/data/teams.py`) and the public ESPN abbreviations for NBA. A code maps
to one full name in one sport. Source ids are not consulted. Two different
teams never share an entry.
"""
from __future__ import annotations

from cohort import team_key

# code -> full public name. "LA" is the Rams only, and only in NFL.
NFL_ABBR = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LA": "Los Angeles Rams", "LAR": "Los Angeles Rams",
    "LAC": "Los Angeles Chargers", "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings", "NE": "New England Patriots", "NO": "New Orleans Saints",
    "NYG": "New York Giants", "NYJ": "New York Jets", "PHI": "Philadelphia Eagles",
    "PIT": "Pittsburgh Steelers", "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers",
    "TB": "Tampa Bay Buccaneers", "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}

NBA_ABBR = {
    "ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BKN": "Brooklyn Nets",
    "CHA": "Charlotte Hornets", "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets", "DET": "Detroit Pistons",
    "GS": "Golden State Warriors", "GSW": "Golden State Warriors",
    "HOU": "Houston Rockets", "IND": "Indiana Pacers",
    "LAC": "Los Angeles Clippers", "LAL": "Los Angeles Lakers",
    "MEM": "Memphis Grizzlies", "MIA": "Miami Heat", "MIL": "Milwaukee Bucks",
    "MIN": "Minnesota Timberwolves", "NOP": "New Orleans Pelicans", "NO": "New Orleans Pelicans",
    "NY": "New York Knicks", "NYK": "New York Knicks",
    "OKC": "Oklahoma City Thunder", "ORL": "Orlando Magic",
    "PHI": "Philadelphia 76ers", "PHX": "Phoenix Suns", "PHO": "Phoenix Suns",
    "POR": "Portland Trail Blazers", "SA": "San Antonio Spurs", "SAS": "San Antonio Spurs",
    "SAC": "Sacramento Kings", "TOR": "Toronto Raptors", "UTA": "Utah Jazz",
    "WAS": "Washington Wizards", "WSH": "Washington Wizards",
}

# Alternate full strings that already appear as public names. Not substrings.
NFL_EXTRA = {}
NBA_EXTRA = {
    "la clippers": "Los Angeles Clippers",
    "la lakers": "Los Angeles Lakers",
    "trail blazers": "Portland Trail Blazers",
    "76ers": "Philadelphia 76ers",
}


def _table(abbr: dict[str, str], extra: dict[str, str]) -> dict[str, str]:
    out = {}
    for code, full in abbr.items():
        canon = team_key(full)
        out[team_key(code)] = canon
        out[canon] = canon
    for alias, full in extra.items():
        out[team_key(alias)] = team_key(full)
    return out


_TABLES = {
    "nfl": _table(NFL_ABBR, NFL_EXTRA),
    "nba": _table(NBA_ABBR, NBA_EXTRA),
}
_FULL = {}
for sport, abbr in (("nfl", NFL_ABBR), ("nba", NBA_ABBR)):
    for full in list(abbr.values()) + list({"nfl": NFL_EXTRA, "nba": NBA_EXTRA}[sport].values()):
        _FULL[(sport, team_key(full))] = full


def canonical_team(sport: str, name: str | None) -> str | None:
    """Stable team key. None when the name is blank. Unknown names stay literal."""
    if not name or not str(name).strip():
        return None
    key = team_key(name)
    table = _TABLES.get(sport)
    if table is None:
        return key
    return table.get(key, key)


def display_team(sport: str, name: str | None) -> str | None:
    """Full public name when the alias table has one. Otherwise the original text."""
    if not name or not str(name).strip():
        return None
    canon = canonical_team(sport, name)
    return _FULL.get((sport, canon), str(name).strip())
