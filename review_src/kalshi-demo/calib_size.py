"""Calibration-tier sizing (2026-11-01, Marvin approved). Kalshi DEMO + Polymarket PAPER only.
Per lane, from that lane's prior SETTLED bets (cached, recomputed when ledger changes or every 30 min):
  calibrated    (n>=30 and actual >= predicted-0.03): stake = bal * min(2%, 1/4 Kelly(p, price))
  overconfident (n>=30 and actual <  predicted-0.03): p' = p*actual/predicted; skip if p' <= price;
                                                       stake = bal * min(1%, 1/4 Kelly(p', price))
  new           (n<30):                              stake = bal * 0.5%
Existing hard caps (max_stake_usd, KALSHI_TT_CAP, ...) are applied by callers as an upper bound.
Off switch: CALIB_SIZING=off env, or data/CALIB_SIZING_OFF in either repo -> size() returns None (callers use old sizing).
Every decision is appended to <repo>/data/calib_decisions.jsonl."""
import csv, json, os, sqlite3, time
from datetime import datetime
from zoneinfo import ZoneInfo
KD, PD = "/opt/kalshi-demo/data", "/opt/polymarket-paper/data"
MIN_N, TOL, CAL_CAP, OVER_CAP, NEW_PCT, KFRAC = 30, 0.03, 0.02, 0.01, 0.005, 0.25
CT = ZoneInfo("America/Chicago")
# 2026-11-02 Marvin: per-lane stake overrides (fraction of balance), forced regardless of tier (skips still skip).
# pm:nhl_prop_live = 0.5% for now. Off: env CALIB_LANE_OVERRIDE=off | data/CALIB_LANE_OVERRIDE_OFF (either repo).
LANE_OVERRIDE = {("pm", "nhl_prop_live"): 0.005}
def override_on():
    if str(os.environ.get("CALIB_LANE_OVERRIDE", "on")).lower() in ("off", "0", "false", "no"): return False
    return not any(os.path.exists(os.path.join(d, "CALIB_LANE_OVERRIDE_OFF")) for d in (KD, PD))

def enabled():
    if str(os.environ.get("CALIB_SIZING", "on")).lower() in ("off", "0", "false", "no"): return False
    return not any(os.path.exists(os.path.join(d, "CALIB_SIZING_OFF")) for d in (KD, PD))

def lane_key(venue, lane, me_type=None):
    l = str(lane or "").strip()
    if venue == "pm":
        if l.startswith("me_track:"): return l.split(":")[1] or me_type or "unlabeled"   # me_track:<me_type>:... -> me_type
        if l.startswith("runner:") or not l: return me_type or ("unlabeled" if not l else "marv")
        return l
    return l or me_type or "unlabeled"

def _kalshi_rows():
    P = {}
    for fn in ("demo_log.jsonl", "results.jsonl"):
        try:
            for ln in open(os.path.join(KD, fn)):
                try: d = json.loads(ln)
                except Exception: continue
                o = d.get("order") or {}; oid = o.get("order_id") or d.get("order_id")
                p = d.get("p_side")
                if p is None and d.get("fair_c") is not None: p = float(d["fair_c"]) / 100
                if oid and p is not None: P[oid] = float(p)
        except FileNotFoundError: pass
    out = []
    for r in csv.DictReader(open(os.path.join(KD, "results_ledger.csv"))):
        if r["status"] != "settled" or r["result"] not in ("win", "loss"): continue
        out.append((lane_key("kalshi", r["me_type"]), P.get(r["order_id"]), r["result"] == "win"))
    return out

def _pm_rows():
    con = sqlite3.connect(os.path.join(PD, "pm_paper.db"))
    try:
        return [(lane_key("pm", l, m), f, res == "win") for m, l, f, res in
                con.execute("select me_type, lane, fair, result from fills where status='settled' and result in ('win','loss')")]
    finally: con.close()

