"""Team identity resolution between football-data short names and the Odds API / ESPN names March_edge uses."""

from ..data.teams import normalize, similarity

# football-data name -> common long name (both are tried)
ALIASES = {
    "Man United": "Manchester United", "Man City": "Manchester City", "Nott'm Forest": "Nottingham Forest",
    "Newcastle": "Newcastle United", "West Ham": "West Ham United", "Wolves": "Wolverhampton Wanderers",
    "Tottenham": "Tottenham Hotspur", "Brighton": "Brighton and Hove Albion", "Leeds": "Leeds United",
    "Leicester": "Leicester City", "Sheffield Weds": "Sheffield Wednesday", "West Brom": "West Bromwich Albion",
    "QPR": "Queens Park Rangers", "Bournemouth": "AFC Bournemouth", "Ipswich": "Ipswich Town", "Luton": "Luton Town",
    "Norwich": "Norwich City", "Stoke": "Stoke City", "Swansea": "Swansea City", "Cardiff": "Cardiff City",
    "Hull": "Hull City", "Coventry": "Coventry City", "Derby": "Derby County", "Preston": "Preston North End",
    "Plymouth": "Plymouth Argyle", "Oxford": "Oxford United", "Bristol City": "Bristol City", "Blackburn": "Blackburn Rovers",
    "Ath Madrid": "Atletico Madrid", "Ath Bilbao": "Athletic Bilbao", "Betis": "Real Betis", "Sociedad": "Real Sociedad",
    "Celta": "Celta Vigo", "Vallecano": "Rayo Vallecano", "Espanol": "Espanyol", "Alaves": "Deportivo Alaves",
    "Mallorca": "RCD Mallorca", "Oviedo": "Real Oviedo",
    "Dortmund": "Borussia Dortmund", "Leverkusen": "Bayer Leverkusen", "M'gladbach": "Borussia Monchengladbach",
    "Ein Frankfurt": "Eintracht Frankfurt", "FC Koln": "FC Cologne", "Mainz": "Mainz 05", "Hoffenheim": "TSG Hoffenheim",
    "St Pauli": "St Pauli", "Heidenheim": "1. FC Heidenheim", "Hamburg": "Hamburger SV", "Stuttgart": "VfB Stuttgart",
    "Wolfsburg": "VfL Wolfsburg", "Freiburg": "SC Freiburg", "Augsburg": "FC Augsburg", "Bochum": "VfL Bochum",
    "Paris SG": "Paris Saint Germain", "St Etienne": "Saint Etienne", "Marseille": "Olympique Marseille",
    "Lyon": "Olympique Lyonnais", "Brest": "Stade Brestois", "Rennes": "Stade Rennais",
    "Inter": "Inter Milan", "Milan": "AC Milan", "Verona": "Hellas Verona", "Roma": "AS Roma",
    "Sp Lisbon": "Sporting Lisbon", "Sp Braga": "SC Braga", "Guimaraes": "Vitoria Guimaraes",
    "PSV Eindhoven": "PSV", "AZ Alkmaar": "AZ", "For Sittard": "Fortuna Sittard", "Nijmegen": "NEC Nijmegen",
    "Go Ahead Eagles": "Go Ahead Eagles", "Waalwijk": "RKC Waalwijk", "Zwolle": "PEC Zwolle",
    "Ajax": "Ajax Amsterdam", "Groningen": "FC Groningen", "Utrecht": "FC Utrecht", "Twente": "FC Twente",
    "Monaco": "AS Monaco", "Lens": "RC Lens", "Nice": "OGC Nice",
    # MLS / Liga MX (Odds API spellings)
    "Los Angeles Galaxy": "LA Galaxy", "Los Angeles FC": "LAFC", "New York Red Bulls": "Red Bull New York",
    "New York City": "New York City FC", "Atlanta Utd": "Atlanta United", "St. Louis City": "St Louis City SC",
    "CF Montreal": "CF Montreal", "UNAM Pumas": "Pumas UNAM", "Atl. San Luis": "Atletico San Luis",
    "Guadalajara Chivas": "Guadalajara", "Club Tijuana": "Tijuana", "Tigres UANL": "Tigres",
}
# extra spellings (several per team)
MORE = {
    "Sp Lisbon": ["Sporting CP", "Sporting"], "Inter": ["Internazionale"], "Paris SG": ["PSG"],
    "Guadalajara Chivas": ["Chivas"], "Los Angeles FC": ["Los Angeles FC"], "Ath Bilbao": ["Athletic Club"],
    "La Coruna": ["Deportivo La Coruna", "Deportivo"],
}


def _tokens(x: str) -> str:
    return " ".join(sorted(normalize(x).split()))


def score(query: str, fd_name: str) -> float:
    s = 0.0
    for n in (fd_name, ALIASES.get(fd_name), *MORE.get(fd_name, [])):
        if n:
            s = max(s, similarity(query, n), 1.0 if _tokens(query) == _tokens(n) else 0.0)
    return s


def resolve(query: str | None, teams) -> str | None:
    """Best football-data team for a name, or None when no confident, unambiguous match."""
    if not query:
        return None
    ranked = sorted(((score(query, t), t) for t in teams), reverse=True)
    if not ranked:
        return None
    best = ranked[0]
    second = ranked[1][0] if len(ranked) > 1 else 0.0
    if best[0] >= 0.99 or (best[0] >= 0.72 and best[0] - second >= 0.05):
        return best[1]
    return None


def in_text(text: str, teams) -> list[str]:
    """Teams whose name (or alias) appears in a free-text alert, longest name first."""
    t = f" {normalize(text)} "
    hits = []
    for fd in teams:
        for n in (fd, ALIASES.get(fd), *MORE.get(fd, [])):
            if n and len(normalize(n)) >= 4 and f" {normalize(n)} " in t:
                hits.append(fd)
                break
    return hits
