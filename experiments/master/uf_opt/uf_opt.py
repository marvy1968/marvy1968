"""UF optimisation study (paper). Joins Master bet universe with per-reason UF flags; evaluates gating policies on two windows."""
import json, sys, numpy as np, pandas as pd
D = "/opt/marv-bot/experiments/master/uf_opt/"
TH = dict(nfl=3.0, cfb=3.0, wnba=4.0, soccer=0.6)
F = pd.read_csv(D+"flags.csv", dtype={"gid": str}, parse_dates=["date"])
REA = ["rivalry", "emotional", "weather", "injury"]
def reasons(r, inj="inj_key"):
    return [k for k, c in (("rivalry", "rival"), ("emotional", "emotional"), ("weather", "weather"), ("injury", inj)) if r[c]]
# totals: Master extremes rule
T = pd.read_csv("/opt/marv-bot/experiments/script_totals/bets_totals.csv", parse_dates=["date"])
T = T[T.d.abs() >= T.sport.map(TH)].copy(); T["market"] = "total"
T["gkey"] = T.date.dt.strftime("%Y-%m-%d")+"|"+T.game
T = T.merge(F.drop(columns=["date", "sport"]), on="gkey", how="left")
T["strength"] = T.d.abs()/T.sport.map(TH)            # 1.0 = at threshold
S = pd.read_csv(D+"bets_tf_2026-08-01.csv", dtype={"gid": str}, parse_dates=["date"]).rename(columns={"edge": "e"})
S = S.merge(F.drop(columns=["date", "sport", "gkey"]), on="gid", how="left")
S["strength"] = S.e/0.03                             # 1.0 = at the 3% edge floor
X = pd.concat([T[["sport", "date", "market", "units", "strength", "rival", "emotional", "weather", "inj_any", "inj_start", "inj_key"]],
               S[["sport", "date", "market", "units", "strength", "rival", "emotional", "weather", "inj_any", "inj_start", "inj_key"]]])
for c in ["rival", "emotional", "weather", "inj_any", "inj_start", "inj_key"]: X[c] = X[c].fillna(False).astype(bool)
X["mk"] = np.where(X.market == "total", "totals", "sides")
X = X[X.date < "2026-10-13"].sort_values("date").reset_index(drop=True)
WIN = {"Sep21-Oct12": "2026-09-21", "Aug01-Oct12": "2026-08-01"}

def stake(r, pol):
    """pol: dict reason -> 'kill'|'half'|'off'; inj: which injury flag; ovr: strength above which kill/half is waived;
    riv_small: rivalry only applies when strength < this."""
    rs = reasons(r, pol.get("inj", "inj_key"))
    if pol.get("riv_small") and "rivalry" in rs and r.strength >= pol["riv_small"]: rs.remove("rivalry")
    acts = [pol.get(f"{k}@{r.sport}", pol.get(f"{k}@{r.mk}", pol.get(k, "kill"))) for k in rs]; acts = [a for a in acts if a != "off"]
    if not acts: return 1.0
    if pol.get("ovr") and r.strength >= pol["ovr"]: return 1.0
    return 0.0 if "kill" in acts else 0.5

def ev(W, pol):
    s = W.apply(lambda r: stake(r, pol), axis=1); u = W.units*s
    cu = u.cumsum().values; dd = float((np.maximum.accumulate(np.r_[0, cu])[1:]-cu).max()) if len(cu) else 0
    cut = 1-s
    return dict(W=int(((u > 0)).sum()), L=int((u < 0).sum()), units=round(u.sum(), 2), risked=round(s.sum(), 1),
                roi=round(u.sum()/max(s.sum(), 1e-9), 3), maxdd=round(dd, 2),
                losers_cut_u=round(float((-W.units*cut)[W.units < 0].sum()), 2), winners_cut_u=round(float((W.units*cut)[W.units > 0].sum()), 2))