def lane_stats(venue):
    src = os.path.join(KD, "results_ledger.csv") if venue == "kalshi" else os.path.join(PD, "pm_paper.db")
    cache = os.path.join(KD if venue == "kalshi" else PD, "calib_cache.json")
    try:
        c = json.load(open(cache))
        if c["src_mtime"] == os.path.getmtime(src) and time.time() - c["built"] < 1800: return c["lanes"]
    except Exception: pass
    rows = _kalshi_rows() if venue == "kalshi" else _pm_rows(); L = {}
    for k, p, w in rows:
        s = L.setdefault(k, {"n": 0, "w": 0, "np": 0, "psum": 0.0}); s["n"] += 1; s["w"] += w
        if p is not None: s["np"] += 1; s["psum"] += p
    for s in L.values():
        s["pred"] = s["psum"] / s["np"] if s["np"] else None; s["act"] = s["w"] / s["n"]
        s["tier"] = "new" if s["n"] < MIN_N or not s["pred"] else ("calibrated" if s["act"] >= s["pred"] - TOL else "overconfident")
    try:
        tmp = cache + ".tmp"; json.dump({"built": time.time(), "src_mtime": os.path.getmtime(src), "lanes": L}, open(tmp, "w"), indent=1); os.replace(tmp, cache)
    except Exception: pass
    return L

def balance(venue):
    if venue == "kalshi":
        s = json.load(open(os.path.join(KD, "summary.json")))
        return float(s.get("account_total") or (float(s.get("cash") or 0) + float(s.get("portfolio_value") or 0)))
    try: start = float(json.load(open(os.path.join(PD, "summary.json"))).get("start_bankroll") or 10000)
    except Exception: start = 10000.0
    con = sqlite3.connect(os.path.join(PD, "pm_paper.db"))
    try: return start + float(con.execute("select coalesce(sum(pnl),0) from fills where status='settled'").fetchone()[0])
    finally: con.close()

def size(venue, lane, p, price, me_type=None, cap_usd=None, dry=False, extra=None):
    """venue 'kalshi'|'pm'; p = model win prob for the side bought; price in dollars (0-1).
    Returns (stake_usd, info) or (None, info) when switched off / error (caller keeps old sizing). stake 0 = skip."""
    if not enabled(): return None, {"tier": "off"}
    try:
        k = lane_key(venue, lane, me_type); s = lane_stats(venue).get(k) or {"n": 0, "tier": "new", "pred": None, "act": None}
        bal = balance(venue); q = float(price); p = float(p or 0); tier = s["tier"]; p_adj = p; why = None
        kel = lambda pp: max(0.0, (pp - q) / (1 - q)) if 0 < q < 1 else 0.0
        if tier == "calibrated": frac = min(CAL_CAP, KFRAC * kel(p))
        elif tier == "overconfident":
            p_adj = p * s["act"] / s["pred"]
            if p_adj <= q: frac, why = 0.0, f"calib skip: shrunk p {p_adj:.3f} <= price {q:.3f}"
            else: frac = min(OVER_CAP, KFRAC * kel(p_adj))
        else: frac = NEW_PCT
        if override_on() and (venue, k) in LANE_OVERRIDE and not why:
            frac = LANE_OVERRIDE[(venue, k)]; tier = tier + "+override"
        stake = bal * frac
        if cap_usd is not None: stake = min(stake, float(cap_usd))
        stake = round(max(0.0, stake), 2)
        if stake <= 0 and not why: why = "calib skip: kelly 0"
        info = {"calib_lane": k, "calib_tier": tier, "calib_n": s["n"], "calib_pred": None if s.get("pred") is None else round(s["pred"], 4),
                "calib_act": None if s.get("act") is None else round(s["act"], 4), "calib_p": round(p, 4), "calib_p_adj": round(p_adj, 4),
                "calib_frac": round(frac, 5), "calib_bal": round(bal, 2), "calib_stake": stake, "calib_skip": why}
        if not dry:
            try:
                with open(os.path.join(KD if venue == "kalshi" else PD, "calib_decisions.jsonl"), "a") as f:
                    f.write(json.dumps(dict(info, ts=datetime.now(CT).isoformat(), venue=venue, price=q, cap_usd=cap_usd, **(extra or {}))) + "\n")
            except Exception: pass
        return stake, info
    except Exception as ex:
        return None, {"tier": "error", "err": str(ex)[:120]}

if __name__ == "__main__":
    for v in ("kalshi", "pm"):
        print(v, "balance", round(balance(v), 2))
        for k, s in sorted(lane_stats(v).items(), key=lambda kv: -kv[1]["n"]):
            print(f"  {k:28s} n={s['n']:4d} pred={s['pred'] if s['pred'] is None else round(s['pred'],3)} act={s['act']:.3f} {s['tier']}")
