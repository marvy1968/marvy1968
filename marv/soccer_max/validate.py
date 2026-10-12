"""Walk-forward validation of Marv Soccer Predict Max (blueprint sections 13, 14, 20).

Point-in-time: every week's matches are projected from ratings fitted on matches strictly before that week; the shots
-> xG proxy is fitted on earlier seasons; hyper-parameters (decay, goal/xG blend, H2H and trend weights, Dixon-Coles
rho) for test season S are chosen on seasons before S only (pooled across leagues). No closing odds, lineups or
post-match stats enter a prediction.

Graded per league / market / season:
  * 1X2 Brier + log loss: model vs league base rates (baseline) vs the closing market (Pinnacle, else Betfair
    exchange, else the market average, no-vig) -> the calibration route of marv/proven.py
  * O/U 2.5 Brier vs the closing market; goals MAE
  * paper bets: model edge >= threshold vs the pre-match no-vig price, paid at the pre-match price (Bovada, else market
    average) -> hit, ROI, closing-line value; per-season stability
Bar (marv/proven.py): n >= 100, ROI > 0 after the vig, positive in most seasons; or Brier <= closing market (n >= 300).
"""

import itertools
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import data as D
from . import engine as E

log = logging.getLogger(__name__)
GRID_POWER = [(hl, w) for hl in (180.0, 365.0) for w in (0.5, 0.8)]
GRID_ADJ = list(itertools.product((0.0, 0.06, 0.12), (0.0, 0.03, 0.06), (-0.10, -0.05, 0.0)))
THRESHOLDS = (0.03, 0.05, 0.08)
EPS = 1e-12


def _components(df: pd.DataFrame, key: str, first_test: int) -> pd.DataFrame:
    """Per match and power combo: point-in-time base xG, H2H net, trend diff."""
    out = []
    seasons = sorted(int(s) for s in df["season"].dropna().unique())
    for S in seasons:
        if S < first_test:
            continue
        proxy = E.fit_proxy(df[df["season"] < S]) or E.fit_proxy(df[df["season"] == S])
        if df["hst"].notna().sum() == 0:
            proxy = None
        rows = E.team_rows(df, proxy)
        test = df[(df["season"] == S) & df["played"]]
        if test.empty:
            continue
        weeks = test["date"].dt.to_period("W-SUN").dt.start_time
        for wk, grp in test.groupby(weeks):
            asof = pd.Timestamp(wk)
            for ci, (hl, w) in enumerate(GRID_POWER):
                p = E.Params(half_life_days=hl, goal_weight=w)
                r = E.fit_ratings(df, asof, p, proxy, rows)
                if not r:
                    continue
                for idx, m in grp.iterrows():
                    if m["home"] not in r["teams"] or m["away"] not in r["teams"]:
                        continue
                    th, ta = r["teams"][m["home"]], r["teams"][m["away"]]
                    net, *_ = E.h2h_net(r, m["home"], m["away"])
                    out.append({"idx": idx, "combo": ci, "league": key, "season": S, "date": m["date"],
                                "bh": r["mu_h"] * th["att"] * ta["def"], "ba": r["mu_a"] * ta["att"] * th["def"],
                                "net": net, "td": E.trend_diff(r, m["home"], m["away"]),
                                "nmin": min(th["n_window"], ta["n_window"])})
    comp = pd.DataFrame(out)
    if comp.empty:
        return comp
    cols = ["hg", "ag", "o_h", "o_d", "o_a", "o_over", "o_under", "c_h", "c_d", "c_a", "c_over", "c_under", "home", "away"]
    return comp.join(df[cols], on="idx")


