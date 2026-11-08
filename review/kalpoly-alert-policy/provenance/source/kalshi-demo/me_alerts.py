"""March_edge SENT-alert feed (2026-10-16) for Kalshi DEMO + Polymarket PAPER (fake money only).
Source: /opt/kalshi-demo/data/props/props_export.json["alerts"], written each minute by ~/bin/export_props.py
from ajbet.db alerts WHERE sent=1 (the alerts March_edge actually sent to Telegram). Read-only.
Each alert -> normalized entry with exact game (ESPN event id -> teams/abbrs/start/state) and alert type tag."""
import json, os, re, urllib.request
from datetime import datetime, timezone

EXPORT = os.environ.get("PROPS_EXPORT", "/opt/kalshi-demo/data/props/props_export.json")
ESPN = {"nfl": "football/nfl", "ncaaf": "football/college-football", "cfb": "football/college-football",
        "mlb": "baseball/mlb", "nhl": "hockey/nhl", "nba": "basketball/nba", "wnba": "basketball/wnba",
        "ncaab": "basketball/mens-college-basketball",
        "wncaab": "basketball/womens-college-basketball"}
SPORT = {"ncaaf": "cfb",          # March_edge league -> Kalshi/Polymarket sport key
         # Oct 16 2026 (Marvin): soccer leagues (identity map + short aliases)
         "arg.1": "arg.1", "usa.nwsl": "usa.nwsl", "usa.w.usl.1": "usa.w.usl.1",
         "mex.1": "mex.1", "usa.1": "usa.1", "eng.1": "eng.1"}
# 2026-10-23 (Marvin): env override. kalshi-demo-live sets ME_LIVE_MAX_AGE_S=300 (demo only);
# Polymarket paper imports this module without the env var and keeps 180.
LIVE_MAX_AGE_S = int(os.environ.get("ME_LIVE_MAX_AGE_S", "180"))   # in-game alerts must be this fresh
PROP_RE = re.compile(r"^(player_|batter_|pitcher_)")
# 2026-10-23 (Marvin, 2:37 PM CT): Kalshi DEMO also takes in-game props March_edge generated but did not send.
# ON only when env ME_UNSENT_LIVE_PROPS=1 (kalshi-demo-live drop-in) AND the OFF file is absent. Polymarket never sets it.
UNSENT_OFF = "/opt/kalshi-demo/UNSENT_PROPS_OFF"
UNSENT_BOOST = {"player_pass_yds": 1.25}    # passing yards keep the x1.25 stake boost

def unsent_on():
    return os.environ.get("ME_UNSENT_LIVE_PROPS") == "1" and not os.path.exists(UNSENT_OFF)

def unsent_guard(a):
    """None if the unsent in-game prop may go to the trader, else the reason (stale Bovada quote / projection sanity)."""
    d = a.get("details") or {}
    if d.get("my_stale") or str(d.get("live_reason") or "").startswith("stale") or str(d.get("alert_blocked") or "").startswith("stale"):
        return "unsent: stale Bovada quote (older than last score change)"
    # 2026-10-31 kalfix (Marvin): 'no projection' blocked ~all unsent props. Root cause: March_edge writes consensus_loc
    # only for over/under ladder props; yes/no props (anytime TD, line=None) never have one. Now: no line -> no
    # line-sanity check needed; over/under without consensus_loc -> accept if March_edge priced it with the calibrated
    # pacer (prob_src == 'pacer', pacer_p present); else still blocked.
    if a.get("line") in (None, ""):
        pass
    elif d.get("consensus_loc") is None and d.get("pacer_p") is not None and d.get("prob_src") == "pacer":
        pass
    else:
        try:
            loc, L = float(d.get("consensus_loc")), float(a.get("line"))
            if not (L > 0 and 0.6 <= loc / L <= 1.67):
                return f"unsent: projection sanity ({loc:.1f} vs line {L:g})"
        except (TypeError, ValueError):
            return "unsent: no projection"
    if not a.get("player"):
        return "unsent: player unknown"
    return None
_espn = {}
NEVER_FEED = {"h2h_cfb"}   # alert strategies never handed to Kalshi demo / Polymarket paper

