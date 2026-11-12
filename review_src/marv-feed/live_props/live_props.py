"""Multi-sport LIVE player props -- TRADING PRACTICE (2026-10-30 Marvin "mostly live prop bets ... 62% edge": Poly PAPER via pm_fill, Kalshi DEMO orders; gate p>=62% & edge>0 OR edge>=5c). Was TRACK-ONLY (Marvin 2026-10-30, "How about the other sports live props add those"; review Nov 7).
Copy of /opt/polymarket-paper/pm_nba_live.py logic (current stat + pregame per-game rate x remaining game fraction,
Poisson / overdispersed tail, 50% shrink to mid, 0.65 cap, edge >= 5c after fee, quote refetch: <=10 s old (Poly),
spread <= 3c, >= $50 depth at the ask, 10-90c band, one log per player/stat/side per game). [now trades, see top]
NO Telegram, NO Odds API credits (ESPN public + Polymarket public + Kalshi DEMO public market data only).
Lanes (type tag, graded by each exchange's track_grade.py, reported separately by lane_roi):
  poly:   nfl_prop_live, nhl_prop_live, epl_prop_live  -> /opt/polymarket-paper/data/track_only.jsonl
  kalshi: nfl_prop_live, nhl_prop_live                 -> /opt/kalshi-demo/data/track_only.jsonl
Kalshi NHL reuses the NHL live props logger's snapshots (/opt/marv-feed/nhl_live_props/data/snaps) for the market list,
then refetches only the candidate market's quote right before logging.
OFF: touch /opt/marv-feed/live_props/LIVE_PROPS_OFF (all) or LIVE_PROPS_<SPORT>_<EX>_OFF (e.g. LIVE_PROPS_NFL_KALSHI_OFF),
or env LIVE_PROPS=off.  Usage: live_props.py <nfl|nhl|epl> <poly|kalshi> [--dry]"""
import json, os, re, sys, math, glob, unicodedata, urllib.request
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
HERE = os.path.dirname(os.path.abspath(__file__)); DATA = os.path.join(HERE, "data"); os.makedirs(DATA, exist_ok=True)
PMDIR = "/opt/polymarket-paper"; KDIR = "/opt/kalshi-demo"; sys.path.insert(0, PMDIR)
CT = ZoneInfo("America/Chicago"); ET = ZoneInfo("America/New_York")
STAKE = 50.0; CAP = 0.65; MIN_EDGE = 0.05; SHRINK = 0.50; MAX_AGE = 10; MAX_SPREAD = 0.03
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
KAL = "https://demo-api.kalshi.co/trade-api/v2"
SPORTS = {
 "nfl": {"espn": "football/nfl", "len": 60.0, "per": 15.0, "nper": 4, "pmlg": "nfl",
         "pm": {"football_player_passing_yards": "PYD", "football_player_passing_touchdowns": "PTD", "football_player_passing_completions": "CMP",
                "football_player_passing_attempts": "ATT", "football_player_rushing_yards": "RYD", "football_player_rushing_attempts": "RAT",
                "football_player_receptions": "REC", "football_player_receiving_yards": "YDS", "football_player_touchdowns": "TD",
                "football_player_scrimmage_yards": "SCR"},
         "kx": {"KXNFLPASSYDS": "PYD", "KXNFLRSHYDS": "RYD", "KXNFLRECYDS": "YDS", "KXNFLREC": "REC"},
         "disp": {"PYD": 20.0, "RYD": 15.0, "YDS": 15.0, "SCR": 15.0, "CMP": 1.3, "ATT": 1.2, "RAT": 1.3, "REC": 1.3, "TD": 1.1, "PTD": 1.1}},
 "nhl": {"espn": "hockey/nhl", "len": 60.0, "per": 20.0, "nper": 3, "pmlg": "nhl",
         "pm": {"hockey_player_goals": "G", "hockey_player_assists": "A", "hockey_player_points": "PTS"},
         "kx": {"KXNHLGOAL": "G", "KXNHLAST": "A", "KXNHLPTS": "PTS"}, "disp": {"G": 1.0, "A": 1.05, "PTS": 1.1}},
 # 2026-10-30: NBA on Kalshi DEMO only (Poly NBA live = pm_nba_live.py). Uses pm_nba_live rates/box/minutes model.
 "nba": {"espn": "basketball/nba", "len": 48.0, "per": 12.0, "nper": 4, "pmlg": "nba", "pm": {},
         "kx": {"KXNBAPTS": "PTS", "KXNBAREB": "REB", "KXNBAAST": "AST", "KXNBA3PT": "3PM"},
         "disp": {"PTS": 1.8, "REB": 1.25, "AST": 1.25, "3PM": 1.2}},
 "epl": {"espn": "soccer/eng.1", "len": 90.0, "per": 45.0, "nper": 2, "pmlg": "epl",
         "pm": {"soccer_player_goals": "G"}, "kx": {}, "disp": {"G": 1.0}},
}
Q = re.compile(r"Will (.+?) (?:record|throw) (?:at least )?(\d+)\+?", re.I)
P_GATE = 0.62   # 2026-10-30 Marvin: trade if capped win prob >= 62% and edge > 0 after fee, OR edge >= MIN_EDGE
PACER_STAT = {"PYD": "PY", "RYD": "RY", "YDS": "RECY", "REC": "REC", "TD": "TD"}   # 2026-10-30 pacer live (NFL)
def _PL():
    d = "/opt/marv-feed/reports/prop_pacer"
    if d not in sys.path: sys.path.insert(0, d)
    import pacer_live; return pacer_live