POL = {
 "NO_UF": dict(rivalry="off", emotional="off", weather="off", injury="off"),
 "CURRENT (all kill, inj=key)": {},
 "CURRENT inj=any": dict(inj="inj_any"),
 "rivalry off": dict(rivalry="off"),
 "emotional off": dict(emotional="off"),
 "injury off": dict(injury="off"),
 "inj=starter": dict(inj="inj_start"),
 "rivalry half": dict(rivalry="half"),
 "emotional half": dict(emotional="half"),
 "rivalry only if small edge (<1.5x)": dict(riv_small=1.5),
 "override >=2x": dict(ovr=2.0),
 "override >=2.5x": dict(ovr=2.5),
 "riv half + emo kill + ovr2x": dict(rivalry="half", ovr=2.0),
 "riv small1.5 + ovr2x": dict(riv_small=1.5, ovr=2.0),
 "riv half + emo half + ovr2x": dict(rivalry="half", emotional="half", ovr=2.0),
 "riv half + ovr2.5x": dict(rivalry="half", ovr=2.5),
 "emo off WNBA": {"emotional@wnba": "off"},
 "emo half WNBA": {"emotional@wnba": "half"},
 "inj off sides": {"injury@sides": "off"},
 "inj half sides": {"injury@sides": "half"},
 "injury half": dict(injury="half"),
 "PROPOSED: emo half WNBA + inj half sides": {"emotional@wnba": "half", "injury@sides": "half"},
 "emo off WNBA + inj off sides": {"emotional@wnba": "off", "injury@sides": "off"},
 "riv off WNBA": {"rivalry@wnba": "off"},
}
if __name__ == "__main__":
    SHOW = (list(POL)[:2] + list(POL)[16:]) if sys.argv[1:] else None
    out = {"breakdown": {}, "policies": {}}
    for wn, t0 in WIN.items():
        W = X[X.date >= t0]
        bd = []
        for rn, col in (("rivalry", "rival"), ("emotional", "emotional"), ("weather", "weather"), ("injury(key)", "inj_key"),
                        ("injury(starter)", "inj_start"), ("injury(any)", "inj_any")):
            for (sp, mk), g in W[W[col]].groupby(["sport", "mk"]):
                bd.append(dict(reason=rn, sport=sp, mkt=mk, n=len(g), W=int((g.units > 0).sum()), L=int((g.units < 0).sum()), units=round(g.units.sum(), 2)))
            g = W[W[col]]; bd.append(dict(reason=rn, sport="ALL", mkt="all", n=len(g), W=int((g.units > 0).sum()), L=int((g.units < 0).sum()), units=round(g.units.sum(), 2)))
        out["breakdown"][wn] = bd
        for mk in ("totals", "sides", "all"):
            M = W if mk == "all" else W[W.mk == mk]
            for pn, p in POL.items(): out["policies"].setdefault(pn, {})[f"{wn}|{mk}"] = ev(M, p)
    # strength buckets of flagged bets (does edge size separate skipped winners from losers?)
    sb = {}
    for wn, t0 in WIN.items():
        W = X[(X.date >= t0) & (X.rival | X.emotional | X.inj_key | X.weather)]
        for lo, hi in ((1, 1.5), (1.5, 2), (2, 2.5), (2.5, 99)):
            g = W[(W.strength >= lo) & (W.strength < hi)]
            sb[f"{wn}|{lo}-{hi}x"] = dict(n=len(g), W=int((g.units > 0).sum()), L=int((g.units < 0).sum()), units=round(g.units.sum(), 2))
    out["strength"] = sb
    json.dump(out, open(D+"report.json", "w"), indent=1)
    for wn, bd in ([] if SHOW else out["breakdown"].items()):
        print("==", wn); print(pd.DataFrame(bd).to_string(index=False))
    if not SHOW: print(pd.DataFrame(sb).T.to_string())
    rows = []
    for pn, v in out["policies"].items():
        for k, m in v.items(): rows.append(dict(policy=pn, win=k, **m))
    R = pd.DataFrame(rows); pd.set_option("display.width", 250)
    for k in R.win.unique(): print("==", k); print(R[(R.win == k) & (R.policy.isin(SHOW) if SHOW else True)].drop(columns="win").to_string(index=False))