def alert_type(a):
    """Tag used in ledgers/reports: value, value_live, prop, prop_live, total_swing, line_swing, team_trend(_live), soccer_grind..."""
    d = a.get("details") or {}
    if d.get("unsent_live_prop"):
        return "prop_live_unsent"
    live = bool(d.get("in_game") or d.get("live")) or a.get("strategy") in ("total_swing", "line_swing", "soccer_grind")
    st = a.get("strategy") or "other"
    if st == "value" and PROP_RE.match(a.get("market") or ""):
        st = "prop"
    if st in ("total_swing", "line_swing", "soccer_grind"):
        return st
    return st + ("_live" if live else "")

def _implied(am):
    am = float(am)
    return 100 / (am + 100) if am > 0 else -am / (-am + 100)

def fair_p(a):
    """Alert's win probability for its selection. value/props/team_trend carry fair_p. total_swing/line_swing carry
    none -> alert price implied prob with a ~4.5% vig haircut (conservative)."""
    dd = a.get("details") or {}
    if dd.get("unsent_live_prop") and dd.get("p_cal"):     # unsent props: paper-calibrated win chance (lower than raw)
        return float(dd["p_cal"]), "calibrated p (unsent)"
    if a.get("strategy") == "value" and dd.get("card_p"):   # 2026-10-20: card win chance (paper-calibrated when shown)
        return float(dd["card_p"]), "card win chance"
    if a.get("fair_p"):
        return float(a["fair_p"]), "alert fair_p"
    d = a.get("details") or {}
    if d.get("p"):
        return float(d["p"]), "alert p"
    if a.get("price") not in (None, ""):
        return _implied(a["price"]) / 1.045, "price implied (devig 4.5%)"
    return None, "no probability"

def espn_game(league, event_id):
    k = (league, str(event_id))
    if k in _espn:
        return _espn[k]
    g = None
    path = ESPN.get(league) or (
        f"soccer/{league}" if league and ("." in league or league in ("mls", "epl", "nwsl", "arg.1", "usa.nwsl", "usa.w.usl.1", "mex.1"))
        else None)
    if path and event_id:
        u = f"https://site.api.espn.com/apis/site/v2/sports/{path}/summary?event={event_id}"
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "me-alert-feed"}), timeout=15) as r:
                j = json.loads(r.read())
            comp = (j.get("header") or {}).get("competitions", [{}])[0]
            t = {c.get("homeAway"): c.get("team") or {} for c in comp.get("competitors", [])}
            g = {"home": t.get("home", {}).get("displayName"), "away": t.get("away", {}).get("displayName"),
                 "home_abbr": (t.get("home", {}).get("abbreviation") or "").upper(),
                 "away_abbr": (t.get("away", {}).get("abbreviation") or "").upper(),
                 "home_loc": t.get("home", {}).get("location"), "away_loc": t.get("away", {}).get("location"),
                 "home_short": t.get("home", {}).get("shortDisplayName"), "away_short": t.get("away", {}).get("shortDisplayName"),
                 "start": comp.get("date"), "state": ((comp.get("status") or {}).get("type") or {}).get("state")}
        except Exception as ex:
            g = {"err": str(ex)[:80]}
    _espn[k] = g
    return g

def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()

def load(stats=None, since_hours=36):
    stats = stats if stats is not None else {}
    try:
        d = json.load(open(EXPORT))
    except Exception as ex:
        stats["blocker"] = f"no export {ex}"; return []
    stats["export"] = d.get("exported")
    now = datetime.now(timezone.utc); out = []
    for a in d.get("alerts", []) or []:
        try:
            ts = datetime.fromisoformat(a["ts"]).astimezone(timezone.utc)
        except Exception:
            continue
        if (now - ts).total_seconds() > since_hours * 3600:
            continue
        if a.get("strategy") in NEVER_FEED:     # 2026-10-24 (Marvin): CFB H2H model test is Telegram-only, never traded
            stats["never_feed"] = stats.get("never_feed", 0) + 1
            continue
        a["age_s"] = int((now - ts).total_seconds())
        out.append(a)
    stats["sent_alerts"] = len(out)
    if unsent_on():
        n = 0
        for a in d.get("unsent_live_props", []) or []:
            try:
                ts = datetime.fromisoformat(a["ts"]).astimezone(timezone.utc)
            except Exception:
                continue
            a = dict(a, details=dict(a.get("details") or {}, unsent_live_prop=True, in_game=True), source="unsent_live_prop")
            a["age_s"] = int((now - ts).total_seconds()); a["unsent_block"] = unsent_guard(a)
            out.append(a); n += 1
        stats["unsent_live_props"] = n
    return out