def _KP():   # 2026-10-31 (Marvin "Set the pacer at 62%"): pacer = deciding model where it can price
    d = "/opt/marv-feed/reports/prop_pacer"
    if d not in sys.path: sys.path.insert(0, d)
    import kp_pacer; return kp_pacer
WHY = {}
def gate_ok(p_side, edge): p = min(p_side, CAP); return edge >= MIN_EDGE or (p >= P_GATE and edge > 0)
DRY = "--dry" in sys.argv
args = [a for a in sys.argv[1:] if not a.startswith("--")]
SP, EX = (args + ["", ""])[:2]
TYPE = f"{SP}_prop_live"; TAG = f"LIVE_PROPS_{SP.upper()}_{EX.upper()}"
LOG = os.path.join(DATA, f"{SP}_{EX}.log"); STATE = os.path.join(DATA, f"{SP}_{EX}_state.json"); RATES = os.path.join(DATA, f"{SP}_rates_cache.json")

def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r: return json.loads(r.read())
def norm(s): return re.sub(r"[^a-z ]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().replace(".", "").replace(" jr", "").replace(" iii", "").replace(" ii", "")).strip()
def num(x):
    try: return float(str(x).replace(",", "").split("/")[0].split("-")[0] if not str(x).startswith("-") else str(x).replace(",", ""))
    except Exception: return 0.0
def f(x):
    try: return float(x["value"] if isinstance(x, dict) else x)
    except Exception: return None
def log(m):
    line = f"{datetime.now(CT):%H:%M:%S} {m}"; print(line, flush=True)
    if not DRY:
        with open(LOG, "a") as fh: fh.write(line + "\n")

def tail(mu, need, disp):   # identical to pm_nba_live.tail
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

# ---------- per-game rates from ESPN season totals (prior season weighted <=10 games, current by games, as NBA lane) ----------
def rates(aid, cache):
    if aid in cache and cache[aid].get("d") == datetime.now(CT).strftime("%Y-%m-%d"): return cache[aid]
    try: d = get(f"https://site.web.api.espn.com/apis/common/v3/sports/{SPORTS[SP]['espn']}/athletes/{aid}/stats")
    except Exception: return None
    per = {}   # season index -> {stat: total, gp}
    for c in d.get("categories", []):
        nm = c.get("names") or []; rows = (c.get("statistics") or [])[-2:]
        for i, s in enumerate(rows):
            v = dict(zip(nm, s.get("stats", []))); slot = per.setdefault(i - len(rows) + 2, {})
            gp = num(v.get("gamesPlayed", v.get("games", v.get("appearances", 0))))
            if gp: slot["gp"] = max(slot.get("gp", 0), gp)
            m = {"passingYards": "PYD", "passingTouchdowns": "PTD", "completions": "CMP", "passingAttempts": "ATT", "rushingYards": "RYD",
                 "rushingAttempts": "RAT", "receptions": "REC", "receivingYards": "YDS", "totalTouchdowns": "TD", "goals": "G",
                 "totalGoals": "G", "assists": "A", "points": "PTS", "goalAssists": "A"}
            for k, key in m.items():
                if k in v and key not in slot: slot[key] = num(v[k])
    acc, wt = {}, 0.0
    ks = sorted(per)
    for j, i in enumerate(ks):
        s = per[i]; gp = s.get("gp", 0)
        if gp <= 0: continue
        w = gp if j == len(ks) - 1 or len(ks) == 1 else min(gp, 10.0)
        for k, x in s.items():
            if k != "gp": acc[k] = acc.get(k, 0) + w * x / gp
        wt += w
    if wt <= 0: return None
    r = {k: x / wt for k, x in acc.items()}; r["d"] = datetime.now(CT).strftime("%Y-%m-%d")
    if SP == "nfl": r["SCR"] = r.get("RYD", 0) + r.get("YDS", 0)
    if SP == "nhl" and "PTS" not in r: r["PTS"] = r.get("G", 0) + r.get("A", 0)
    cache[aid] = r; return r

def _gc():
    import importlib.util
    mod = getattr(_gc, "mod", None)
    if mod is not None:
        return mod
    here = os.path.dirname(os.path.abspath(__file__)); up = here; cands = []
    for _ in range(5):
        cands.append(os.path.join(up, "game_clock.py")); up = os.path.dirname(up)
    cands.append("/opt/marv-feed/game_clock.py")
    for c in cands:
        if os.path.isfile(c):
            spec = importlib.util.spec_from_file_location("game_clock_live_props", c)
            mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); _gc.mod = mod; return mod
    raise ImportError("game_clock.py")