def _probs(c: pd.DataFrame, kh: float, kt: float, rho: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    adj = kh * c["net"].to_numpy() + kt * c["td"].to_numpy() / 2
    lh = np.clip(c["bh"].to_numpy() * np.exp(adj), 0.15, 4.5)
    la = np.clip(c["ba"].to_numpy() * np.exp(-adj), 0.15, 4.5)
    m = E.score_matrices(lh, la, rho)
    return E.probs_1x2(m), E.p_over(m, 2.5), lh, la


def _outcome(c: pd.DataFrame) -> np.ndarray:
    y = np.zeros((len(c), 3))
    y[np.arange(len(c)), np.where(c["hg"] > c["ag"], 0, np.where(c["hg"] == c["ag"], 1, 2))] = 1
    return y


def _logloss(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    return -np.log(np.clip((p * y).sum(1), EPS, 1))


def _brier(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    return ((p - y) ** 2).sum(1)


def _novig(a: np.ndarray) -> np.ndarray:
    inv = 1 / a
    return inv / inv.sum(1, keepdims=True)


def _select(c: pd.DataFrame) -> tuple[int, tuple]:
    """Best (power combo, (k_h2h, k_trend, rho)) by log loss on the given (training) matches."""
    best, arg = np.inf, (0, GRID_ADJ[0])
    y_cache = {}
    for ci in sorted(c["combo"].unique()):
        cc = c[c["combo"] == ci]
        y = y_cache.setdefault(ci, _outcome(cc))
        for adj in GRID_ADJ:
            p, *_ = _probs(cc, *adj)
            ll = float(_logloss(p, y).mean())
            if ll < best:
                best, arg = ll, (int(ci), adj)
    return arg


def _bets(c: pd.DataFrame, p: np.ndarray, pov: np.ndarray, thr: float) -> pd.DataFrame:
    """Paper bets: best positive-EV selection with edge >= thr vs the pre-match no-vig price, paid at that price."""
    rows = []
    o = c[["o_h", "o_d", "o_a"]].to_numpy(float)
    cl = c[["c_h", "c_d", "c_a"]].to_numpy(float)
    ou = c[["o_over", "o_under"]].to_numpy(float)
    clou = c[["c_over", "c_under"]].to_numpy(float)
    hg, ag = c["hg"].to_numpy(), c["ag"].to_numpy()
    res = np.where(hg > ag, 0, np.where(hg == ag, 1, 2))
    over = (hg + ag) > 2.5
    for i in range(len(c)):
        if np.all(np.isfinite(o[i])) and np.all(o[i] > 1):
            nv = (1 / o[i]) / (1 / o[i]).sum()
            e = p[i] - nv
            ev = p[i] * o[i] - 1
            j = int(np.argmax(np.where(e >= thr, ev, -np.inf)))
            if e[j] >= thr and ev[j] > 0 and o[i][j] <= 6.0:
                clv = (o[i][j] / cl[i][j] - 1) if np.isfinite(cl[i][j]) and cl[i][j] > 1 else np.nan
                rows.append({"market": "1x2", "season": c["season"].iat[i], "league": c["league"].iat[i],
                             "win": res[i] == j, "price": o[i][j], "profit": (o[i][j] - 1) if res[i] == j else -1.0,
                             "clv": clv})
        if np.all(np.isfinite(ou[i])) and np.all(ou[i] > 1):
            nv = (1 / ou[i]) / (1 / ou[i]).sum()
            pm = np.array([pov[i], 1 - pov[i]])
            e, ev = pm - nv, pm * ou[i] - 1
            j = int(np.argmax(ev))
            if e[j] >= thr and ev[j] > 0:
                win = over[i] if j == 0 else not over[i]
                clv = (ou[i][j] / clou[i][j] - 1) if np.isfinite(clou[i][j]) and clou[i][j] > 1 else np.nan
                rows.append({"market": "total", "season": c["season"].iat[i], "league": c["league"].iat[i], "win": win,
                             "price": ou[i][j], "profit": (ou[i][j] - 1) if win else -1.0, "clv": clv})
    return pd.DataFrame(rows)


def _summ(b: pd.DataFrame) -> dict:
    if b.empty:
        return {"n": 0}
    by = b.groupby("season")["profit"].agg(["count", "mean"])
    return {"n": int(len(b)), "hit": round(float(b["win"].mean()), 4), "roi": round(float(b["profit"].mean()), 4),
            "avg_price": round(float(b["price"].mean()), 3), "clv": round(float(b["clv"].mean()), 4) if b["clv"].notna().any() else None,
            "seasons_positive": f"{int((by['mean'] > 0).sum())}/{len(by)}",
            "by_season": {int(s): {"n": int(r["count"]), "roi": round(float(r["mean"]), 4)} for s, r in by.iterrows()}}


def run(cache_dir: Path, out_dir: Path, leagues: list[str] | None = None, first_test: int = 2019,
        report_from: int = 2020) -> dict:
    t0 = time.time()
    leagues = leagues or list(D.LEAGUES)
    comps = []
    for key in leagues:
        df = D.load_league(key, cache_dir)
        if df.empty:
            log.warning("%s: no data", key)
            continue
        c = _components(df, key, first_test)
        log.info("%s: %d match-combos (%.0fs)", key, len(c), time.time() - t0)
        if not c.empty:
            comps.append(c)
    allc = pd.concat(comps, ignore_index=True)
    seasons = sorted(allc["season"].unique())
    preds, chosen = [], {}
    for S in seasons:
        train = allc[(allc["season"] < S)]
        ci, adj = _select(train) if not train.empty else (GRID_POWER.index((365.0, 0.8)), (0.06, 0.03, -0.05))
        chosen[int(S)] = {"half_life_days": GRID_POWER[ci][0], "goal_weight": GRID_POWER[ci][1],
                          "k_h2h": adj[0], "k_trend": adj[1], "rho": adj[2]}
        cc = allc[(allc["season"] == S) & (allc["combo"] == ci)].copy()
        p, pov, lh, la = _probs(cc, *adj)
        cc[["p_h", "p_d", "p_a"]] = p
        cc["p_over"], cc["lh"], cc["la"] = pov, lh, la
        preds.append(cc)
    final_ci, final_adj = _select(allc)
    final = {"half_life_days": GRID_POWER[final_ci][0], "goal_weight": GRID_POWER[final_ci][1],
             "k_h2h": final_adj[0], "k_trend": final_adj[1], "rho": final_adj[2]}
    pr = pd.concat(preds, ignore_index=True)
    pr = pr[pr["season"] >= report_from]
    report = {"generated": datetime.now(timezone.utc).isoformat(), "model_version": E.MODEL_VERSION,
              "test_seasons": [int(s) for s in sorted(pr["season"].unique())], "params_by_season": chosen,
              "params_live": final, "leagues": {}, "pooled": {}, "runtime_s": round(time.time() - t0, 1)}

    def block(c: pd.DataFrame) -> dict:
        y = _outcome(c)
        p = c[["p_h", "p_d", "p_a"]].to_numpy()
        res = {"n": int(len(c))}
        res["model_brier"] = round(float(_brier(p, y).mean()), 4)
        res["model_logloss"] = round(float(_logloss(p, y).mean()), 4)
        base = np.tile(y.mean(0), (len(y), 1))  # league base rates (in-sample: generous to the baseline)
        res["baseline_brier"] = round(float(_brier(base, y).mean()), 4)
        mk = c[["c_h", "c_d", "c_a"]].to_numpy(float)
        ok = np.all(np.isfinite(mk) & (mk > 1), axis=1)
        if ok.sum() >= 50:
            nv = _novig(mk[ok])
            res["market_n"] = int(ok.sum())
            res["market_brier"] = round(float(_brier(nv, y[ok]).mean()), 4)
            res["model_brier_same_games"] = round(float(_brier(p[ok], y[ok]).mean()), 4)
            res["market_logloss"] = round(float(_logloss(nv, y[ok]).mean()), 4)
            res["model_logloss_same_games"] = round(float(_logloss(p[ok], y[ok]).mean()), 4)
        over = ((c["hg"] + c["ag"]) > 2.5).to_numpy(float)
        res["ou25_model_brier"] = round(float(((c["p_over"] - over) ** 2).mean()), 4)
        mo = c[["c_over", "c_under"]].to_numpy(float)
        ok2 = np.all(np.isfinite(mo) & (mo > 1), axis=1)
        if ok2.sum() >= 50:
            nvo = _novig(mo[ok2])[:, 0]
            res["ou25_market_brier"] = round(float(((nvo - over[ok2]) ** 2).mean()), 4)
            res["ou25_model_brier_same_games"] = round(float(((c["p_over"].to_numpy()[ok2] - over[ok2]) ** 2).mean()), 4)
        res["goals_mae"] = round(float((np.abs(c["lh"] - c["hg"]) + np.abs(c["la"] - c["ag"])).mean() / 2), 3)
        res["bets"] = {}
        for thr in THRESHOLDS:
            b = _bets(c, p, c["p_over"].to_numpy(), thr)
            res["bets"][f"edge>={thr:.0%}"] = {m: _summ(b[b["market"] == m]) if not b.empty else {"n": 0}
                                               for m in ("1x2", "total")}
        return res

    for key in sorted(pr["league"].unique()):
        report["leagues"][key] = block(pr[pr["league"] == key])
    report["pooled"] = block(pr)
    report["proven"] = verdicts(report)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "soccer_validation.json").write_text(json.dumps(report, indent=1, default=float))
    pr.drop(columns=["idx"]).to_csv(out_dir / "soccer_validation_predictions.csv.gz", index=False)
    return report


def verdicts(report: dict) -> dict:
    """Which (scope, market) pass marv/proven.py's bar. Nothing here prints a % by itself: a pass still has to be
    added to marv/proven.py by hand after a live paper sample."""
    out = {}
    for scope, res in [("pooled", report["pooled"]), *report["leagues"].items()]:
        cal = (res.get("market_n", 0) >= 300 and res.get("model_brier_same_games", 9) <= res.get("market_brier", 0))
        out[f"{scope}:1x2:calibration"] = bool(cal)
        for thr, mk in res.get("bets", {}).items():
            for market, s in mk.items():
                if s.get("n", 0) < 100:
                    passed = False
                else:
                    pos, tot = (int(x) for x in s["seasons_positive"].split("/"))
                    passed = s["roi"] > 0 and pos > tot / 2
                out[f"{scope}:{market}:{thr}"] = bool(passed)
    return out


def markdown(report: dict) -> str:
    lines = [f"# Marv Soccer Predict Max - walk-forward validation ({report['generated'][:10]})", "",
             f"Test seasons {report['test_seasons']} (season = start year), hyper-parameters chosen on earlier seasons only.",
             f"Live params: {report['params_live']}", "",
             "| scope | n | model Brier | market Brier (close) | baseline Brier | model LL | market LL | O/U2.5 model | O/U2.5 mkt | 1X2 bets e>=5% n / hit / ROI / CLV / seasons+ | O/U bets e>=5% n / hit / ROI / seasons+ |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for scope, r in [("POOLED", report["pooled"]), *sorted(report["leagues"].items())]:
        b1 = r["bets"].get("edge>=5%", {}).get("1x2", {})
        b2 = r["bets"].get("edge>=5%", {}).get("total", {})
        fb = lambda b: (f"{b['n']} / {b['hit']:.1%} / {b['roi']:+.1%} / {b.get('clv') if b.get('clv') is None else format(b['clv'], '+.1%')} / {b['seasons_positive']}"
                        if b.get("n") else "0")
        lines.append(f"| {scope} | {r['n']} | {r.get('model_brier_same_games', r['model_brier'])} | {r.get('market_brier', '-')} | "
                     f"{r['baseline_brier']} | {r.get('model_logloss_same_games', r['model_logloss'])} | {r.get('market_logloss', '-')} | "
                     f"{r.get('ou25_model_brier_same_games', r['ou25_model_brier'])} | {r.get('ou25_market_brier', '-')} | {fb(b1)} | {fb(b2)} |")
    passed = [k for k, v in report["proven"].items() if v]
    lines += ["", f"Proven-bar passes: {passed or 'none'}",
              "A pass is necessary, not sufficient: promotion to marv/proven.py also needs a live paper sample."]
    return "\n".join(lines)
