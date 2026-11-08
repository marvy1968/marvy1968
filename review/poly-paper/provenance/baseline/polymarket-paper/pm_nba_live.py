"""NBA LIVE player props -- TRACK-ONLY (2026-10-23, Marvin approved). Logs would-have-bets to data/track_only.jsonl
(type nba_prop_live, never in the trade allowlist; no paper orders/fills). Graded by track_grade.py (settlementPx,
props that resolve mid-game included).
Model: live ESPN box score (current stat, minutes, fouls, period/clock, margin) + pregame per-minute rate (ESPN
season averages, prior season blended with current) x expected remaining minutes (foul / blowout adjusted);
Poisson / overdispersed tail -> probability, shrunk 50% toward market mid, capped at 0.65; needs edge >= 5c after fee.
Quote guard: bbo refetched right before logging; skip if quote > 10 s old, spread > 3c, or < $50 depth at the ask.
Simulated fill at the ask. One log per player/stat/side per game (Polymarket lists a ladder of fixed thresholds, not a moving line, so a
new threshold is NOT a line move; no second log for the same player/stat/side).
OFF SWITCH: touch NBA_LIVE_OFF in this dir (or env NBA_LIVE=off). --dry prints, writes nothing."""
import json, os, re, sys, math, time, unicodedata, urllib.request
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from pm_client import PMPublic
import pm_type_gate as G
CT = ZoneInfo("America/Chicago"); DATA = os.path.join(HERE, "data")
TRACK = os.path.join(DATA, "track_only.jsonl"); STATE = os.path.join(DATA, "nba_live_state.json")
RATES = os.path.join(DATA, "nba_rates_cache.json"); LOG = os.path.join(DATA, "nba_live.log")
TYPE = "nba_prop_live"; STAKE = 50.0; CAP = 0.65; MIN_EDGE = 0.05; SHRINK = 0.50; MAX_AGE = 10; MAX_SPREAD = 0.03
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
STAT = {"basketball_player_points": "PTS", "basketball_player_threes": "3PM", "basketball_player_rebounds": "REB",
        "basketball_player_assists": "AST", "basketball_player_steals": "STL", "basketball_player_blocks": "BLK",
        "basketball_player_points_rebounds_assists": "PRA"}
DISP = {"PTS": 1.8, "PRA": 1.6, "REB": 1.25, "AST": 1.25, "3PM": 1.2, "STL": 1.0, "BLK": 1.1}  # variance / mean
Q = re.compile(r"Will (.+?) record at least (\d+)", re.I)
P_GATE = 0.62   # 2026-10-30 Marvin: trade if capped win prob >= 62% and edge > 0 after fee, OR edge >= MIN_EDGE
def gate_ok(p_side, edge): p = min(p_side, CAP); return edge >= MIN_EDGE or (p >= P_GATE and edge > 0)
DRY = "--dry" in sys.argv

def get(url):
    # 2026-11-02 bug fix: empty / HTML / rate-limit bodies raise a clear ValueError (caller logs + skips the cycle)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r: body = r.read()
    try: return json.loads(body)
    except ValueError: raise ValueError(f"bad JSON from {url.split('?')[0][:90]} ({len(body)} bytes: {body[:60]!r})")