def remaining(period, clock, sport=None):
    """Minutes left. None = ambiguous or unparseable clock; callers skip the bet.
    Colon-less floats are not minutes and not seconds."""
    sp = sport or SP
    c = SPORTS.get(sp)
    if not c:
        return None
    if sp == "epl":
        return _gc().epl_minutes_left(clock, length=c["len"])
    return _gc().game_minutes_left(period, clock, period_minutes=c["per"], n_periods=c["nper"])

def box_players(s):
    pl = {}
    if SP == "epl":
        for tm in s.get("rosters", []):
            ab = (tm.get("team") or {}).get("abbreviation", "")
            for a in tm.get("roster", []):
                st = {x.get("name"): x.get("value") for x in a.get("stats", [])}
                if not a.get("starter") and not a.get("subbedIn"): continue
                if a.get("subbedOut"): continue
                pl[norm(a["athlete"]["displayName"])] = {"id": a["athlete"]["id"], "name": a["athlete"]["displayName"], "team": ab,
                                                         "cur": {"G": int(num(st.get("totalGoals", 0)))}}
        return pl
    for tm in s.get("boxscore", {}).get("players", []):
        ab = tm["team"]["abbreviation"]
        for blk in tm.get("statistics", []):
            nm = blk.get("keys") or blk.get("names") or []
            for a in blk.get("athletes", []):
                if not a.get("stats"): continue
                v = dict(zip(nm, a["stats"])); k = norm(a["athlete"]["displayName"])
                p = pl.setdefault(k, {"id": a["athlete"]["id"], "name": a["athlete"]["displayName"], "team": ab, "cur": {}}); c = p["cur"]
                if SP == "nfl":
                    if "passingYards" in v:
                        c["PYD"] = num(v["passingYards"]); c["PTD"] = num(v.get("passingTouchdowns"))
                        ca = str(v.get("completions/passingAttempts", "0/0")).split("/"); c["CMP"] = num(ca[0]); c["ATT"] = num(ca[1] if len(ca) > 1 else 0)
                    if "rushingYards" in v: c["RYD"] = num(v["rushingYards"]); c["RAT"] = num(v.get("rushingAttempts")); c["rtd"] = num(v.get("rushingTouchdowns"))
                    if "receivingYards" in v: c["YDS"] = num(v["receivingYards"]); c["REC"] = num(v.get("receptions")); c["ctd"] = num(v.get("receivingTouchdowns"))
                else:
                    if "goals" in v: c["G"] = num(v["goals"]); c["A"] = num(v.get("assists")); c["PTS"] = c["G"] + c["A"]
    if SP == "nfl":
        for p in pl.values():
            c = p["cur"]
            for k in ("PYD", "PTD", "CMP", "ATT", "RYD", "RAT", "YDS", "REC"): c.setdefault(k, 0.0)
            c["TD"] = c.pop("rtd", 0.0) + c.pop("ctd", 0.0); c["SCR"] = c["RYD"] + c["YDS"]
    return pl

def games():
    if SP == "nba": return _NB().boxes()
    base = "https://site.api.espn.com/apis/site/v2/sports/" + SPORTS[SP]["espn"]; out = []
    for e in get(base + "/scoreboard").get("events", []):
        st = e["status"]
        if st["type"]["state"] != "in": continue
        s = get(f"{base}/summary?event={e['id']}")
        comp = e["competitions"][0]["competitors"]; sc = [int(c.get("score") or 0) for c in comp]
        out.append({"id": e["id"], "name": e["shortName"], "period": st["period"], "clock": st.get("displayClock", "0:00"),
                    "margin": abs(sc[0] - sc[1]), "sc": {c["team"]["abbreviation"].upper(): int(c.get("score") or 0) for c in comp}, "teams": {c["team"]["abbreviation"].lower() for c in comp}, "players": box_players(s)})
    return out