def to_entry(a):
    """Normalized entry or (None, reason). Entry keys follow the Marv board shape the mappers already use
    (sport, market h2h|spread|total|prop, pick, home, away, p, start) + me_* tags."""
    d = a.get("details") or {}
    typ = alert_type(a); lg = a.get("league"); mk = a.get("market") or ""
    p, psrc = fair_p(a)
    if a.get("unsent_block"):
        return {"me_alert_id": a["id"], "me_type": typ, "me_ts": a["ts"], "age_s": a.get("age_s"), "league": lg}, a["unsent_block"]
    base = {"me_alert_id": a["id"], "me_type": typ, "me_ts": a["ts"], "age_s": a.get("age_s"), "event_id": a.get("event_id"),
            "league": lg, "sport": SPORT.get(lg, lg), "p": p, "p_src": psrc, "alert_price": a.get("price"),
            "alert_book": a.get("book"), "headline": a.get("headline")}
    if not p:
        return base, "no probability on alert"
    g = espn_game(lg, a.get("event_id")) or {}
    if not g.get("home"):
        mu = d.get("matchup") or ""
        if "@" in mu:
            g = dict(g, away=mu.split("@")[0].strip(), home=mu.split("@")[1].strip())
    if not g.get("home") or not g.get("away"):
        return base, "game teams unknown"
    base.update(home_loc=g.get("home_loc"), away_loc=g.get("away_loc"), home_short=g.get("home_short"), away_short=g.get("away_short"))
    base.update(home=g["home"], away=g["away"], home_abbr=g.get("home_abbr"), away_abbr=g.get("away_abbr"),
                start=g.get("start") or a.get("commence"), state=g.get("state"))
    base["live"] = (g.get("state") == "in") or typ.endswith("_live") or typ == "prop_live_unsent" or typ in ("total_swing", "line_swing", "soccer_grind")
    if g.get("state") == "post":
        return base, "game final"
    sel = a.get("selection") or ""; L = a.get("line")
    if PROP_RE.match(mk):
        if not a.get("player"):
            return base, "prop player unknown"
        base.update(market="prop", prop_market=mk, player=a["player"], side=sel.lower(), line=L,
                    pick=f"{a['player']} {mk} {sel} {L}", ev=a.get("edge"), commence=base["start"])
        if (a.get("details") or {}).get("unsent_live_prop"):
            base.update(stake=UNSENT_BOOST.get(mk, 1.0), source="unsent_live_prop")
        return base, None
    if mk == "h2h":
        base.update(market="h2h", pick=sel)
    elif mk == "spreads":
        base.update(market="spread", pick=f"{sel} {float(L):+g}" if L is not None else sel)
    elif mk == "totals":
        base.update(market="total", pick=f"{sel} {L}")
    else:
        return base, f"unsupported market {mk}"
    base["key"] = f"me:{a['event_id']}:{base['market']}:{_norm(sel)}"
    try:  # 2026-10-17 (Marvin): trade only what today's tested Marv card allows (/opt/marv-feed/card_rules.py)
        import sys; sys.path.insert(0, "/opt/marv-feed"); import card_rules as CR
        ok, why, mult = CR.check(base)
    except Exception as ex:  # fail closed
        ok, why, mult = False, f"card rules error {str(ex)[:60]}", 0.0
    base["card_rule"] = why
    if not ok:
        return base, f"card rules: {why}"
    base["stake"] = mult
    return base, None

def fresh_enough(e):
    """Pregame: game not started. Live: alert <= LIVE_MAX_AGE_S old."""
    if e.get("live"):
        return (e.get("age_s") or 1e9) <= LIVE_MAX_AGE_S, f"live alert stale ({e.get('age_s')}s > {LIVE_MAX_AGE_S}s)"
    try:
        st = datetime.fromisoformat(str(e.get("start")).replace("Z", "+00:00"))
        if st <= datetime.now(timezone.utc):
            return False, "game started (pregame alert)"
    except Exception:
        pass
    return True, None

def kalshi_codes(e):
    return (e.get("away_abbr") or "").upper(), (e.get("home_abbr") or "").upper()
