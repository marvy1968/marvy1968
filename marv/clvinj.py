"""Marv CLV + injury/lineup guard (PAPER ONLY). Standalone: python -m marv.clvinj [clv|injuries|all|report].

CLV: closing line = last pre-game line in March_edge's line_history (ajbet.db, read-only) before kickoff:
Odds API consensus > ESPN(DraftKings) > Bovada. No new Odds API credits (re-uses March_edge's pulls).
Injuries: ESPN summary (free) per game: injuries + season leaders (QB/top rusher/top receiver, top scorer)
+ soccer lineups when posted. Re-check window 50-100 min pre-kick. Key player Out/Doubtful with news dated after
the pick -> pick marked result='void-injury' in marv_paper.db and listed in state/void_injury.json (pick_feed skips).
Everything logged in state/marv_clv.db (tables clv, injury_snap, pick_flags)."""
import json, os, re, sqlite3, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

STATE = os.environ.get("MARV_STATE", "/opt/marv-bot/state")
LEDGER = os.path.join(STATE, "marv_paper.db")
OWN = os.path.join(STATE, "marv_clv.db")
VOID = os.path.join(STATE, "void_injury.json")
AJDB = os.environ.get("AJ_DB", "/home/moonrider1968/aj-bet-bot/ajbet.db")
KALSHI_LOG = "/opt/kalshi-demo/data/demo_log.jsonl"
PM_DB = "/opt/polymarket-paper/data/pm_paper.db"
ESPN = {"nfl": ("football/nfl", "nfl"), "cfb": ("football/college-football", "ncaaf"),
        "wnba": ("basketball/wnba", "wnba"), "nba": ("basketball/nba", "nba")}
PTS = {"nfl": (2.5, 2.0), "cfb": (2.0, 1.5), "wnba": (1.5, 1.0), "nba": (1.5, 1.0)}  # CLV % per point (spread, total)
KEY_CATS = {"passingYards", "rushingYards", "receivingYards", "points", "goals", "assists", "rating"}
BAD = {"out", "doubtful", "inactive", "suspension", "suspended"}
WIN = (50, 100)  # re-check window, minutes before start
UTC = timezone.utc
_cache = {}


def get(url):
    if url in _cache:
        return _cache[url]
    for i in range(2):
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20))
            _cache[url] = r
            return r
        except Exception:
            time.sleep(1)
    _cache[url] = {}
    return {}


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower().replace("&", "and")).strip()


def ts(x):
    try:
        d = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except Exception:
        return None