def evaluate(g, p, stt, thr, b0, a0, cache, stats, sk):
    """model -> list of (side, p_side, pm, mu, need, left) with edge vs the listed quote (same gates as NBA lane)."""
    r = (_NB().rates(p["id"], cache) if SP == "nba" else rates(p["id"], cache))
    if not r or stt not in r: sk("no rates"); return []
    stats["matched"] += 1
    cur = p["cur"].get(stt, 0); need = int(math.ceil(thr - cur - 1e-9))
    if need <= 0: sk("already hit"); return []
    left = remaining(g["period"], g["clock"])
    if left is None: sk("bad clock"); return []
    if SP == "nba":
        proj_left = _NB().proj(p, g, r)
        if proj_left is None: sk("bad clock"); return []
        mu = r[stt] * proj_left
    else:
        mu = r[stt] * left / SPORTS[SP]["len"]
    pm = tail(mu, need, SPORTS[SP]["disp"][stt]); mid = (b0 + a0) / 2; pc = (1 - SHRINK) * pm + SHRINK * mid
    src = "old"; wk = (g["id"], norm(p["name"]), stt, thr)
    WHY[wk] = "no calibrated pacer for " + SP if SP in ("nhl", "epl") else "pacer off (PACER_KP_%s_OFF)" % SP.upper()
    # 2026-10-30 (Marvin approved): NFL win prob = prop pacer calibrated prob (no mid blend) for PYD/RYD/YDS/REC/TD;
    # other stats + any pacer error -> current projection+50/50 blend. Off: PACER_NFL_OFF=1 env or file
    # /opt/marv-feed/reports/prop_pacer/PACER_NFL_OFF. Before 2026-10-31 ET: shadow (computed, not used).
    if SP == "nfl" and stt not in PACER_STAT: WHY[wk] = f"pacer does not cover NFL {stt}"
    if SP == "nfl" and stt in PACER_STAT and not _KP().off("nfl"):
        try:
            PL = _PL(); md = PL.mode("nfl")
            if md != "off":
                sc = g.get("sc") or {}; tm = str(p.get("team", "")).upper()
                lead = sc.get(tm, 0) - sum(v for k, v in sc.items() if k != tm)
                pp = PL.prob("nfl", PACER_STAT[stt], thr, cur, r[stt], 1 - left / SPORTS[SP]["len"], lead)
                stats[f"pacer_{md}"] = stats.get(f"pacer_{md}", 0) + 1
                if md == "live": pc = pp; src = "pacer"
                else: WHY[wk] = f"pacer_live mode {md}"
        except Exception as ex:
            WHY[wk] = f"{type(ex).__name__}: {str(ex)[:80]}"; stats["pacer_err"] = stats.get("pacer_err", 0) + 1; log(f"pacer fallback {p.get('name')} {stt} {thr}: {type(ex).__name__} {str(ex)[:80]}")
    if SP == "nba" and not _KP().off("nba"):
        try:
            o = _KP().nba(g["id"], p["name"], stt, thr)
            if "p_over" in o: pc = float(o["p_over"]); src = "pacer"; stats["pacer_live"] = stats.get("pacer_live", 0) + 1
            else: WHY[wk] = o.get("err", "?"); stats["pacer_err"] = stats.get("pacer_err", 0) + 1
        except Exception as ex:
            WHY[wk] = f"{type(ex).__name__}: {str(ex)[:80]}"; stats["pacer_err"] = stats.get("pacer_err", 0) + 1
    out = []
    for side, p_side, ask in (("long", pc, a0), ("short", 1 - pc, 1 - b0)):
        fee = FEE * ask * (1 - ask)
        if gate_ok(p_side, min(p_side, CAP) - ask - fee): stats["edge"] += 1; out.append((side, p_side, pm, mu, need, left, cur, src))
    return out

