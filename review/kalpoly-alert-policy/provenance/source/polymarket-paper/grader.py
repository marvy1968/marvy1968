"""Polymarket US PAPER grader (fake money). Settles paper fills from data/pm_paper.db.
Primary: public bbo endpoint -> state EXPIRED/RESOLVED + settlementPx (0/1). Fallback: ESPN final scores
(moneyline picks only). Writes data/results_ledger.csv, data/results.jsonl and data/summary.json. Read-only to the network."""
import csv, json, os, re, sqlite3, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
from pm_client import PMPublic
HERE = os.path.dirname(os.path.abspath(__file__)); CT = ZoneInfo("America/Chicago")
DATA = os.path.join(HERE, "data"); DB = os.path.join(DATA, "pm_paper.db")
START_BANKROLL = float(json.load(open(os.path.join(HERE, "config.json"))).get("paper_bankroll_start", 10000))
ESPN = {"nhl": "hockey/nhl", "nfl": "football/nfl", "cfb": "football/college-football", "mlb": "baseball/mlb",
        "nba": "basketball/nba", "wnba": "basketball/wnba", "cbb": "basketball/mens-college-basketball"}
def _f(a):
    try: return float((a or {}).get("value"))
    except (TypeError, ValueError): return None
def _norm(s): return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()
_espn = {}
def espn_final(slug, pick):
    """Moneyline fallback: returns 1.0 if picked team won, 0.0 lost, 0.5 tie, None unknown."""
    m = re.match(r"aec-([a-z]+)-[a-z]+-[a-z]+-(\d{4})-(\d\d)-(\d\d)$", slug or "")
    if not m or m.group(1) not in ESPN: return None
    lg, d = m.group(1), m.group(2) + m.group(3) + m.group(4); k = (lg, d)
    if k not in _espn:
        u = f"https://site.api.espn.com/apis/site/v2/sports/{ESPN[lg]}/scoreboard?dates={d}&limit=300" + ("&groups=80" if lg == "cfb" else "")
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "pm-paper-grader"}), timeout=20) as r:
                _espn[k] = json.loads(r.read()).get("events", [])
        except Exception: _espn[k] = []
    p = _norm(pick)
    for e in _espn[k]:
        c = e["competitions"][0]
        if not c["status"]["type"].get("completed"): continue
        for t in c["competitors"]:
            if _norm(t["team"].get("displayName")) == p or _norm(t["team"].get("shortDisplayName")) == p:
                o = [x for x in c["competitors"] if x is not t][0]
                a, b = float(t.get("score") or 0), float(o.get("score") or 0)
                return 1.0 if a > b else 0.0 if a < b else 0.5
    return None
_box = {}
_STAT = {"PTS": "PTS", "REB": "REB", "AST": "AST", "STL": "STL", "BLK": "BLK", "3PM": "3PT"}
def espn_prop(key, slug):
    """2026-11-05 kpfix: player-prop fallback (key nbalive:<espn_event>:<player>:<stat>:<line>:<side>).
    Polymarket prop markets can stay MARKET_STATE_OPEN with no settlementPx long after the game; settle from the
    ESPN final box score. Returns value of ONE LONG ('player gets line+') contract: 1.0 hit, 0.0 miss, None unknown.
    DNP / player not in box -> None (left open for manual/PM resolution)."""
    m = re.match(r"nbalive:(\d+):(.+):([A-Z0-9]+):(\d+(?:\.\d+)?):(long|short)$", key or "")
    if not m or m.group(3) not in _STAT: return None
    ev, player, stat, line = m.group(1), _norm(m.group(2)), _STAT[m.group(3)], float(m.group(4))
    if ev not in _box:
        u = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={ev}"
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "pm-paper-grader"}), timeout=20) as r:
                _box[ev] = json.loads(r.read())
        except Exception as ex: print("espn box err", ev, ex); _box[ev] = None
    d = _box[ev]
    if not d: return None
    try:
        if not d["header"]["competitions"][0]["status"]["type"].get("completed"): return None
        for t in d["boxscore"]["players"]:
            st = t["statistics"][0]; i = st["names"].index(stat)
            for a in st["athletes"]:
                if _norm(a["athlete"]["displayName"]) != player: continue
                if a.get("didNotPlay") or not a.get("stats"): return None
                v = a["stats"][i]; v = float(v.split("-")[0]) if stat == "3PT" else float(v)
                return 1.0 if v >= line else 0.0
    except Exception as ex: print("espn box parse err", ev, ex)
    return None
def db():
    c = sqlite3.connect(DB); cols = [r[1] for r in c.execute("pragma table_info(fills)")]
    for col, typ in (("result", "text"), ("payout", "real"), ("settle_px", "real"), ("settled_ts", "text"), ("settle_src", "text")):
        if col not in cols: c.execute(f"alter table fills add column {col} {typ}")
    return c
