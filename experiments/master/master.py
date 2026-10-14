"""MASTER architecture tracker (paper / Kalshi demo / Polymarket paper only; NEVER real money, never Bovada automation).
Per game, sequential: MC baseline (engine, 20k sims >= 5k spec) -> x TCI*PPE full weight, bounded -> UF gate (all sports)
-> TF_dampened (marv/tci_ppe_uf.py, MARV_MASTER=1 writes master_* into predictions.json) -> extremes rule for TOTALS here.
Extremes (totals only; sides are NOT bypassed): proj - line >= TH -> High Shootout, Over only; <= -TH -> Defensive Grind,
Under only; inside -> dead zone, no total bet. Side/script agreement logged for analysis only.
Ledger state/master/ledger.json (master + legacy side picks A/B, master totals), decisions.jsonl, summary.json (/reportcard).
Kill: touch state/master/KILL  |  systemctl disable --now marv-master.timer"""
import json, os, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0, "/opt/marv-bot/experiments/script_totals")
import script_ou as so

ST = Path("/opt/marv-bot/state"); D = ST / "master"; LED = D / "ledger.json"; SUM = D / "summary.json"; DEC = D / "decisions.jsonl"
TH = {"nfl": 3.0, "cfb": 3.0, "wnba": 4.0, "nba": 5.0, "mlb": 1.0, "soccer": 0.6}
SPORTS = ("nfl", "cfb", "wnba", "nba", "mlb")
so.ESPN.setdefault("mlb", "baseball/mlb"); so.SD.setdefault("mlb", 4.4)
now = so.now

def line_for(key, v):
    t = so.bov(v["sport"], v.get("home"), v.get("away"))
    ln = so.jl(ST / "lines.json", {}).get(key) or {}
    sp = ln.get("spread_last", ln.get("spread"))
    if t: return float(t[0]), float(t[1] or -110), float(t[2] or -110), "Bovada", sp
    if ln.get("total") is not None: return float(ln.get("total_last") or ln["total"]), -110.0, -110.0, "consensus (-110 assumed)", sp
    return None

def agree(v, side_pick, tot_side, line, spread_home):
    """Over agrees if the side team is projected above its implied team total; Under if its opponent is projected below."""
    if spread_home is None: return None
    home = side_pick == v["home"]
    imp_home = line / 2 - spread_home / 2
    imp_side, imp_opp = (imp_home, line - imp_home) if home else (line - imp_home, imp_home)
    ps, po = (v["home_exp"], v["away_exp"]) if home else (v["away_exp"], v["home_exp"])
    return bool(ps > imp_side) if tot_side > 0 else bool(po < imp_opp)

def grade_side(r):
    st = so.ts(r["start"])
    if not st or now < st + timedelta(hours=3): return
    for dd in dict.fromkeys([(st - timedelta(hours=6)).date(), st.date()]):
        for h, a, hs, as_ in so.finals(r["sport"], datetime.combine(dd, datetime.min.time())):
            if so.same(h, r["home"]) and so.same(a, r["away"]):
                m = (hs - as_) if r["side"] == r["home"] else (as_ - hs)
                res = m + (r["line"] if r["market"] == "spread" else 0)
                r.update(final=f"{int(as_)}-{int(hs)}", status="W" if res > 0 else ("L" if res < 0 else "P"),
                         units=round(so.pay(r["price"]) if res > 0 else (-1.0 if res < 0 else 0.0), 3), graded=now.isoformat())
                return
    if now > st + timedelta(days=4): r.update(status="ungraded")