def emit(rec, pk, state, stats, track_fn):
    try:
        k = rec["key"].split(":"); wk = (k[1], k[2], k[3], int(k[4]))
        _KP().log({"bot": "kalpoly", "ex": EX, "sport": SP, "key": rec["key"], "pick": rec.get("pick"), "used": rec.get("prob_src"),
                   "p_side": rec.get("p_side", (rec.get("fair_c") or 0) / 100), "why_old": None if rec.get("prob_src") == "pacer" else WHY.get(wk), "dry": DRY})
    except Exception: pass
    if DRY: log("DRY would log " + json.dumps(rec)); stats["logged"] += 1; return
    if track_fn(rec): state[pk] = datetime.now(CT).isoformat(); stats["logged"] += 1
    else: stats["skip"]["dup"] = stats["skip"].get("dup", 0) + 1

def band_ok(px): return 0.10 <= px <= 0.90

def _NB():
    import pm_nba_live as NB; return NB

def poly_fill(rec, g, p, p_side, stats):
    """2026-10-30 (Marvin "mostly live prop bets"): Polymarket PAPER fill via pm_fill (same path as NBA live).
    Off: PM_FILL_<SPORT>_LIVE=off | /opt/polymarket-paper/data/PM_FILL_<SPORT>_LIVE_OFF."""
    try:
        import pm_fill as PFL
        ok, why = PFL.fill(f"{SP.upper()}_LIVE", dict(rec, fair=min(p_side, CAP)), TYPE, me_type="marv",
                           group_prefix=f"{SP}live:{g['id']}:{norm(p['name'])}:", dry=DRY, gate={"cap": CAP, "p": P_GATE, "min_edge": MIN_EDGE})
        k = "fill:" + why.split(" ")[0]; stats[k] = stats.get(k, 0) + 1
        if DRY: log("DRY fill check: " + why)
    except Exception as ex: log(f"fill error {type(ex).__name__}: {str(ex)[:120]}")

