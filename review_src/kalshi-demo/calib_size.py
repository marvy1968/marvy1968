"""Calibration-tier sizing (2026-11-01, Marvin approved). Kalshi DEMO + Polymarket PAPER only.
Per lane, from that lane's prior SETTLED bets (cached, recomputed when ledger changes or every 30 min):
  calibrated    (n>=30 and actual >= predicted-0.03): stake = bal * min(2%, 1/4 Kelly(p, price))
  overconfident (n>=30 and actual <  predicted-0.03): p' = p*actual/predicted; skip if p' <= price;
                                                       stake = bal * min(1%, 1/4 Kelly(p', price))
  new           (n<30):                              stake = bal * 0.5%
Existing hard caps (max_stake_usd, KALSHI_TT_CAP, ...) are applied by callers as an upper bound.
Off switch: CALIB_SIZING=off env, or data/CALIB_SIZING_OFF in either repo -> size() returns None (callers use old sizing).
Every decision is appended to <repo>/data/calib_decisions.jsonl.

Lane autopause (opt-in, default off): LANE_AUTOPAUSE_ALLOW is empty unless an operator
lists venue:lane pairs. The intended candidate is kalshi:nba_prop_live_pacer. The same
overconfident rule as size() (n>=30 and act < pred-0.03 on prior settled fills only)
latches that lane to track-only until an operator clears it. pm:nba_prop_live is not
eligible: a 3c trip on 2026-10-27 would have sat out a profitable paper book.
force_track on nba_prop_live_pacer is a separate switch and is not modified here."""
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

# Intended opt-in only. Not paused unless an operator puts this pair in LANE_AUTOPAUSE_ALLOW.
# pm:nba_prop_live is excluded outright (see autopause_allowlisted).
AUTOPAUSE_INTENDED = ("kalshi", "nba_prop_live_pacer")
AUTOPAUSE_EXCLUDED = {("pm", "nba_prop_live")}

def _norm_venue(venue):
    v = str(venue or "").strip().lower()
    if v in ("kalshi", "kalshi_demo"): return "kalshi"
    if v in ("pm", "polymarket", "polymarket_paper"): return "pm"
    return v

def overconfident(n, pred, act):
    """Same tier rule as lane_stats / size(): n>=30 and act < pred-0.03.
    act == pred-0.03 stays calibrated (the size() comparison is act >= pred-TOL)."""
    try:
        n = int(n)
        pred = float(pred)
        act = float(act)
    except (TypeError, ValueError):
        return False
    if n < MIN_N or pred == 0.0:
        return False
    return act < pred - TOL

def _autopause_pairs(text):
    out = []
    for part in str(text or "").replace("\n", ",").split(","):
        part = part.strip()
        if not part or part.startswith("#") or ":" not in part:
            continue
        venue, lane = part.split(":", 1)
        out.append((_norm_venue(venue), lane.strip()))
    return out

def autopause_allowlist():
    """Opt-in venue:lane pairs. Default empty. Env LANE_AUTOPAUSE_ALLOW and/or
    data/LANE_AUTOPAUSE_ALLOW (one pair per line or comma-separated).
    Intended value when an operator wants the backstop: kalshi:nba_prop_live_pacer.
    pm:nba_prop_live is dropped even if listed."""
    chunks = [os.environ.get("LANE_AUTOPAUSE_ALLOW", "")]
    for d in (KD, PD):
        path = os.path.join(d, "LANE_AUTOPAUSE_ALLOW")
        try:
            chunks.append(open(path).read())
        except OSError:
            pass
    allow = set()
    for venue, lane in _autopause_pairs(",".join(chunks)):
        if (venue, lane) in AUTOPAUSE_EXCLUDED:
            continue
        allow.add((venue, lane))
    return allow

def autopause_allowlisted(venue, lane):
    venue = _norm_venue(venue)
    if (venue, lane) in AUTOPAUSE_EXCLUDED:
        return False
    return (venue, lane) in autopause_allowlist()

def autopause_state_path(venue="kalshi"):
    base = KD if _norm_venue(venue) == "kalshi" else PD
    return os.path.join(base, "lane_autopause.json")

def _load_json(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}

def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)

def _operator_cleared(venue, lane, state_dir):
    keys = set(_autopause_pairs(os.environ.get("LANE_AUTOPAUSE_CLEAR", "")))
    path = os.path.join(state_dir or "", "LANE_AUTOPAUSE_CLEAR")
    try:
        keys.update(_autopause_pairs(open(path).read()))
    except OSError:
        pass
    return (_norm_venue(venue), lane) in keys

def clear_autopause(venue, lane, state_path=None):
    """Operator lift. Stays lifted (does not immediately re-latch) until rearm_autopause."""
    venue = _norm_venue(venue)
    path = state_path or autopause_state_path(venue)
    state = _load_json(path)
    key = f"{venue}:{lane}"
    rec = dict(state.get(key) or {})
    rec.update(paused=False, cleared=True, cleared_at=datetime.now(CT).isoformat())
    state[key] = rec
    _save_json(path, state)
    return rec