def settle_open(con, cl):
    now = datetime.now(CT).isoformat(); n = 0; cache = {}
    for fid, slug, side, pick, cnt, cost, key in con.execute(
            "select id,slug,side,pick,contracts,cost,key from fills where status='open'").fetchall():
        if slug not in cache:
            try: cache[slug] = cl.bbo(slug)
            except Exception as ex: print("bbo err", slug, ex); cache[slug] = {}
        md = cache[slug]; st = md.get("state") or ""; sp = _f(md.get("settlementPx"))
        win_px = src = None   # value of ONE contract of our side at settlement
        if st in ("MARKET_STATE_EXPIRED", "MARKET_STATE_RESOLVED", "MARKET_STATE_SETTLED") and sp is not None:
            win_px = sp if side == "long" else 1 - sp; src = "polymarket"
        elif st not in ("MARKET_STATE_OPEN", ""):
            r = espn_final(slug, pick)          # pick team won? (works for long or short: pick is our team)
            if r is not None: win_px = r; src = "espn_final"
        if win_px is None and (key or "").startswith("nbalive:"):
            r = espn_prop(key, slug)            # long-side value; NOT/short side = 1 - r
            if r is not None: win_px = r if side == "long" else 1 - r; src = "espn_box"
        if win_px is None: continue
        win_px = round(win_px, 4); payout = round(cnt * win_px, 4); pnl = round(payout - cost, 4)
        res = "win" if win_px >= 0.99 else "loss" if win_px <= 0.01 else "push"
        con.execute("update fills set status='settled', result=?, payout=?, pnl=?, settle_px=?, settled_ts=?, settle_src=? where id=?",
                    (res, payout, pnl, win_px, now, src, fid)); n += 1
        print("settled", fid, pick, slug, side, res, pnl, src)
    con.commit(); return n
def rec(rows):
    w = sum(r["result"] == "win" for r in rows); l = sum(r["result"] == "loss" for r in rows)
    p = sum(r["result"] == "push" for r in rows); risk = sum(r["cost"] for r in rows); pnl = sum(r["pnl"] for r in rows)
    return {"W": w, "L": l, "P": p, "record": f"{w}-{l}-{p}", "risked": round(risk, 2), "pnl": round(pnl, 2),
            "roi_pct": round(100 * pnl / risk, 2) if risk else None}
def write(con):
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("select * from fills order by id")]
    st = [r for r in rows if r["status"] == "settled"]; op = [r for r in rows if r["status"] == "open"]
    keys = ["id", "date", "pick", "slug", "side", "contracts", "price", "fee", "cost", "status", "result", "settle_px", "payout", "pnl", "settle_src", "settled_ts", "me_type", "me_alert_id"]
    with open(os.path.join(DATA, "results_ledger.csv.tmp"), "w", newline="") as f:
        w = csv.DictWriter(f, keys, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    os.replace(os.path.join(DATA, "results_ledger.csv.tmp"), os.path.join(DATA, "results_ledger.csv"))
    with open(os.path.join(DATA, "results.jsonl.tmp"), "w") as f:
        for r in st: f.write(json.dumps({k: r.get(k) for k in keys}) + "\n")
    os.replace(os.path.join(DATA, "results.jsonl.tmp"), os.path.join(DATA, "results.jsonl"))
    by_bet = {}; by_set = {}
    for r in st:
        by_bet.setdefault(r["date"], []).append(r); by_set.setdefault(r["settled_ts"][:10], []).append(r)
    realized = sum(r["pnl"] for r in st); open_cost = sum(r["cost"] for r in op)
    s = {"generated": datetime.now(CT).isoformat(), "paper_only": True, "start_bankroll": START_BANKROLL,
         "bankroll_settled": round(START_BANKROLL + realized, 2),
         "cash_available": round(START_BANKROLL + realized - open_cost, 2),
         "all_time": rec(st), "today_ct": {"date": datetime.now(CT).date().isoformat(), **rec(by_set.get(datetime.now(CT).date().isoformat(), []))},
         "daily_by_bet_date": {d: rec(v) for d, v in sorted(by_bet.items())},
         "daily_by_settle_date": {d: rec(v) for d, v in sorted(by_set.items())},
         "by_alert_type": {t: rec([r for r in st if (r.get("me_type") or "marv") == t]) for t in sorted({r.get("me_type") or "marv" for r in st})},
         "open_by_alert_type": {t: sum(1 for r in op if (r.get("me_type") or "marv") == t) for t in sorted({r.get("me_type") or "marv" for r in op})},
         "open": {"count": len(op), "cost": round(open_cost, 2),
                  "positions": [{k: r[k] for k in ("id", "date", "pick", "slug", "side", "contracts", "price", "cost")} for r in op]}}
    tmp = os.path.join(DATA, "summary.json.tmp"); json.dump(s, open(tmp, "w"), indent=1); os.replace(tmp, os.path.join(DATA, "summary.json"))
    return s
if __name__ == "__main__":
    con = db(); n = settle_open(con, PMPublic()); s = write(con)
    print(f"graded {n} | all-time {s['all_time']} | bankroll {s['bankroll_settled']} | open {s['open']['count']} (${s['open']['cost']})")