_KC = {}
def kx_order(rec, stats):
    """2026-10-30: Kalshi DEMO order (existing KalshiDemo.place_order IOC path + marv_cbb_kalshi guards: kill/autogate,
    DEMO_AUTO+key, ticker held/bought today, game+side, max orders/run, 10-90c). Off: KALSHI_<SPORT>_LIVE_TRADE=off |
    /opt/kalshi-demo/data/KALSHI_<SPORT>_LIVE_TRADE_OFF (or KALSHI_TRADE_ALL off)."""
    def st(k): stats["order:" + k] = stats.get("order:" + k, 0) + 1
    try:
        if KDIR not in sys.path: sys.path.insert(0, KDIR)
        import marv_cbb_kalshi as MC, me_kalshi as MK, type_gate as KTG
        from kalshi_demo import KalshiDemo
        if not (MK._sw(f"KALSHI_{SP.upper()}_LIVE_TRADE") and KTG.trade_all()): return st("lane_off")
        if hasattr(KTG, "force_track") and KTG.force_track(TYPE): return st("force_track")   # 2026-11-02 Marvin (KALSHI_FORCE_TRACK=off to undo); still tracked via emit()
        c = _KC.setdefault("c", KalshiDemo()); g = MC._guard(c); cfg = g["cfg"]
        if g["kill"]: return st("kill")
        if not (cfg.get("DEMO_AUTO") and c.has_key): return st("no_key")
        if rec["ticker"] in g["skip"]: return st("held")
        skk = g["SG"].side_key(rec["ticker"], rec.get("pick"))
        if skk and skk in g["sides"]: return st("game_side_held")
        if g["placed"] >= cfg["max_orders_per_run"]: return st("max_per_run")
        ask = int(rec["ask"])
        if not 10 <= ask <= 90: return st("band")
        # 2026-11-03 Marvin: Kalshi NBA live favorites (ask > 50c) track-only (still tracked via emit()).
        # Off: KALSHI_NBA_LIVE_FAV_TRACK=off | /opt/kalshi-demo/data/KALSHI_NBA_LIVE_FAV_TRACK_OFF
        if SP == "nba" and ask > 50 and os.environ.get("KALSHI_NBA_LIVE_FAV_TRACK", "on").lower() not in ("off", "0", "false", "no") \
                and not os.path.exists(os.path.join(KDIR, "data", "KALSHI_NBA_LIVE_FAV_TRACK_OFF")): return st("nba_fav_track")
        # 2026-11-03 8:30 PM ET Marvin: NBA live orders need edge >= 10c (still tracked via emit()). Off: KALSHI_NBA_LIVE_MIN10=off | /opt/kalshi-demo/data/KALSHI_NBA_LIVE_MIN10_OFF
        if SP == "nba" and float(rec.get("edge_c") or 0) < 10 and os.environ.get("KALSHI_NBA_LIVE_MIN10", "on").lower() not in ("off", "0", "false", "no") \
                and not os.path.exists(os.path.join(KDIR, "data", "KALSHI_NBA_LIVE_MIN10_OFF")): return st("nba_min10_track")
        n = max(1, int(STAKE * 100 // ask))
        if DRY: log(f"DRY WOULD ORDER {rec['ticker']} {rec['side']} {n}@{ask}c edge {rec['edge_c']}c p {rec['fair_c']}"); return st("would")
        o = dict(rec, ts=datetime.now(CT).isoformat(), mode="demo", src="live_props", bet_type=TYPE, reason=None)
        try:
            o["order"] = c.place_order(rec["ticker"], rec["side"], n, ask, **({"live_meta": o} if getattr(c, "LIVE_HOOK", False) else {})); g["placed"] += 1; g["skip"].add(rec["ticker"])   # 2026-11-10 live_meta: lane for the LIVE mirror (no-op unless LIVE=1)
            if skk: g["sides"].add(skk)
            st("placed")
        except Exception as ex: o["error"] = str(ex)[:200]; st("error")
        with open(g["LOG"], "a") as fh: fh.write(json.dumps(o) + "\n")
    except Exception as ex: log(f"order error {type(ex).__name__}: {str(ex)[:120]}"); st("exc")

def run_poly(gs, cache, state, stats, sk):
    from pm_client import PMPublic
    import pm_type_gate as G
    cl = PMPublic(); today = datetime.now(timezone(timedelta(hours=-4))).strftime("%Y-%m-%d")
    track = lambda rec: G.track(os.path.join(PMDIR, "data", "track_only.jsonl"), rec)
    for ev in [e for e in cl.league_events(SPORTS[SP]["pmlg"]) if not e.get("closed") and today in e.get("slug", "")]:
        parts = ev["slug"].split("-"); pt = {parts[1], parts[2]}
        g = next((x for x in gs if pt & x["teams"] or any(t[:2] == q[:2] for t in x["teams"] for q in pt)), None)
        if not g: continue
        for m in (cl.event(ev["slug"]) or {}).get("markets", []):
            stt = SPORTS[SP]["pm"].get(m.get("sportsMarketType"))
            if not stt or m.get("closed"): continue
            stats["markets"] += 1
            q = Q.match(m.get("question") or "")
            if not q: sk("parse"); continue
            p = g["players"].get(norm(q.group(1)))
            if not p: sk("no box player"); continue
            thr = int(q.group(2)); b0, a0 = f(m.get("bestBidQuote")), f(m.get("bestAskQuote"))
            if b0 is None or a0 is None: sk("no quote"); continue
            for side, p_side, pm, mu, need, left, cur, src in evaluate(g, p, stt, thr, b0, a0, cache, stats, sk):
                pk = f"{g['id']}|{norm(p['name'])}|{stt}|{side}"
                if pk in state: sk("dup"); continue
                md = cl.bbo(m["slug"]); b, a = f(md.get("bestBid")), f(md.get("bestAsk")); ts = (md.get("lastPriceSample") or {}).get("ts")
                try: age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts[:26].rstrip("Z") + "+00:00")).total_seconds()
                except Exception: age = 999
                if md.get("state") != "MARKET_STATE_OPEN": sk("not open"); continue
                if b is None or a is None or age > MAX_AGE: sk("stale quote"); continue
                if a - b > MAX_SPREAD + 1e-9: sk("spread>3c"); continue
                px = a if side == "long" else round(1 - b, 4)
                depth = float(md.get("askShares") or 0) * a if side == "long" else float(md.get("bidShares") or 0) * (1 - b)
                if depth < STAKE: sk("depth<$50"); continue
                fee = FEE * px * (1 - px); edge = min(p_side, CAP) - px - fee
                if not gate_ok(p_side, edge): sk("edge gone at refetch"); continue
                if not band_ok(px): sk("price outside 10-90c"); continue
                c = round(STAKE / (px + fee), 2)
                rec = {"date": datetime.now(CT).strftime("%Y-%m-%d"), "type": TYPE, "lane": TYPE, "sport": SP, "market": "prop",
                       "key": f"{SP}live:{g['id']}:{norm(p['name'])}:{stt}:{thr}:{side}", "ticker": m["slug"], "slug": m["slug"],
                       "pick": f"{p['name']} {'' if side=='long' else 'NOT '}{thr}+ {stt}", "side": side, "price": px, "contracts": c,
                       "cost": round(c * (px + fee), 2), "fee": round(c * fee, 4), "stake": STAKE, "p_model_raw": round(pm, 4), "prob_src": src,
                       "p_side": round(min(p_side, CAP), 4), "edge": round(edge, 4), "mu_rest": round(mu, 2), "need": need, "cur": cur,
                       "min_left": round(left, 1), "period": g["period"], "clock": g["clock"], "margin": g["margin"], "bid": b, "ask": a,
                       "quote_age_s": round(age, 1), "depth_usd": round(depth, 0), "game": g["name"]}
                poly_fill(rec, g, p, p_side, stats)
                emit(rec, pk, state, stats, track)

