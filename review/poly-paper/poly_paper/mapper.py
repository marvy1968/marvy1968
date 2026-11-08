"""Map Marv board entries to Polymarket US markets. Same entry parsing as /opt/kalshi-demo/mapper.py
(sport, game '2026_05_TB_DAL', pick team name, prop key). Polymarket US moneyline = ONE instrument per game:
long side = first team (marketSides[long=True]); backing the other team = SELL/short (cost = 1 - best_bid)."""
import re

# Review-package isolation: the baseline tries to import helpers from a machine-local
# kalshi tree and rewrites sys.path. Those helpers are the same fallback already in
# the baseline except-clause. This copy always uses that fallback and does not touch
# sys.path. Name matching below is unchanged from the 2026-11-07 mapper.
def _norm(s): return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", (s or "").lower())).strip()
def _game_teams(e):
    p = (e.get("game") or "").split("_"); return [x.upper() for x in p[-2:]] if len(p) >= 2 else []
LEAGUE = {"nfl": "nfl", "cfb": "cfb", "ncaaf": "cfb", "nba": "nba", "wnba": "wnba", "ncaab": "cbb",
          "mlb": "mlb", "nhl": "nhl", "mls": "mls", "epl": "epl", "soccer": "mls",
          # Oct 16 2026 (Marvin): March_edge soccer league keys -> Polymarket US slugs
          "usa.1": "mls", "eng.1": "epl", "mex.1": "lmx", "arg.1": "lpa",
          "usa.nwsl": "nwsl", "nwsl": "nwsl", "uefa.champions": "ucl", "ucl": "ucl"}
WINNER = ("_full_game_winner", "moneyline")
def _names(team):
    t = team or {}
    return {_norm(x) for x in (t.get("name"), t.get("alias"), t.get("safeName"), t.get("abbreviation"),
                               t.get("displayAbbreviation")) if x}

# Shared college/pro nicknames. A side whose only overlap with the pick is one of
# these (e.g. description "Bulldogs") must not match "Samford Bulldogs".
# 2026-11-07: Samford ML was filled on Boyce vs Liberty (slug aec-cbb-bc-librty)
# because Boyce's market side is nicknamed Bulldogs (2c dog). Rollback: restore
# mapper.py.bak-20261107
_SHARED_NICKS = {
    "bulldogs", "tigers", "eagles", "wildcats", "lions", "bears", "panthers",
    "knights", "cougars", "hawks", "cardinals", "owls", "jaguars", "spartans",
    "trojans", "vikings", "warriors", "raiders", "huskies", "aggies", "bison",
    "broncos", "mustangs", "rams", "bulls", "hornets", "pirates", "terriers",
    "bobcats", "cats", "dogs", "devils", "wolves", "saints", "pioneers",
    "rebels", "cowboys", "mountaineers", "demons", "braves", "colonels",
    "generals", "cadets", "miners", "explorers", "redhawks", "seahawks",
    "gamecocks", "buffaloes", "phoenix", "stars", "kings", "hornets",
}
_STOP = {"ml", "the", "of", "vs"}

def _name_match(pick_norm, names):
    """True if this market side is the picked team. Shared nicknames alone are not enough."""
    names = {n for n in names if n}
    if not pick_norm or not names:
        return False
    distinctive = [t for t in pick_norm.split() if t not in _SHARED_NICKS and t not in _STOP]
    if not distinctive:
        return False  # mascot-only pick ("Bulldogs") is ambiguous across schools
    if pick_norm in names:
        return True
    return all(any(d == n or d in n.split() or n.endswith(" " + d) or n.startswith(d + " ")
                    for n in names) for d in distinctive)

def _f(a):
    try: return float((a or {}).get("value"))
    except (TypeError, ValueError): return None
def _price(client, slug, long):
    md = client.bbo(slug); ask, bid = _f(md.get("bestAsk")), _f(md.get("bestBid"))
    if ask is None or bid is None or ask - bid > 0.10: return None, md.get("state")   # 2026-10-23 stale/one-sided book guard
    return (ask if long else (1 - bid if bid is not None else None)), md.get("state")
def _event(entry, client, league):
    """Find the game: 'Away @ Home' full names (board.py) or '2026_05_TB_DAL' (Kalshi style)."""
    g = entry.get("game") or ""
    if entry.get("home") and entry.get("away"): g = f"{entry['away']} @ {entry['home']}"
    try: events = client.league_events(league)
    except Exception: return None
    if "@" in g:
        want = [_norm(x) for x in g.split("@")]
        for ev in events:
            if ev.get("closed"): continue
            names = [_names(t) for t in (ev.get("teams") or [])]
            if not names:
                w = [m for m in ev.get("markets", []) if (m.get("sportsMarketType") or "").endswith(WINNER)]
                names = [_names(s.get("team")) for s in (w[0]["marketSides"] if w else [])]
            if all(any(_name_match(x, ns) for ns in names) for x in want): return ev
        return None
    teams = [t.lower() for t in _game_teams(entry)]
    for ev in events:
        if not ev.get("closed") and teams and all(f"-{t}-" in ev.get("slug", "") + "-" for t in teams): return ev
    return None
