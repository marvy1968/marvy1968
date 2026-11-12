"""NBA prop pacer SHADOW (Marvin approved 2026-10-30). READ-ONLY w.r.t. lanes: reads nba_prop_live rows from
Polymarket paper + Kalshi demo track_only.jsonl, scores them with the pacer, appends to nba_pacer_shadow.jsonl.
Never places/changes bets, stakes or gates. Off: touch PACER_SHADOW_OFF in this dir."""
import json, os, sys, re, math, unicodedata, urllib.request, pickle, traceback
from datetime import datetime, timezone
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "nba_pacer_shadow.jsonl")
SRC = {"poly": "/opt/polymarket-paper/data/track_only.jsonl", "kalshi": "/opt/kalshi-demo/data/track_only.jsonl"}
RATES = "/opt/polymarket-paper/data/nba_rates_cache.json"
FEE = {"poly": 0.0695, "kalshi": 0.07}; VETO = 0.03
def norm(s): return re.sub(r"[^a-z ]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().replace(" jr", "").replace(" iii", "").replace(" ii", "")).strip()
def box(gid):
    u = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={gid}"
    s = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=15).read())
    o = {}
    for tm in s.get("boxscore", {}).get("players", []):
        b = tm["statistics"][0]; nm = b.get("names") or []
        for a in b.get("athletes", []):
            v = dict(zip(nm, a.get("stats") or []))
            try: mn = float(v.get("MIN") or 0); pf = int(v.get("PF") or 0)
            except Exception: mn, pf = 0.0, 0
            o[norm(a["athlete"]["displayName"])] = dict(id=a["athlete"]["id"], starter=int(bool(a.get("starter"))), MIN=mn, PF=pf)
    return o
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
            spec = importlib.util.spec_from_file_location("game_clock_shadow", c)
            mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); _gc.mod = mod; return mod
    raise ImportError("game_clock.py")
def clk(c, period_minutes=12.0):
    """Same parser as the live lanes. Ambiguous clocks raise; the row is skipped."""
    left = _gc().parse_period_clock(c, period_minutes)
    if left is None:
        raise ValueError("ambiguous or unparseable clock: %r" % (c,))
    return left
def main():
    import pandas as pd
    from pacer import Pacer, FEAT  # noqa
    if os.path.exists(os.path.join(HERE, "PACER_SHADOW_OFF")): print("off"); return
    done = set()
    if os.path.exists(OUT):
        for l in open(OUT):
            try: r = json.loads(l); done.add((r["ex"], r["key"], r.get("ticker")))
            except Exception: pass
    P = pickle.load(open(os.path.join(HERE, "pacer_nba.pkl"), "rb")); rates = json.load(open(RATES)) if os.path.exists(RATES) else {}
    boxes = {}; n = 0
    for ex, path in SRC.items():
        if not os.path.exists(path): continue
        for l in open(path):
            if '"nba_prop_live"' not in l: continue
            try:
                t = json.loads(l)
                if t.get("type") != "nba_prop_live" or (ex, t["key"], t.get("ticker")) in done: continue
                k = t["key"].split(":"); gid, nm, st, thr, side = k[1], k[2], k[3], int(k[4]), k[5]
                if gid not in boxes:
                    try: boxes[gid] = box(gid)
                    except Exception: boxes[gid] = {}
                bp = boxes[gid].get(nm, {}); rt = rates.get(bp.get("id", ""), {})
                per = int(t["period"]); el = (per - 1) * 12 + 12 - clk(t["clock"]) if per <= 4 else 48.0; rem = max(0.0, 48 - el)
                if ex == "poly":
                    px = float(t["price"]); mp = float(t["min_played"]); pf = int(t["pf"])
                    rate = t["mu_rest"] / t["exp_min_left"] if t.get("exp_min_left") else rt.get(st)
                else:
                    px = float(t["ask"]) / 100; mp = bp.get("MIN"); pf = bp.get("PF", 0); rate = rt.get(st)
                rec = dict(ts=datetime.now(timezone.utc).isoformat(), ex=ex, key=t["key"], ticker=t.get("ticker"), date=t.get("date"),
                           pick=t.get("pick"), side=side, price=px, stat=st, thr=thr, lane_ts=t.get("ts"), lane_p_side=t.get("p_side", (t.get("fair_c") or 0) / 100),
                           lane_edge=t.get("edge", (t.get("edge_c") or 0) / 100), state_src="row" if ex == "poly" else "box_at_shadow_time")
                if rate is None or mp is None or rem <= 0 or not rt.get("min"):
                    rec.update(pacer_ok=False, why="missing rate/minutes/state")
                else:
                    d = pd.DataFrame([dict(avgmin=rt["min"], mp=mp, el=el, rem=rem, pf=pf, margin=t["margin"], starter=bp.get("starter", 1), on=1,
                                           st=st, cur=t["cur"], need=t["need"], rate=rate)])
                    po = float(P.prob(d)[0]); left = float(P.left(d)[0])
                    ps = po if side in ("long", "yes") else 1 - po; fee = FEE[ex] * px * (1 - px); edge = ps - px - fee
                    rec.update(pacer_ok=True, p_over=round(po, 4), p_side=round(ps, 4), min_proj_left=round(left, 2),
                               fee=round(fee, 4), pacer_edge=round(edge, 4), veto_skip=bool(edge < VETO))
                with open(OUT, "a") as fh: fh.write(json.dumps(rec) + "\n")
                n += 1
            except Exception as e:
                print("row err", type(e).__name__, str(e)[:100])
    print(f"{datetime.now():%H:%M:%S} shadow logged {n}")
if __name__ == "__main__":
    try: main()
    except Exception: traceback.print_exc()