def kx_markets(g):
    """Kalshi DEMO markets for this game. NHL: from the NHL live props logger's latest snapshot (reuse, no extra polling)."""
    out = []
    if SP == "nhl":
        files = sorted(glob.glob("/opt/marv-feed/nhl_live_props/data/snaps/*.jsonl"))
        for fn in files[-2:]:
            last = {}
            for ln in open(fn):
                try: r = json.loads(ln)
                except Exception: continue
                for ser, blk in (r.get("kalshi") or {}).items():
                    ev = blk.get("ev", "")
                    if all(t.upper()[:2] in ev.upper() for t in g["teams"]) and ser in SPORTS[SP]["kx"]: last[ser] = (r.get("ts"), blk)
            for ser, (ts, blk) in last.items():
                for m in blk.get("m", []):
                    out.append({"ticker": m.get("t"), "series": ser, "strike": m.get("k"), "title": m.get("title"), "status": m.get("st"), "yb": m.get("yb"), "ya": m.get("ya")})
        if out: return out   # else fall through to a direct DEMO listing (logger snapshot not written yet)
    for ser in SPORTS[SP]["kx"]:
        cur = None
        for _ in range(5):
            d = get(f"{KAL}/markets?series_ticker={ser}&status=open&limit=1000" + (f"&cursor={cur}" if cur else ""))
            for m in d.get("markets", []):
                suf = m["event_ticker"].split("-")[1][7:].lower()
                if all(any(t[:2] == x for x in (suf[:2], suf[2:4], suf[3:5], suf[-2:], suf[-3:-1])) or t in suf for t in g["teams"]):
                    out.append({"ticker": m["ticker"], "series": ser, "strike": m.get("floor_strike"), "title": m.get("title"), "status": "open", "yb": f(m.get("yes_bid_dollars")), "ya": f(m.get("yes_ask_dollars"))})
            cur = d.get("cursor")
            if not cur: break
    return out

def kx_quote(t):
    m = get(f"{KAL}/markets/{t}").get("market", {})
    yb, ya = f(m.get("yes_bid_dollars")), f(m.get("yes_ask_dollars"))
    try: ob = get(f"{KAL}/markets/{t}/orderbook").get("orderbook_fp") or {}
    except Exception: ob = {}
    def lv(k):  # best level qty from the opposite book (yes ask = 1 - best no bid)
        L = ob.get(k) or []
        return float(L[-1][1]) if L else 0.0
    return m, yb, ya, lv("no_dollars"), lv("yes_dollars")