def main():
    D.mkdir(parents=True, exist_ok=True)
    if (D / "KILL").exists(): print("KILL"); return
    L = so.jl(LED, []); have = {r["key"] for r in L}; dec = []
    P = so.jl(ST / "predictions.json", {})
    for key, v in P.items():
        sp = v.get("sport"); st = so.ts(v.get("start"))
        if sp not in SPORTS or not st or st <= now or "master_home_win" not in v: continue
        muf = int(v.get("master_uf", 1))
        st_tot = float(v.get("master_uf_stake_total", muf))  # UF v2 stake for totals (0 kill / 0.5 half / 1)
        muf_old = int(v.get("master_uf_old", muf))
        sides = []
        for pk in v.get("master_picks") or []:
            for model, fld in (("master", "active_master"), ("legacy", "active_legacy")):
                if not pk.get(fld): continue
                k = f"{model}:side:{key}:{pk['market']}:{pk['side']}"
                if model == "master": sides.append(pk)
                if k in have: continue
                L.append(dict(key=k, model=model, kind="side", sport=sp, pred_key=key, game=v.get("game_id"), home=v["home"], away=v["away"],
                              start=v["start"], market=pk["market"], side=pk["side"], line=pk["line"] if pk["market"] == "spread" else 0,
                              price=pk["price"], p=pk["prob_master" if model == "master" else "prob_legacy"], prob_mc=pk["prob_mc"],
                              stake=float(pk.get("uf_stake", 1.0)) if model == "master" else 1.0,
                              uf_old=int(v.get("master_uf_old", muf)), uf_reason=v.get("master_uf_reason", ""),
                              status="pending", created=now.isoformat(), units=None)); have.add(k)
        if v.get("model_total") is None: continue
        li = line_for(key, v)
        if not li: dec.append(dict(t=now.isoformat(), key=key, decision="no line")); continue
        line, po, pu, book, spread_home = li
        proj = float(v["model_total"]); d = proj - line; th = TH[sp]
        tier = "high_shootout" if d >= th else ("defensive_grind" if d <= -th else "dead_zone")
        row = dict(t=now.isoformat(), key=key, sport=sp, proj=round(proj, 2), line=line, d=round(d, 2), th=th, tier=tier, master_uf=muf,
                   uf_reason=v.get("master_uf_reason", ""), uf_stake_total=st_tot, master_uf_old=muf_old)
        if tier != "dead_zone":
            side = 1 if d > 0 else -1
            ag = [dict(pick=f"{s['market']}:{s['side']}", agree=agree(v, s["side"], side, line, spread_home)) for s in sides]
            row["side_agreement"] = ag
            k = f"master:total:{key}:{'over' if side > 0 else 'under'}"
            opp = k.rsplit(":", 1)[0] + (":under" if side > 0 else ":over")
            if st_tot > 0 and k not in have and opp not in have:
                mu = 0.5 * proj + 0.5 * line
                p_over = 1 - so.ncdf((line - mu) / so.SD[sp]); p = p_over if side > 0 else 1 - p_over; price = po if side > 0 else pu
                L.append(dict(key=k, model="master", kind="total", tier=tier, sport=sp, pred_key=key, game=v.get("game_id"), home=v["home"],
                              away=v["away"], start=v["start"], line=line, proj=round(proj, 2), d=round(d, 2), side=side, price=price, book=book,
                              p=round(p, 4), ev=round(p * so.pay(price) - (1 - p), 4), side_agreement=ag, status="pending",
                              stake=st_tot, uf_old=muf_old, uf_reason=v.get("master_uf_reason", ""),
                              created=now.isoformat(), units=None, close=None, clv=None)); have.add(k)
                print("NEW total", k, d)
            row["decision"] = ("bet" if st_tot >= 1 else "bet half stake (UF v2)") if st_tot > 0 else "UF=0 kill"
            row["decision_uf_v1"] = "bet" if muf_old == 1 else "UF=0 kill"
        else:
            row["decision"] = "no total bet (dead zone)"
        dec.append(row)
    try:  # soccer totals via the Marv soccer card (UF from its hard vetoes), Master threshold 0.6 goals
        for c in so.candidates():
            if c["sport"] != "soccer" or abs(c["d"]) < TH["soccer"]: continue
            k = "master:total:" + c["key"].split(":", 1)[1]
            if c["uf"] != 1 or k in have: continue
            c.update(key=k, model="master", kind="total", tier="high_shootout" if c["side"] > 0 else "defensive_grind",
                     status="pending", created=now.isoformat(), units=None, close=None, clv=None)
            L.append(c); have.add(k)
    except Exception as ex: print("soccer err", ex)
    for r in L:
        if r.get("status") != "pending": continue
        if r["kind"] == "total": so.refresh_close(r); so.grade(r)
        else: grade_side(r)
        if r.get("units") is not None and r.get("stake", 1.0) != 1.0 and "units_full" not in r:  # UF v2 half stake
            r["units_full"] = r["units"]; r["units"] = round(r["units"] * r["stake"], 3)
    tmp = LED.with_suffix(".tmp"); json.dump(L, open(tmp, "w"), indent=1); os.replace(tmp, LED)
    with open(DEC, "a") as f:
        for x in dec: f.write(json.dumps(x) + "\n")
    s = summary(L)
    try:
        s["script_tracker"] = tr = track(P)
        gg = tr["games_by_grade"]; ext = [gg[k] for k in ("High Shootout", "Def Grind") if k in gg]
        n = sum(x["n"] for x in ext); h = sum(x["dir_hit"] for x in ext)
        pp = tr["props_by_grade"]; pn = sum(x["n"] for k, x in pp.items() if k != "Neutral"); ph = sum(x["lean_hit"] for x in pp.values())
        s["line"] += f" | script read: extremes {h}/{n} right, props lean {ph}/{pn} right"
    except Exception as ex:
        s["script_tracker_err"] = str(ex)[:200]
    json.dump(s, open(SUM, "w"), indent=1); print(s["line"])