BUDGET = float(os.environ.get("NBA_LIVE_BUDGET_S", "120"))   # 2026-11-02: unit is killed at 170 s; stop early, save state
def norm(s): return re.sub(r"[^a-z ]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().replace(" jr", "").replace(" iii", "").replace(" ii", "")).strip()
def f(x):
    try: return float(x["value"] if isinstance(x, dict) else x)
    except Exception: return None
def log(m):
    line = f"{datetime.now(CT):%H:%M:%S} {m}"; print(line, flush=True)
    if not DRY:
        with open(LOG, "a") as fh: fh.write(line + "\n")

def rates(aid, cache):
    """per-minute rates + expected minutes from ESPN averages (prior season weighted 10 games, current by games)."""
    if aid in cache and cache[aid].get("d") == datetime.now(CT).strftime("%Y-%m-%d"): return cache[aid]
    try: d = get(f"https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{aid}/stats")
    except Exception: return None
    cat = next((c for c in d.get("categories", []) if c.get("name") == "averages"), None)
    if not cat: return None
    nm = cat["names"]; rows = cat.get("statistics", [])[-2:]
    acc = {}; wt = 0.0
    for i, s in enumerate(rows):
        v = dict(zip(nm, s["stats"])); gp = float(v.get("gamesPlayed") or 0)
        w = gp if i == len(rows) - 1 else min(gp, 10.0)
        if len(rows) == 1: w = gp
        mn = float(v.get("avgMinutes") or 0)
        if w <= 0 or mn <= 0: continue
        tpm = float(str(v.get("avgThreePointFieldGoalsMade-avgThreePointFieldGoalsAttempted", "0-0")).split("-")[0] or 0)
        vals = {"MIN": mn, "PTS": float(v["avgPoints"]), "REB": float(v["avgRebounds"]), "AST": float(v["avgAssists"]),
                "STL": float(v["avgSteals"]), "BLK": float(v["avgBlocks"]), "3PM": tpm}
        for k, x in vals.items(): acc[k] = acc.get(k, 0) + w * x
        wt += w
    if wt <= 0: return None
    a = {k: x / wt for k, x in acc.items()}
    r = {"d": datetime.now(CT).strftime("%Y-%m-%d"), "min": a["MIN"]}
    for k in ("PTS", "REB", "AST", "STL", "BLK", "3PM"): r[k] = a[k] / a["MIN"]
    r["PRA"] = r["PTS"] + r["REB"] + r["AST"]
    cache[aid] = r; return r

def tail(mu, need, disp):
    """P(X >= need), X ~ Poisson(mu) or negative binomial with variance disp*mu."""
    if need <= 0: return 1.0
    if mu <= 0: return 0.0
    if disp <= 1.01:
        p, term = 0.0, math.exp(-mu)
        for k in range(need): p += term; term *= mu / (k + 1)
        return max(0.0, 1 - p)
    rr = mu / (disp - 1); q = rr / (rr + mu); cdf = 0.0
    for k in range(need):
        cdf += math.exp(math.lgamma(k + rr) - math.lgamma(rr) - math.lgamma(k + 1) + rr * math.log(q) + k * math.log(1 - q))
    return max(0.0, 1 - cdf)

def remaining(period, clock):
    try: m, s = (clock.split(":") + ["0"])[:2] if ":" in clock else (0, clock); left_q = float(m) + float(s) / 60
    except Exception: left_q = 0.0
    if period > 4: return left_q
    return max(0.0, (4 - period) * 12 + left_q)

def boxes():
    out = []
    for e in get(ESPN + "/scoreboard").get("events", []):
        st = e["status"]
        if st["type"]["state"] != "in": continue
        s = get(f"{ESPN}/summary?event={e['id']}")
        comp = e["competitions"][0]["competitors"]; sc = [int(c.get("score") or 0) for c in comp]
        pl = {}
        for tm in s.get("boxscore", {}).get("players", []):
            ab = tm["team"]["abbreviation"]; blk = tm["statistics"][0]; nm = blk.get("names") or blk.get("labels")
            for a in blk.get("athletes", []):
                if a.get("didNotPlay") or not a.get("stats"): continue
                v = dict(zip(nm, a["stats"]))
                try:
                    cur = {"MIN": float(v["MIN"] or 0), "PTS": int(v["PTS"]), "REB": int(v["REB"]), "AST": int(v["AST"]),
                           "STL": int(v["STL"]), "BLK": int(v["BLK"]), "3PM": int(str(v["3PT"]).split("-")[0]), "PF": int(v["PF"])}
                except Exception: continue
                cur["PRA"] = cur["PTS"] + cur["REB"] + cur["AST"]
                pl[norm(a["athlete"]["displayName"])] = {"id": a["athlete"]["id"], "name": a["athlete"]["displayName"], "team": ab,
                                                          "starter": bool(a.get("starter")), "active": a.get("active", True), "cur": cur}
        teams = {c["team"]["abbreviation"].lower() for c in comp}
        out.append({"id": e["id"], "name": e["shortName"], "period": st["period"], "clock": st.get("displayClock", "0:00"),
                    "margin": abs(sc[0] - sc[1]), "teams": teams, "players": pl, "half": st["type"].get("shortDetail", "")})
    return out

def proj(p, g, r):
    rem_game = remaining(g["period"], g["clock"]); played = p["cur"]["MIN"]
    if g["half"].lower().startswith(("half", "end")) and g["period"] in (2,): pass
    exp_left = max(0.0, r["min"] * 1.0 - played) if rem_game > 0 else 0.0
    share = r["min"] / 48.0; exp_left = min(exp_left * 0.5 + share * rem_game * 0.5, rem_game)
    pf, per = p["cur"]["PF"], g["period"]
    if pf >= 5: exp_left *= 0.6
    elif (per == 1 and pf >= 2) or (per == 2 and pf >= 3) or (per == 3 and pf >= 4): exp_left *= 0.8
    if (per >= 4 and g["margin"] >= 18) or (per == 3 and g["margin"] >= 22): exp_left *= 0.6 if p["starter"] else 1.25
    return min(exp_left, rem_game)

def main():
    if os.environ.get("NBA_LIVE", "").lower() in ("off", "0", "false") or os.path.exists(os.path.join(HERE, "NBA_LIVE_OFF")):
        print("NBA live props OFF (switch)"); return
    # 2026-10-26: old "never trades" warning removed; paper fills now go through pm_fill (PM_FILL_NBA_LIVE)
    t0 = time.time()
    try: games = boxes()
    except Exception as ex: log(f"nba_live SKIP cycle: ESPN {type(ex).__name__}: {str(ex)[:160]}"); return
    if not games: print("no NBA games in progress"); return
    cache = json.load(open(RATES)) if os.path.exists(RATES) else {}
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    cl = PMPublic(); today = datetime.now(timezone(timedelta(hours=-4))).strftime("%Y-%m-%d")
    try: lev = cl.league_events("nba")
    except Exception as ex: log(f"nba_live SKIP cycle: Polymarket events {type(ex).__name__}: {str(ex)[:160]}"); return
    evs = [e for e in lev if not e.get("closed") and today in e.get("slug", "")]
    stats = {"games": len(games), "markets": 0, "matched": 0, "edge": 0, "logged": 0, "skip": {}}
    def sk(k): stats["skip"][k] = stats["skip"].get(k, 0) + 1
    for ev in evs:
        parts = ev["slug"].split("-"); al = {"was": "wsh", "gs": "gsw", "wsh": "wsh"}
        pt = {al.get(parts[1], parts[1]), al.get(parts[2], parts[2])}
        g = next((x for x in games if pt & x["teams"]), None)
        if not g: continue
        if time.time() - t0 > BUDGET: stats["budget_stop"] = True; break
        try: full = cl.event(ev["slug"]) or {}
        except Exception as ex: sk("event fetch error"); log(f"event {ev['slug']} skipped: {type(ex).__name__}: {str(ex)[:120]}"); continue
        for m in full.get("markets", []):
            if time.time() - t0 > BUDGET: stats["budget_stop"] = True; break
            stt = STAT.get(m.get("sportsMarketType"))
            if not stt or m.get("closed"): continue
            stats["markets"] += 1
            q = Q.match(m.get("question") or "")
            if not q: sk("parse"); continue
            p = g["players"].get(norm(q.group(1)))
            if not p: sk("no box player"); continue
            r = rates(p["id"], cache)
            if not r: sk("no rates"); continue
            stats["matched"] += 1
            thr = int(q.group(2)); need = thr - p["cur"][stt]
            if need <= 0: sk("already hit"); continue
            left = proj(p, g, r); mu = r[stt] * left
            pm = tail(mu, need, DISP[stt])
            b0, a0 = f(m.get("bestBidQuote")), f(m.get("bestAskQuote"))
            if b0 is None or a0 is None: sk("no quote"); continue
            mid = (b0 + a0) / 2
            pc = (1 - SHRINK) * pm + SHRINK * mid
            # 2026-10-31 (Marvin "Set the pacer at 62%"): NBA pacer = deciding model (no mid blend); any pacer error -> old
            # model above, logged. Off: PACER_KP_NBA_OFF (env or file in /opt/marv-feed[/live_props|/reports/prop_pacer])
            src, why = "old", "pacer off (PACER_KP_NBA_OFF)"
            try:
                import sys as _s
                if "/opt/marv-feed/reports/prop_pacer" not in _s.path: _s.path.insert(0, "/opt/marv-feed/reports/prop_pacer")
                import kp_pacer as KP
                if not KP.off("nba"):
                    o = KP.nba(g["id"], p["name"], stt, thr)
                    if "p_over" in o: pc = float(o["p_over"]); src, why = "pacer", None
                    else: why = o.get("err", "?")
            except Exception as ex:
                why = f"{type(ex).__name__}: {str(ex)[:80]}"
            for side, p_side, ask in (("long", pc, a0), ("short", 1 - pc, 1 - b0)):
                p_cap = min(p_side, CAP); fee = 0.0695 * ask * (1 - ask)
                if not gate_ok(p_cap, p_cap - ask - fee): continue
                stats["edge"] += 1
                pk = f"{g['id']}|{norm(p['name'])}|{stt}|{side}"
                if pk in state: sk("dup"); continue
                try: md = cl.bbo(m["slug"]) or {}
                except Exception as ex: sk("bbo error"); continue
                b, a = f(md.get("bestBid")), f(md.get("bestAsk"))
                ts = (md.get("lastPriceSample") or {}).get("ts")
                try: age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts[:26].rstrip("Z") + "+00:00")).total_seconds()
                except Exception: age = 999
                if md.get("state") != "MARKET_STATE_OPEN": sk("not open"); continue
                if b is None or a is None or age > MAX_AGE: sk("stale quote"); continue
                if a - b > MAX_SPREAD + 1e-9: sk("spread>3c"); continue
                px = a if side == "long" else round(1 - b, 4)
                depth = float(md.get("askShares") or 0) * a if side == "long" else float(md.get("bidShares") or 0) * (1 - b)
                if depth < STAKE: sk("depth<$50"); continue
                fee = 0.0695 * px * (1 - px); edge = min(p_side, CAP) - px - fee
                if not gate_ok(p_side, edge): sk("edge gone at refetch"); continue
                # 2026-10-25 audit (Marvin): long-shot quotes (<10c / >90c) on thin prop ladders were logged as $50 fills
                # (62 rows, 21 wins vs 3.3 priced = $19.8k of the $26.3k). Skip them. Off: PM_NBA_LIVE_BAND=off | data/PM_NBA_LIVE_BAND_OFF
                if (os.environ.get("PM_NBA_LIVE_BAND", "on").lower() not in ("off", "0", "false", "no")
                        and not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "PM_NBA_LIVE_BAND_OFF"))
                        and not 0.10 <= px <= 0.90): sk("price outside 10-90c"); continue
                c = round(STAKE / (px + fee), 2)   # 2026-10-25 bug fix: cost now includes the Polymarket fee (was c*px only)
                rec = {"date": datetime.now(CT).strftime("%Y-%m-%d"), "type": TYPE, "sport": "nba", "market": "prop",
                       "key": f"nbalive:{g['id']}:{norm(p['name'])}:{stt}:{thr}:{side}", "ticker": m["slug"], "slug": m["slug"],
                       "pick": f"{p['name']} {'' if side=='long' else 'NOT '}{thr}+ {stt}", "side": side, "price": px,
                       "contracts": c, "cost": round(c * (px + fee), 2), "fee": round(c * fee, 4), "stake": STAKE, "p_model_raw": round(pm, 4), "prob_src": src, "why_old": why,
                       "p_side": round(min(p_side, CAP), 4), "edge": round(edge, 4), "mu_rest": round(mu, 2), "need": need,
                       "cur": p["cur"][stt], "min_played": p["cur"]["MIN"], "exp_min_left": round(left, 1), "pf": p["cur"]["PF"],
                       "period": g["period"], "clock": g["clock"], "margin": g["margin"], "bid": b, "ask": a,
                       "quote_age_s": round(age, 1), "depth_usd": round(depth, 0), "game": g["name"]}
                # 2026-10-26 (Marvin "Let it trade"): realistic PAPER fill via pm_fill (60% cap, 3c edge, depth >= $50 at the
                # real ask checked above, 10-90c band, one prop per player per game, game+side dedupe, $2k/day cap).
                # Off: PM_FILL_NBA_LIVE=off | data/PM_FILL_NBA_LIVE_OFF. Track row still logged for lane ROI.
                try: KP.log({"bot": "kalpoly", "ex": "poly", "sport": "nba", "key": rec["key"], "pick": rec["pick"], "used": src, "p_side": rec["p_side"], "why_old": why, "dry": DRY})
                except Exception: pass
                # 2026-11-03 8:30 PM ET Marvin: NBA live prop paper FILLS need edge >= 10c; below -> track row only (still graded).
                # Off: PM_NBA_LIVE_MIN10=off | data/PM_NBA_LIVE_MIN10_OFF
                _min10 = (os.environ.get("PM_NBA_LIVE_MIN10", "on").lower() not in ("off", "0", "false", "no")
                          and not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "PM_NBA_LIVE_MIN10_OFF")))
                if _min10 and edge < 0.10:
                    stats["fill:min10_track"] = stats.get("fill:min10_track", 0) + 1; rec["min10"] = "track-only: edge < 10c"
                else:
                  try:
                    import pm_fill as PFL
                    ok, why = PFL.fill("NBA_LIVE", dict(rec, fair=p_side), "nba_prop_live", group_prefix=f"nbalive:{g['id']}:{norm(p['name'])}:", dry=DRY, gate={"cap": CAP, "p": P_GATE, "min_edge": MIN_EDGE})
                    stats["fill:" + why.split(" ")[0]] = stats.get("fill:" + why.split(" ")[0], 0) + 1
                    if DRY: log("DRY fill check: " + why)
                  except Exception as ex:
                    log(f"fill error {type(ex).__name__}: {str(ex)[:120]}")
                if DRY: log("DRY would log " + json.dumps(rec)); stats["logged"] += 1; continue
                if G.track(TRACK, rec): state[pk] = {"thr": thr, "ts": rec.get("ts") or datetime.now(CT).isoformat()}; stats["logged"] += 1
                else: sk("dup")
    if not DRY:
        json.dump(cache, open(RATES + ".tmp", "w")); os.replace(RATES + ".tmp", RATES)
        json.dump(state, open(STATE + ".tmp", "w")); os.replace(STATE + ".tmp", STATE)
    log(f"nba_live {'DRY ' if DRY else ''}{json.dumps(stats)}")

if __name__ == "__main__":
    try: main()
    except Exception as e: log(f"ERROR {type(e).__name__}: {str(e)[:200]}"); sys.exit(0)
 