def run_kalshi(gs, cache, state, stats, sk):
    import importlib.util
    spec = importlib.util.spec_from_file_location("ktg", os.path.join(KDIR, "type_gate.py")); TG = importlib.util.module_from_spec(spec)
    try: spec.loader.exec_module(TG); track = lambda rec: TG.track(os.path.join(KDIR, "data", "track_only.jsonl"), rec)
    except Exception:
        def track(rec):
            pth = os.path.join(KDIR, "data", "track_only.jsonl"); sig = (rec["type"], rec["key"], rec["ticker"], rec["date"])
            for ln in open(pth):
                try: r = json.loads(ln)
                except Exception: continue
                if (r.get("type"), r.get("key"), r.get("ticker"), r.get("date")) == sig: return False
            with open(pth, "a") as fh: fh.write(json.dumps(dict(rec, ts=datetime.now(CT).isoformat(), mode="track")) + "\n")
            return True
    for g in gs:
        try: ms = kx_markets(g)
        except Exception as ex: sk("kalshi list err"); continue
        for mk in ms:
            if mk["status"] not in ("open", "active"): continue
            stt = SPORTS[SP]["kx"][mk["series"]]; stats["markets"] += 1
            # ticker ...-<TEAM><FIRSTINITIAL><LAST><NO>-<thr>; title "Name: N+ ..."
            p = None
            if mk.get("title"): p = g["players"].get(norm(mk["title"].split(":")[0]))
            if not p:
                pp = mk["ticker"].split("-")[-2].lower(); pp = re.sub(r"\d+$", "", pp)
                p = next((v for k, v in g["players"].items() if pp.endswith(re.sub(r"[^a-z]", "", (k.split()[0][:1] + "".join(k.split()[1:]))))), None)
            if not p: sk("no box player"); continue
            thr = int(math.floor(float(mk["strike"]) + 1)) if mk.get("strike") is not None else None
            if thr is None: sk("parse"); continue
            b0, a0 = mk.get("yb"), mk.get("ya")   # screen on listed quote; refetch only candidates
            if not b0 or not a0: sk("no quote"); continue
            for side, p_side, pm, mu, need, left, cur, src in evaluate(g, p, stt, thr, b0, a0, cache, stats, sk):
                pk = f"{g['id']}|{norm(p['name'])}|{stt}|{side}"
                if pk in state: sk("dup"); continue
                m, b, a, ask_qty, bid_qty = kx_quote(mk["ticker"])   # refetch right before logging
                if m.get("status") not in ("active", "open"): sk("not open"); continue
                if not b or not a: sk("stale quote"); continue
                if a - b > MAX_SPREAD + 1e-9: sk("spread>3c"); continue
                px = a if side == "long" else round(1 - b, 4)
                depth = ask_qty * a if side == "long" else bid_qty * (1 - b)
                if depth < STAKE: sk("depth<$50"); continue
                fee = FEE * px * (1 - px); edge = min(p_side, CAP) - px - fee
                if not gate_ok(p_side, edge): sk("edge gone at refetch"); continue
                if not band_ok(px): sk("price outside 10-90c"); continue
                c = int(STAKE // (px + fee))
                rec = {"date": datetime.now(CT).strftime("%Y-%m-%d"), "type": TYPE, "lane": TYPE, "bet_type": TYPE, "sport": SP,
                       "key": f"{SP}live:{g['id']}:{norm(p['name'])}:{stt}:{thr}:{side}", "ticker": mk["ticker"],
                       "pick": f"{p['name']} {'' if side=='long' else 'NOT '}{thr}+ {stt}", "side": "yes" if side == "long" else "no",
                       "ask": round(px * 100), "fair_c": round(min(p_side, CAP) * 100, 1), "edge_c": round(edge * 100, 2), "stake": STAKE,
                       "contracts": c, "reason": f"live prop: {TYPE}", "p_model_raw": round(pm, 4), "prob_src": src, "mu_rest": round(mu, 2), "need": need,
                       "cur": cur, "min_left": round(left, 1), "period": g["period"], "clock": g["clock"], "margin": g["margin"],
                       "bid": b, "ask_px": a, "depth_usd": round(depth, 0), "game": g["name"]}
                kx_order(rec, stats)
                emit(rec, pk, state, stats, track)

def main():
    global FEE
    if SP not in SPORTS or EX not in ("poly", "kalshi") or (EX == "kalshi" and not SPORTS[SP]["kx"]) or (EX == "poly" and not SPORTS[SP]["pm"]): print("usage: live_props.py <nfl|nhl|epl|nba> <poly|kalshi> [--dry]"); return
    if os.environ.get("LIVE_PROPS", "").lower() in ("off", "0", "false") or os.path.exists(os.path.join(HERE, "LIVE_PROPS_OFF")) \
            or os.environ.get(TAG, "").lower() in ("off", "0", "false") or os.path.exists(os.path.join(HERE, TAG + "_OFF")):
        print(f"{TAG} OFF (switch)"); return
    FEE = 0.0695 if EX == "poly" else 0.07
    gs = games()
    if not gs: print(f"{TAG}: no {SP} games in progress"); return
    cache = json.load(open(RATES)) if os.path.exists(RATES) else {}
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    stats = {"games": len(gs), "markets": 0, "matched": 0, "edge": 0, "logged": 0, "skip": {}}
    def sk(k): stats["skip"][k] = stats["skip"].get(k, 0) + 1
    (run_poly if EX == "poly" else run_kalshi)(gs, cache, state, stats, sk)
    if stats["markets"] == 0: stats["note"] = "no listed live player-prop markets for in-progress games"
    if not DRY:
        json.dump(cache, open(RATES + ".tmp", "w")); os.replace(RATES + ".tmp", RATES)
        json.dump(state, open(STATE + ".tmp", "w")); os.replace(STATE + ".tmp", STATE)
    log(f"{TAG} {'DRY ' if DRY else ''}{json.dumps(stats)}")

if __name__ == "__main__":
    try: main()
    except Exception as e: log(f"ERROR {type(e).__name__}: {str(e)[:200]}"); sys.exit(0)
