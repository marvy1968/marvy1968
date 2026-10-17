"""Small backtest: Marv live pricer vs REAL Bovada live soccer total quotes (March_edge total_swing / soccer_grind
alerts in Marv's 10 leagues, sent or paper, graded against final scores).

Point-in-time: each game is priced from Marv ratings refitted with only matches BEFORE the alert's date (no leakage);
live-model parameters come from live_props_calib.json (fit on HT/FT data before 2026-08-21). These alerts are the only
stored live Bovada soccer prices with score + minute (Oct 9-14 2026); team totals / BTTS / scorer have no stored live
Bovada prices at all, so they cannot be backtested.
"""
import csv, json, math, re, sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, "/opt/marv-bot")
from marv.soccer_max import data as D, engine as E, names as N, node as NODE, live_props as LP

ST = Path("/opt/marv-bot/state")
CSV = Path(sys.argv[1]); CAL = Path(sys.argv[2]); EV_MIN = 0.05
prm = {**LP.DEFAULTS, **json.loads(CAL.read_text())["params"]}
SC = re.compile(r"(\d+)\s*[–\-]\s*(\d+)[^\n·\d]{0,24}·\s*(HT|FT|\d+)")
p_ = NODE.live_params(ST)
cache, rcache = ST / "cache" / "soccer_fd", {}


def ratings_asof(key, day):
    k = (key, day)
    if k not in rcache:
        df = D.load_league(key, cache, pd.Timestamp(day).to_pydatetime())
        df = df[df["date"] < pd.Timestamp(day)] if not df.empty else df
        cur = D.current_season(pd.Timestamp(day).to_pydatetime())
        proxy = E.fit_proxy(df[df["season"] < cur]) if df["hst"].notna().any() else None
        rcache[k] = E.fit_ratings(df, pd.Timestamp(day), p_, proxy)
    return rcache[k]


def dec(a):
    a = float(a)
    return 1 + (a / 100 if a > 0 else 100 / -a)


def ev_total(s, side, line, d):
    """EV per 1 unit; quarter lines = half stake on each neighbour."""
    if abs(line * 4 - round(line * 4)) < 1e-9 and abs(line * 2 - round(line * 2)) > 1e-9:
        return (ev_total(s, side, line - 0.25, d) + ev_total(s, side, line + 0.25, d)) / 2
    t = s["totals"].get(f"{line:g}")
    if t is None:
        return None
    w = t["over"] if side == "over" else t["under"]
    return w * (d - 1) - (1 - w - t["push"])


def profit(final_total, side, line, d):
    if abs(line * 4 - round(line * 4)) < 1e-9 and abs(line * 2 - round(line * 2)) > 1e-9:
        return (profit(final_total, side, line - 0.25, d) + profit(final_total, side, line + 0.25, d)) / 2
    diff = (final_total - line) * (1 if side == "over" else -1)
    return d - 1 if diff > 0 else 0.0 if diff == 0 else -1.0


rows, seen = [], set()
for a in csv.DictReader(open(CSV)):
    if a["book"].lower() != "bovada" or a["status"] not in ("won", "lost", "push"):
        continue
    m = SC.search(a["message"]); fs = re.search(r"(\d+)-(\d+)", a["final_score"] or "")
    if not m or not fs or " @ " not in (a["matchup"] or ""):
        continue
    away_n, home_n = a["matchup"].split(" @ ", 1)
    sa, sh = int(m.group(1)), int(m.group(2))            # message shows AWAY score first (matches matchup order)
    ht = m.group(3) == "HT"; minute = 45.0 if ht else float(m.group(3))
    if m.group(3) == "FT":
        continue
    day = a["ts"][:10]
    r = ratings_asof(a["league"], day)
    if not r:
        continue
    home, away = N.resolve(home_n, list(r["teams"])), N.resolve(away_n, list(r["teams"]))
    if not home or not away or home == away:
        continue
    xg = E.expected_goals(r, home, away)
    s = LP.price_state(xg["home_xg"], xg["away_xg"], sh, sa, minute, prm, ht)
    side, line, d = a["selection"].lower(), float(a["line"]), dec(a["price"])
    ev = ev_total(s, side, line, d)
    if ev is None:
        continue
    ft = int(fs.group(1)) + int(fs.group(2))
    rows.append({"id": a["id"], "ts": a["ts"][:16], "league": a["league"], "game": f"{away} @ {home}", "state": f"{sa}-{sh} {minute:.0f}'",
                 "bet": f"{side} {line:g}", "price": float(a["price"]), "sent": a["sent"], "marv_ev": round(ev, 4),
                 "profit": profit(ft, side, line, d), "final_total": ft, "dup": (a["event_id"], side, line) in seen})
    seen.add((a["event_id"], side, line))

df = pd.DataFrame(rows)
def summ(x):
    if not len(x):
        return {"n": 0}
    return {"n": int(len(x)), "w": int((x.profit > 0).sum()), "l": int((x.profit < 0).sum()), "p": int((x.profit == 0).sum()),
            "units": round(float(x.profit.sum()), 2), "roi": round(float(x.profit.mean()), 4)}
u = df[~df.dup]
rep = {"window": f"{df.ts.min()} .. {df.ts.max()} ET", "priced": len(df), "unique_game_bet_line": len(u),
       "all_alert_quotes": summ(u), "marv_ev_ge_5pct": summ(u[u.marv_ev >= EV_MIN]), "marv_ev_ge_7pct_2star": summ(u[u.marv_ev >= 0.07]),
       "marv_ev_lt_0": summ(u[u.marv_ev < 0]),
       "by_side_ev5": {s: summ(u[(u.marv_ev >= EV_MIN) & u.bet.str.startswith(s)]) for s in ("over", "under")},
       "sent_alerts": summ(u[u.sent == "1"]), "sent_and_marv_ev5": summ(u[(u.sent == "1") & (u.marv_ev >= EV_MIN)]),
       "params": prm}
print(json.dumps(rep, indent=1))
df.to_csv("/tmp/slp_bt_rows.csv", index=False)