def rec(G):
    G = [r for r in G if r.get("status") in ("W", "L", "P")]
    return dict(W=sum(r["status"] == "W" for r in G), L=sum(r["status"] == "L" for r in G), P=sum(r["status"] == "P" for r in G),
                units=round(sum(r["units"] for r in G), 2))

def summary(L):
    o = {"updated": now.isoformat(), "thresholds": TH}
    o["master_all"] = rec([r for r in L if r["model"] == "master"])
    o["master_sides"] = rec([r for r in L if r["model"] == "master" and r["kind"] == "side"])
    o["legacy_sides"] = rec([r for r in L if r["model"] == "legacy"])
    o["totals_by_tier"] = {t: rec([r for r in L if r.get("tier") == t]) for t in ("high_shootout", "defensive_grind")}
    o["agree"] = rec([r for r in L if r["kind"] == "total" and any(a.get("agree") for a in r.get("side_agreement") or [])])
    o["disagree"] = rec([r for r in L if r["kind"] == "total" and r.get("side_agreement") and not any(a.get("agree") for a in r["side_agreement"])])
    o["pending"] = sum(r.get("status") == "pending" for r in L)
    # UF A/B: v1 = binary kill (rows v1 would have killed excluded, full stake); v2 = live per-reason stakes
    def rec1(G):
        G = [r for r in G if r.get("status") in ("W", "L", "P") and r.get("uf_old", 1) == 1]
        return dict(W=sum(r["status"] == "W" for r in G), L=sum(r["status"] == "L" for r in G), P=sum(r["status"] == "P" for r in G),
                    units=round(sum(r.get("units_full", r["units"]) for r in G), 2))
    M = [r for r in L if r["model"] == "master"]
    o["uf_ab"] = {"v2_live": rec(M), "v1_binary": rec1(M), "v2_half_stake_rows": sum(r.get("stake", 1.0) == 0.5 for r in M)}
    f = lambda x: f"{x['W']}-{x['L']}-{x['P']} {x['units']:+.2f}u"
    tb = o["totals_by_tier"]
    o["line"] = (f"Master (tracking, unproven): {f(o['master_all'])} | sides {f(o['master_sides'])} vs legacy {f(o['legacy_sides'])}"
                 f" | totals Shootout {f(tb['high_shootout'])}, Grind {f(tb['defensive_grind'])} | {o['pending']} pending")
    return o

# ---------------- predicted-vs-actual tracker (game script + props), 2026-10-13
TRK = D / "script_track.json"; PROPR = D / "prop_reads.jsonl"; PROPG = D / "prop_graded.json"
sys.path.insert(0, "/opt/marv-feed"); import game_script as GS

def espn_summary(sport, eid):
    import requests
    path = so.ESPN.get(sport) or so.ESPN.get(GS.SPORT.get(sport, sport))
    try: return requests.get(f"https://site.api.espn.com/apis/site/v2/sports/{path}/summary", params={"event": eid}, timeout=20).json()
    except Exception: return None