def own():
    c = sqlite3.connect(OWN, timeout=30)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS clv (key TEXT PRIMARY KEY, src TEXT, sport TEXT, event_id TEXT, start TEXT, market TEXT,
      pick TEXT, line REAL, price REAL, close_line REAL, close_price REAL, close_fair_p REAL, close_feed TEXT,
      close_ts TEXT, clv_pct REAL, beat INTEGER, note TEXT, computed_at TEXT);
    CREATE TABLE IF NOT EXISTS injury_snap (event_id TEXT, sport TEXT, team TEXT, player TEXT, pos TEXT, status TEXT,
      news_ts TEXT, key_player INTEGER, seen_at TEXT, PRIMARY KEY (event_id, team, player, status));
    CREATE TABLE IF NOT EXISTS pick_flags (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, sport TEXT, event_id TEXT,
      pick TEXT, market TEXT, team TEXT, player TEXT, pos TEXT, status TEXT, action TEXT, reason TEXT, at TEXT);
    """)
    return c


# ------------------------------------------------------------------ ESPN
def events(sport, day):
    path = ESPN[sport][0]
    extra = "&groups=80&limit=300" if sport == "cfb" else ""
    d = get(f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={day:%Y%m%d}{extra}")
    out = []
    for e in d.get("events", []) or []:
        comp = (e.get("competitions") or [{}])[0]
        t = {c.get("homeAway"): c.get("team", {}) for c in comp.get("competitors", [])}
        out.append({"id": e["id"], "start": ts(e.get("date")), "home": t.get("home", {}), "away": t.get("away", {})})
    return out


def tmatch(name, team):
    n = norm(name)
    if not n:
        return False
    cands = {norm(team.get(k)) for k in ("displayName", "shortDisplayName", "location", "abbreviation")} - {""}
    full = norm(team.get("displayName"))
    return n in cands or (len(n) > 4 and (full.startswith(n) or n.startswith(full)))


def find_event(sport, home, away, start, pick=None):
    st = start or datetime.now(UTC)
    for off in (0, -1, 1):
        for e in events(sport, (st + timedelta(days=off)).astimezone(timezone(timedelta(hours=-5)))):
            if home and away and tmatch(home, e["home"]) and tmatch(away, e["away"]):
                return e
            if pick and not (home and away) and (tmatch(pick, e["home"]) or tmatch(pick, e["away"])):
                if not start or abs((e["start"] - start).total_seconds()) < 36 * 3600:
                    return e
    return None


# ------------------------------------------------------------------ close
def close_rows(league, eid, start):
    c = sqlite3.connect(f"file:{AJDB}?mode=ro", uri=True, timeout=30)
    rows = c.execute("SELECT market, feed, value, ts_utc FROM line_history WHERE event_id=? AND league=? AND phase='pre' "
                     "AND ts_utc<=? ORDER BY id", (str(eid), league, start.strftime("%Y-%m-%dT%H:%M:%SZ"))).fetchall()
    c.close()
    last = {}
    for m, f, v, t in rows:
        last[(m, f)] = (v, t)
    out = {}
    for m in ("ml_home", "ml_away", "spread", "total"):
        for f in ("oddsapi", "espn", "bovada"):
            if (m, f) in last and last[(m, f)][0] is not None:
                out[m] = (last[(m, f)][0], f, last[(m, f)][1])
                break
    return out


def imp(a):
    a = float(a)
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def dec(a):
    a = float(a)
    return 1 + (a / 100 if a > 0 else 100 / -a)


def side_is_home(pick, e):
    if tmatch(pick, e["home"]):
        return True
    if tmatch(pick, e["away"]):
        return False
    return None


def compute(sport, market, pick, line, price, e, cl, prob=None):
    """-> (clv_pct, close_line, close_price, fair_p, feed, ts, note) or None"""
    sp, tp = PTS.get(sport, (2.0, 1.5))
    if market in ("ml", "h2h", "upset"):
        h = side_is_home(pick, e)
        if h is None or "ml_home" not in cl or "ml_away" not in cl:
            return None
        mh, ma = cl["ml_home"][0], cl["ml_away"][0]
        ph, pa = imp(mh), imp(ma)
        fair = (ph if h else pa) / (ph + pa)
        cp = mh if h else ma
        if prob is not None:  # exchange fill: probability price
            clv = (fair / prob - 1) * 100
        elif price is not None:
            clv = (dec(price) / dec(cp) - 1) * 100  # price CLV vs same-side close
        else:
            return None
        ev = (dec(price) * fair - 1) * 100 if prob is None else clv
        return clv, None, cp, fair, cl["ml_home"][1], cl["ml_home"][2], f"ml price CLV; EV@no-vig close {ev:+.1f}%"
    if market in ("ats", "spread"):
        h = side_is_home(pick, e)
        if h is None or "spread" not in cl or line is None:
            return None
        cs = cl["spread"][0] if h else -cl["spread"][0]
        pts = line - cs  # got more points than close -> positive
        return pts * sp, cs, None, None, cl["spread"][1], cl["spread"][2], f"spread {pts:+.1f} pts x{sp}%/pt"
    if market == "total":
        if "total" not in cl or line is None:
            return None
        ct = cl["total"][0]
        o = (pick or "").lower().startswith("o")
        pts = (ct - line) if o else (line - ct)
        return pts * tp, ct, None, None, cl["total"][1], cl["total"][2], f"total {pts:+.1f} pts x{tp}%/pt"
    return None


def run_clv(now=None):
    now = now or datetime.now(UTC)
    o = own()
    done = {r[0] for r in o.execute("SELECT key FROM clv WHERE clv_pct IS NOT NULL")}
    todo = []
    L = sqlite3.connect(LEDGER, timeout=30)
    for k, sport, home, away, market, pick, line, price, start, res in L.execute(
            "SELECT key,sport,home,away,market,pick,line,price,start,result FROM picks"):
        todo.append(("marv", k, sport, home, away, market, pick, line, price, None, ts(start)))
    try:
        for l in open(KALSHI_LOG):
            r = json.loads(l)
            if r.get("mode") != "demo" or not r.get("order") or not str(r.get("key", "")).startswith("h2h:"):
                continue
            fp = float(r["order"].get("average_fill_price") or 0)
            if fp > 0 and float(r["order"].get("fill_count") or 0) > 0:
                todo.append(("kalshi", "kalshi:" + r["order"]["order_id"], r["key"].split(":")[1], None, None, "ml",
                             r["pick"], None, None, fp, ts(r["ts"])))
    except FileNotFoundError:
        pass
    try:
        P = sqlite3.connect(f"file:{PM_DB}?mode=ro", uri=True)
        for i, t, k, pick, price in P.execute("SELECT id,ts,key,pick,price FROM fills WHERE key LIKE 'h2h:%'"):
            todo.append(("polymarket", f"pm:{i}", k.split(":")[1], None, None, "ml", pick, None, None, price, ts(t)))
    except Exception:
        pass
    n = 0
    for src, k, sport, home, away, market, pick, line, price, prob, start in todo:
        if k in done or sport not in ESPN or (src == "marv" and start and start > now):
            continue
        e = find_event(sport, home, away, start if src == "marv" else start, pick)
        if not e or not e["start"] or e["start"] > now:
            continue  # not kicked off yet: close not final
        cl = close_rows(ESPN[sport][1], e["id"], e["start"])
        r = compute(sport, market, pick, line, price, e, cl, prob)
        if not r:
            o.execute("INSERT OR REPLACE INTO clv (key,src,sport,event_id,start,market,pick,line,price,note,computed_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (k, src, sport, e["id"], e["start"].isoformat(), market, pick,
                                                       line, price if prob is None else prob, "no close in line_history",
                                                       now.isoformat()))
            continue
        clv, cline, cprice, fair, feed, cts, note = r
        o.execute("INSERT OR REPLACE INTO clv VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (k, src, sport, e["id"], e["start"].isoformat(), market, pick, line, price if prob is None else prob,
                   cline, cprice, fair, feed, cts, round(clv, 3), int(clv > 0), note, now.isoformat()))
        if src == "marv":  # fill the ledger's empty close columns only (never overwrite)
            L.execute("UPDATE picks SET close_line=COALESCE(close_line,?), close_price=COALESCE(close_price,?) WHERE key=?",
                      (cline, cprice, k))
        n += 1
    o.commit(); L.commit()
    return n


def clv_summary(src=None, days=None):
    o = own()
    q, a = "SELECT clv_pct FROM clv WHERE clv_pct IS NOT NULL", []
    if src:
        q += " AND src=?"; a.append(src)
    if days:
        q += " AND start>=?"; a.append((datetime.now(UTC) - timedelta(days=days)).isoformat())
    v = [r[0] for r in o.execute(q, a)]
    if not v:
        return None
    dec_ = [x for x in v if abs(x) > 1e-9]  # beat-close rate excludes exact ties (same number at close)
    return sum(v) / len(v), (100 * sum(x > 0 for x in dec_) / len(dec_)) if dec_ else 0.0, len(v), len(v) - len(dec_)


def clv_line():
    """'CLV: +x.x% avg, beat close y%' for /reportcard (Marv picks; demo fills separately)."""
    try:
        s = clv_summary("marv")
        if not s:
            return "CLV: no graded closes yet"
        out = f"CLV: {s[0]:+.1f}% avg, beat close {s[1]:.0f}% (n={s[2]}, {s[3]} unchanged)"
        d = [x for x in (("Kalshi demo", clv_summary("kalshi")), ("Poly paper", clv_summary("polymarket"))) if x[1]]
        if d:
            out += " · " + " · ".join(f"{n} {x[0]:+.1f}%/{x[1]:.0f}% (n={x[2]})" for n, x in d)
        return out
    except Exception as ex:
        return f"CLV: unavailable ({str(ex)[:40]})"


# ------------------------------------------------------------------ injuries
def summary(sport, eid):
    return get(f"https://site.api.espn.com/apis/site/v2/sports/{ESPN[sport][0]}/summary?event={eid}")


def key_players(s):
    """team displayName -> set(norm player) from season leaders (QB / top RB / top WR / star)."""
    out = {}
    for t in s.get("leaders", []) or []:
        nm = t.get("team", {}).get("displayName", "")
        for c in t.get("leaders", []) or []:
            if c.get("name") in KEY_CATS and c.get("leaders"):
                out.setdefault(nm, set()).add(norm(c["leaders"][0]["athlete"]["displayName"]))
    return out


def injuries(sport, e, now):
    s = summary(sport, e["id"])
    keys = key_players(s)
    rows = []
    for t in s.get("injuries", []) or []:
        team = t.get("team", {}).get("displayName", "")
        for i in t.get("injuries", []) or []:
            st = (i.get("status") or "").strip()
            if st.lower() in ("injured reserve", "active", "physically unable to perform", ""):
                continue  # long-term, already priced
            a = i.get("athlete", {})
            pos = (a.get("position") or {}).get("abbreviation", "")
            nm = a.get("displayName", "")
            key = norm(nm) in keys.get(team, set())
            rows.append((team, nm, pos, st, i.get("date"), int(key)))
    # soccer-style lineups / football inactives when ESPN posts rosters with starter flags
    for r in s.get("rosters", []) or []:
        team = r.get("team", {}).get("displayName", "")
        posted = [p for p in r.get("roster", []) or [] if "starter" in p]
        if not posted:
            continue
        for p in posted:
            nm = p.get("athlete", {}).get("displayName", "")
            if norm(nm) in keys.get(team, set()) and not p.get("starter") and not p.get("subbedIn"):
                rows.append((team, nm, (p.get("position") or {}).get("abbreviation", ""), "Not starting",
                             now.isoformat(), 1))
    return rows


def run_injuries(now=None, window=WIN, force=False):
    now = now or datetime.now(UTC)
    o = own()
    L = sqlite3.connect(LEDGER, timeout=30)
    void = json.load(open(VOID)) if os.path.exists(VOID) else []
    vkeys = {v["key"] for v in void}
    picks = list(L.execute("SELECT key,sport,home,away,market,pick,start,logged_at,result FROM picks "
                           "WHERE result IS NULL OR result=''"))
    seen = {}
    flags = 0
    for k, sport, home, away, market, pick, start, logged, res in picks:
        st = ts(start)
        if sport not in ESPN or not st:
            continue
        mins = (st - now).total_seconds() / 60
        if not force and not (window[0] <= mins <= window[1]):
            continue
        if force and not (0 < mins < 8 * 24 * 60):
            continue
        e = find_event(sport, home, away, st)
        if not e:
            continue
        if e["id"] not in seen:
            seen[e["id"]] = injuries(sport, e, now)
            for team, nm, pos, stt, nts, key in seen[e["id"]]:
                o.execute("INSERT OR IGNORE INTO injury_snap VALUES (?,?,?,?,?,?,?,?,?)",
                          (e["id"], sport, team, nm, pos, stt, nts, key, now.isoformat()))
        pk_home = side_is_home(pick, e) if market in ("ml", "ats", "spread", "h2h", "upset") else None
        lt = ts(logged) or now
        for team, nm, pos, stt, nts, key in seen[e["id"]]:
            if not key and pos != "QB":
                continue
            is_home_team = tmatch(team, e["home"]) or norm(team) == norm(e["home"].get("displayName"))
            bad = stt.lower() in BAD or stt == "Not starting"
            fresh = (ts(nts) or now) >= lt - timedelta(hours=1)
            if pk_home is not None:
                hurts = (pk_home == is_home_team)
            elif market == "total":
                hurts = (pick or "").lower().startswith("o")  # key absence lowers scoring -> hurts Overs
            else:
                hurts = False
            if bad and hurts and fresh and (key or pos == "QB"):
                action = "void-injury" if not force else "would-void"
            elif hurts:
                action = "flag-" + ("out-priced-in" if bad else "questionable")
            else:
                action = "flag-opponent"
            reason = f"{nm} ({pos}, {team}) {stt}; news {nts}; pick logged {logged}; {mins:.0f}m to start"
            dup = o.execute("SELECT 1 FROM pick_flags WHERE key=? AND player=? AND status=? AND action=?",
                            (k, nm, stt, action)).fetchone()
            if not dup:
                o.execute("INSERT INTO pick_flags (key,sport,event_id,pick,market,team,player,pos,status,action,reason,at) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (k, sport, e["id"], pick, market, team, nm, pos, stt, action, reason, now.isoformat()))
                flags += 1
                print(f"[{action}] {k}: {reason}")
            if action == "void-injury" and k not in vkeys:
                L.execute("UPDATE picks SET result='void-injury', note=COALESCE(note,'')||? WHERE key=? "
                          "AND (result IS NULL OR result='')", (f" | void-injury: {nm} {stt}", k))
                void.append({"key": k, "sport": sport, "home": home, "away": away, "market": market, "pick": pick,
                             "start": start, "player": nm, "status": stt, "at": now.isoformat()})
                vkeys.add(k)
    o.commit(); L.commit()
    tmp = VOID + ".tmp"
    json.dump(void, open(tmp, "w"), indent=1); os.replace(tmp, VOID)
    return flags


def main(argv):
    cmd = argv[0] if argv else "all"
    if cmd in ("clv", "all"):
        print("clv rows computed:", run_clv())
    if cmd in ("injuries", "all"):
        print("injury flags:", run_injuries())
    if cmd == "scan":  # test: flag (not void-window) every open pick in the next 8 days
        print("injury flags:", run_injuries(force=True))
    print(clv_line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