def rearm_autopause(venue, lane, state_path=None):
    """Drop the latch so a later overconfident check can pause the lane again."""
    venue = _norm_venue(venue)
    path = state_path or autopause_state_path(venue)
    state = _load_json(path)
    state.pop(f"{venue}:{lane}", None)
    _save_json(path, state)
    return state

def autopause_status(venue, lane, stats=None, state_path=None):
    """Return whether this lane must stay track-only. No-op unless allowlisted.
    A trip latches until clear_autopause / LANE_AUTOPAUSE_CLEAR. Stats, when
    passed in, must already be prior-settled only (the live ledger is settled
    rows; replay_autopause filters settled_CT < filled_CT)."""
    venue = _norm_venue(venue)
    if not autopause_allowlisted(venue, lane):
        return {"paused": False, "allowlisted": False, "venue": venue, "lane": lane}
    path = state_path or autopause_state_path(venue)
    state = _load_json(path)
    key = f"{venue}:{lane}"
    if _operator_cleared(venue, lane, os.path.dirname(path)):
        rec = clear_autopause(venue, lane, path)
        return {"paused": False, "allowlisted": True, "cleared": True, "venue": venue, "lane": lane, "n": rec.get("n"),
                "pred": rec.get("pred"), "act": rec.get("act")}
    rec = state.get(key) or {}
    if rec.get("cleared") and not rec.get("paused"):
        return {"paused": False, "allowlisted": True, "cleared": True, "venue": venue, "lane": lane}
    if rec.get("paused"):
        return {"paused": True, "allowlisted": True, "sticky": True, "venue": venue, "lane": lane,
                "n": rec.get("n"), "pred": rec.get("pred"), "act": rec.get("act"), "tripped_at": rec.get("tripped_at"),
                "reason": "track-only: %s (lane autopause)" % lane}
    if stats is None:
        stats = lane_stats(venue).get(lane_key(venue, lane)) or {"n": 0, "pred": None, "act": None}
    n, pred, act = stats.get("n"), stats.get("pred"), stats.get("act")
    if overconfident(n, pred, act):
        rec = {"paused": True, "cleared": False, "n": n, "pred": pred, "act": act,
               "tripped_at": datetime.now(CT).isoformat(), "rule": "n>=30 and act < pred-0.03"}
        state[key] = rec
        _save_json(path, state)
        return {"paused": True, "allowlisted": True, "tripped": True, "venue": venue, "lane": lane,
                "n": n, "pred": pred, "act": act, "tripped_at": rec["tripped_at"],
                "reason": "track-only: %s (lane autopause)" % lane}
    return {"paused": False, "allowlisted": True, "venue": venue, "lane": lane, "n": n, "pred": pred, "act": act}

def prior_settled(fills, filled_ct):
    """Fills whose settled_CT is strictly before this fill's filled_CT. No lookahead."""
    asof = datetime.fromisoformat(filled_ct)
    out = []
    for row in fills:
        if datetime.fromisoformat(row["settled_CT"]) < asof:
            out.append(row)
    return out

def stats_from_fills(rows):
    n = len(rows)
    ps = [float(r["p_side"]) for r in rows if r.get("p_side") is not None]
    pred = (sum(ps) / len(ps)) if ps else None
    wins = sum(1 for r in rows if r.get("won"))
    act = (wins / n) if n else None
    return {"n": n, "pred": pred, "act": act, "wins": wins}

def replay_autopause(fills, allowlisted):
    """Walk fills in filled_CT order. Pause latches on the first overconfident
    decision and stays latched. allowlisted False never pauses (default)."""
    ordered = sorted(fills, key=lambda r: r["filled_CT"])
    paused = False
    first = None
    decisions = []
    for row in ordered:
        st = stats_from_fills(prior_settled(ordered, row["filled_CT"]))
        tripped_here = False
        if allowlisted and not paused and overconfident(st["n"], st["pred"], st["act"]):
            paused = True
            tripped_here = True
            first = {"filled_CT": row["filled_CT"], "n": st["n"], "pred": st["pred"], "act": st["act"], "wins": st["wins"]}
        decisions.append({"filled_CT": row["filled_CT"], "paused": bool(allowlisted and paused),
                          "n_prior": st["n"], "pred": st["pred"], "act": st["act"], "tripped_here": tripped_here})
    return {"first": first, "decisions": decisions, "paused": bool(allowlisted and paused)}

if __name__ == "__main__":
    for v in ("kalshi", "pm"):
        print(v, "balance", round(balance(v), 2))
        for k, s in sorted(lane_stats(v).items(), key=lambda kv: -kv[1]["n"]):
            print(f"  {k:28s} n={s['n']:4d} pred={s['pred'] if s['pred'] is None else round(s['pred'],3)} act={s['act']:.3f} {s['tier']}")