def plays_from(summ):
    try:
        tot = 0
        for t in summ["boxscore"]["teams"]:
            for s in t.get("statistics", []):
                if s.get("name") in ("totalOffensivePlays",): tot += float(s.get("displayValue") or 0)
        return tot or None
    except Exception: return None

def track(P):
    T = so.jl(TRK, {})
    for key, v in P.items():
        sp = v.get("sport"); st = so.ts(v.get("start"))
        if sp not in SPORTS or not st or v.get("model_total") is None: continue
        if st > now and key not in T or (st > now and T.get(key, {}).get("actual_total") is None):
            r = GS.read(sp, v["start"], v.get("home"), preds={key: v})
            if r and r.get("grade"):
                T[key] = dict(sport=sp, game=v.get("game_id"), home=v["home"], away=v["away"], start=v["start"], proj_total=r["proj_total"],
                              market_total=r["market_total"], d=r["d"], pace=r["pace"], grade=r["grade"], updated=now.isoformat())
    for key, t in T.items():
        if t.get("actual_total") is not None: continue
        st = so.ts(t["start"])
        if not st or now < st + timedelta(hours=3): continue
        for dd in dict.fromkeys([(st - timedelta(hours=6)).date(), st.date()]):
            for h, a, hs, as_ in so.finals(t["sport"], datetime.combine(dd, datetime.min.time())):
                if so.same(h, t["home"]) and so.same(a, t["away"]):
                    tot = hs + as_; t.update(actual_total=tot, over=(tot > t["market_total"]) if tot != t["market_total"] else None)
                    if t["sport"] in ("nfl", "cfb") and str(t.get("game", "")).isdigit():
                        t["actual_plays"] = plays_from(espn_summary(t["sport"], t["game"]))
        if t.get("actual_total") is None and now > st + timedelta(days=4): t["actual_total"] = "ungraded"
    json.dump(T, open(TRK, "w"), indent=1)
    G = so.jl(PROPG, {})
    if PROPR.exists():
        sys.path.insert(0, os.path.expanduser("~/aj-bet-bot"))
        from ajbet.boxscore import stat_for
        cache = {}
        for ln in open(PROPR):
            try: r = json.loads(ln)
            except Exception: continue
            k = f"{r['event_id']}:{r['player']}:{r['market']}:{r['line']}"
            if k in G and G[k].get("actual") is not None: continue
            G.setdefault(k, r)
            st = so.ts(r["commence"])
            if not st or now < st + timedelta(hours=4) or not r.get("event_id"): continue
            sm = cache.get(r["event_id"]) or espn_summary(r["league"], r["event_id"]); cache[r["event_id"]] = sm
            a = stat_for(sm or {}, r["player"], r["market"]) if sm else None
            if a is not None: G[k]["actual"] = a; G[k]["went_over"] = a > r["line"]
    json.dump(G, open(PROPG, "w"), indent=1)
    acc = {}
    for t in T.values():
        if t.get("over") is None or t.get("actual_total") == "ungraded": continue
        g = acc.setdefault(t["grade"], {"n": 0, "dir_hit": 0, "abs_err": 0.0})
        lean = 1 if "Shootout" in t["grade"] else (-1 if "Grind" in t["grade"] else 0)
        g["n"] += 1; g["abs_err"] += abs(t["actual_total"] - t["proj_total"])
        if lean: g["dir_hit"] += int((lean > 0) == t["over"])
    for g in acc.values(): g["abs_err"] = round(g["abs_err"] / g["n"], 2)
    pacc = {}
    for r in G.values():
        if r.get("went_over") is None or not (r.get("script") or {}).get("grade"): continue
        gr = r["script"]["grade"]; lean = 1 if "Shootout" in gr else (-1 if "Grind" in gr else 0)
        x = pacc.setdefault(gr, {"n": 0, "lean_hit": 0}); x["n"] += 1
        if lean: x["lean_hit"] += int((lean > 0) == r["went_over"])
    return {"games_by_grade": acc, "props_by_grade": pacc, "games_tracked": len(T), "props_tracked": len(G)}

if __name__ == "__main__":
    main()