_LINE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*$")
UNFAV_HAIRCUT = 0.03   # fair-prob haircut per 0.5 pt of unfavorable line move (conservative)
def _pick_line(cands, L, better):
    """cands: [(line_m, slug, long, meta)]. better(lm) = pts in pick's favour (>0 favourable).
    Exact > nearest favourable > unfavourable within 0.5. Never unfavourable beyond 0.5."""
    ok = [(abs(better(c[0])), c) for c in cands if better(c[0]) >= -0.5 + 1e-9]
    if not ok: return None
    exact = [c for d, c in ok if d < 1e-9]
    if exact: return exact[0]
    fav = sorted([(d, c) for d, c in ok if better(c[0]) > 0], key=lambda x: x[0])
    unf = sorted([(d, c) for d, c in ok if better(c[0]) < 0], key=lambda x: x[0])
    return (fav or unf)[0][1]
def map_line(entry, client, league):
    mk = entry.get("market"); pick = entry.get("pick") or ""
    m = _LINE.search(pick)
    if not m: return None
    L = float(m.group(1)); ev = _event(entry, client, league)
    if not ev: return None
    try:  # 2026-10-23: list endpoint carries only the winner market; load the full event
        full = client.event(ev["slug"]) if hasattr(client, "event") else None
        if full and full.get("markets"): ev = full
    except Exception: pass
    cands = []
    if mk == "total":
        over = pick.strip().lower().startswith("over")
        if not (over or pick.strip().lower().startswith("under")): return None
        for mm in ev.get("markets", []):
            if not (mm.get("sportsMarketType") or "").endswith("_full_game_total") or "team_points" in mm.get("sportsMarketType", "") or mm.get("closed"): continue
            for s in mm.get("marketSides", []):
                if _norm(s.get("description")) == ("over" if over else "under"):
                    lm = float(re.search(r"([\d.]+)$", mm["slug"].replace("pt", ".")).group(1))
                    cands.append((lm, mm["slug"], bool(s.get("long")), s.get("description")))
        better = (lambda lm: L - lm) if over else (lambda lm: lm - L)
    else:  # spread: pick 'Buffalo Bills +2.5'
        team = _norm(_LINE.sub("", pick))
        for mm in ev.get("markets", []):
            if not (mm.get("sportsMarketType") or "").endswith("_full_game_spread") or mm.get("closed"): continue
            for s in mm.get("marketSides", []):
                nm = _names(s.get("team"))
                if _name_match(team, nm):
                    try: lm = float(s.get("description"))
                    except (TypeError, ValueError): continue
                    cands.append((lm, mm["slug"], bool(s.get("long")), (s.get("team") or {}).get("name")))
        better = lambda lm: lm - L
    best = _pick_line(cands, L, better)
    if not best: return None
    lm, slug, long, meta = best
    px, state = _price(client, slug, long); diff = better(lm)
    adj = 0.0 if diff >= 0 else UNFAV_HAIRCUT * (abs(diff) / 0.5)
    return {"slug": slug, "side": "long" if long else "short", "team": meta, "ask": px, "state": state,
            "pick_line": L, "market_line": lm, "line_diff": round(diff, 2), "fair_adj": -adj, "event": ev.get("slug")}
def map_entry(entry, client):
    mk = entry.get("market")
    if mk == "prop": return None   # Polymarket US lists no NFL player props (team markets only) as of 2026-10
    league = LEAGUE.get(entry.get("sport"))
    if mk in ("spread", "total"): return map_line(entry, client, league) if league else None
    if mk not in ("h2h", "ml", "moneyline"): return None
    team = re.sub(r"\bml\b", "", _norm(entry.get("pick"))).strip()
    if not league or not team: return None
    teams = [t.lower() for t in _game_teams(entry)]
    try: events = client.league_events(league)
    except Exception: return None
    ev0 = _event(entry, client, league) if ("@" in (entry.get("game") or "") or entry.get("home")) else None
    if ev0:
        events, teams = [ev0], []
    elif entry.get("home") and entry.get("away"):
        return None  # known game, no Polymarket event: do not nickname-match a different game
    for ev in events:
        if ev.get("closed"): continue
        if teams and not all(f"-{t}-" in ev.get("slug", "") + "-" for t in teams): continue
        for m in ev.get("markets", []):
            if not (m.get("sportsMarketType") or "").endswith(WINNER) or m.get("closed"): continue
            for s in m.get("marketSides", []):
                nm = _names(s.get("team")) | {_norm(s.get("description"))}
                if not _name_match(team, nm): continue
                md = client.bbo(m["slug"])
                ask, bid = _f(md.get("bestAsk")), _f(md.get("bestBid"))
                long = bool(s.get("long"))
                px = ask if long else (1 - bid if bid is not None else None)
                return {"slug": m["slug"], "side": "long" if long else "short", "team": s.get("description"),
                        "ask": px, "state": md.get("state"), "title": m.get("question"), "event": ev.get("slug")}
    return None
 
