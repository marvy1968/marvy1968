"""CFB LINE-LEVEL O/U tracker (TRACKING ONLY, paper; never bets, never Telegram). Study 2026-10-16: experiments/script_totals/ou_study.
Rule (CFB only, pregame): fc = market total - BASE, BASE = average FBS closing total over the prior 365 days.
  fc >= +11.57 -> Defensive Grind / UNDER   |   fc <= -12.93 -> High Shootout / OVER   |   otherwise no action.
Held-out (train 2018-22, unseen 2023-26 to 2026-10-04, -110): 113-89-1, 55.9%, ROI +6.8%, n=203
  vs current Script O/U extremes proxy on the same games 381-380-14, 50.1%, ROI -4.3%, n=775.
Each script-ou run: logs every CFB game that either rule fires on, at the latest pregame line (= closing proxy), with BOTH the
new and the CURRENT extremes decision; grades both after the final -> state/script_ou/linelevel_ledger.json + linelevel_summary.json.
Flags: MARV_OU_LINELEVEL=0 disables tracking. MARV_OU_LINELEVEL_LIVE=1 (default OFF) would also feed these picks into the
Script O/U ledger/daily card (Telegram still governed by SCRIPT_OU_TELEGRAM). MARV_OU_LINELEVEL_BASE overrides the 51.8 fallback."""
import json, os
from datetime import datetime, timedelta
from pathlib import Path

D = Path("/opt/marv-bot/state/script_ou"); LED = D / "linelevel_ledger.json"; SUM = D / "linelevel_summary.json"
UNDER_AT, OVER_AT = 11.57, -12.93
BASE0 = float(os.environ.get("MARV_OU_LINELEVEL_BASE", "51.8"))  # prior-365d FBS avg closing total as of 2026-10-04 (cache)
MIN_SEEN = 300  # once >= 300 logged CFB lines in the last 365 days, BASE = their mean (self-updating)

def _on(name, default=""):
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")
def enabled(): return os.environ.get("MARV_OU_LINELEVEL", "1").strip().lower() not in ("0", "false", "no", "off")
def live(): return _on("MARV_OU_LINELEVEL_LIVE")

def base(seen, so):
    cut = so.now - timedelta(days=365)
    v = [x["line"] for x in seen.values() if (so.ts(x.get("start")) or so.now) >= cut and x.get("line") is not None]
    return (round(sum(v) / len(v), 2), "logged", len(v)) if len(v) >= MIN_SEEN else (BASE0, "fallback", len(v))

def decide(line, b):
    fc = line - b
    return (-1 if fc >= UNDER_AT else (1 if fc <= OVER_AT else 0)), round(fc, 2)

def _units(so, side, price, tot, line):
    if not side: return None, None
    r = (tot - line) * side
    return ("W" if r > 0 else ("L" if r < 0 else "P")), round(so.pay(price) if r > 0 else (-1.0 if r < 0 else 0.0), 3)

def _grade(so, r):
    st = so.ts(r["start"])
    if not st or so.now < st + timedelta(hours=3): return
    for dd in dict.fromkeys([(st - timedelta(hours=6)).date(), st.date()]):
        for h, a, hs, as_ in so.finals("cfb", datetime.combine(dd, datetime.min.time())):
            if so.same(h, r["home"]) and so.same(a, r["away"]):
                tot = hs + as_; r.update(final=f"{int(as_)}-{int(hs)}", total=tot, status="graded", graded=so.now.isoformat())
                for m in ("new", "cur"):
                    r[m + "_res"], r[m + "_units"] = _units(so, r[m + "_side"], r[m + "_price"], tot, r["line"])
                return
    if so.now > st + timedelta(days=4): r["status"] = "ungraded"

def _rec(rows, m):
    G = [r for r in rows if r.get(m + "_res")]
    u = sum(r[m + "_units"] for r in G); W = sum(r[m + "_res"] == "W" for r in G); L = sum(r[m + "_res"] == "L" for r in G)
    return dict(n=len(G), W=W, L=L, P=len(G) - W - L, hit=round(W / (W + L), 3) if W + L else None, units=round(u, 2),
                roi=round(u / len(G), 3) if G else None, pending=sum(1 for r in rows if r.get("status") == "pending" and r.get(m + "_side")))

def run(so, cands, cur_th=3.0):
    """so = script_ou module, cands = so.candidates(). Returns live picks (only when MARV_OU_LINELEVEL_LIVE is on)."""
    if not enabled(): return []
    D.mkdir(parents=True, exist_ok=True)
    S = so.jl(LED, {}); seen = S.setdefault("seen", {}); rows = S.setdefault("rows", {})
    cfb = [c for c in cands if c.get("sport") == "cfb"]
    for c in cfb: seen[c["pred_key"]] = dict(line=c["line"], start=c["start"])
    b, bsrc, bn = base(seen, so); picks = []
    for c in cfb:
        ns, fc = decide(c["line"], b)
        cs = c["side"] if (c["uf"] == 1 and c["ev"] > 0 and abs(c["d"]) >= cur_th) else 0
        k = c["pred_key"]
        if not ns and not cs and k not in rows: continue
        t = so.bov("cfb", c["home"], c["away"])
        po, pu = (float(t[1] or -110), float(t[2] or -110)) if t and float(t[0]) == c["line"] else (-110.0, -110.0)
        r = rows.setdefault(k, dict(created=so.now.isoformat(), status="pending"))
        r.update(game=c["game"], home=c["home"], away=c["away"], start=c["start"], line=c["line"], book=c["book"], base=b, base_src=bsrc,
                 fc=fc, new_side=ns, new_tier=("high_shootout" if ns > 0 else "defensive_grind") if ns else None,
                 new_price=(po if ns > 0 else pu) if ns else None, cur_side=cs, cur_price=(po if cs > 0 else pu) if cs else None,
                 proj=c["proj"], d=c["d"], uf=c["uf"], ev=c["ev"], updated=so.now.isoformat())
        if ns and live():
            p = dict(c); p.update(key=f"scriptou:{c['game']}:{'over' if ns > 0 else 'under'}", side=ns, price=r["new_price"],
                                  tier="line_level", line_level=dict(base=b, fc=fc))
            picks.append(p)
    for r in rows.values():
        if r.get("status") == "pending": _grade(so, r)
    S["updated"] = so.now.isoformat()
    tmp = LED.with_suffix(".tmp"); json.dump(S, open(tmp, "w"), indent=1); os.replace(tmp, LED)
    R = list(rows.values()); n, cu = _rec(R, "new"), _rec(R, "cur")
    both = [r for r in R if r.get("new_side") and r.get("cur_side")]
    summ = dict(updated=so.now.isoformat(), tracking_only=True, live=live(), base=b, base_src=bsrc, base_n=bn,
                rule=f"CFB: Under if line-BASE >= {UNDER_AT}, Over if line-BASE <= {OVER_AT}", new=n, current_same_games=cu,
                overlap=dict(both_fire=len(both), same_side=sum(r["new_side"] == r["cur_side"] for r in both)),
                line=(f"CFB Line-Level O/U (tracking only): {n['W']}-{n['L']}-{n['P']} {n['units']:+.2f}u vs current extremes "
                      f"{cu['W']}-{cu['L']}-{cu['P']} {cu['units']:+.2f}u, {n['pending']} pending"))
    tmp = SUM.with_suffix(".tmp"); json.dump(summ, open(tmp, "w"), indent=1); os.replace(tmp, SUM)
    print(summ["line"])
    return picks
