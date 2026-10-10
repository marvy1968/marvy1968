"""CFB upset / soft-favourite backtest for Marvin's HPR hybrid (PAPER research), walk-forward.

HPR (Marvin's cfb_hybrid_simulation, made scale-free and fit walk-forward):
  z_off, z_def      season-to-date points/drive for / against, z-scored vs earlier seasons' league
  d_off, d_def      last-3 minus season (z units)                       -- "rolling delta"
  decay             min(1, 1 / (1 + KD * |d_off|))                        -- dynamic velocity decay (pasted KD 0.5)
  bcr_mult          1 + max(0, BCR - 0.40) * KB * depth                   -- pasted KB 0.4
                    BCR = 4/5-star share of the last 4 HS classes (CFBD recruiting), depth = 247 talent / FBS median
  HPR               (z_off - z_def + WO * d_off * decay - WD * d_def) * bcr_mult * 10   (pasted WO 1.2, WD 0.8)
  margin            HFA + C * (HPR_home - HPR_away)  (lstsq, earlier seasons); P(win) = Phi(margin / (11.5 * sqrt 2))
                    (= MC with 10,000 sims, each team's score sd 11.5)
Upset rule: heavy favourite (spread <= -6.5) is SOFT when the model margin for the favourite is >= T points under the
market spread. Bet the underdog ATS at -110; also report the outright upset rate. Grid (WO, WD, KD, KB, T) chosen on
earlier seasons by dog-ATS ROI on soft favourites (n>=60), graded on the next season (walk-forward).
BCR false-upset stress test: soft favourites split by (fav BCR > 0.70 & dog BCR < 0.45) vs the rest.
Usage: nice .venv/bin/python tools/cfb_upset_bt.py  -> state/reports/cfb_upset_backtest.json, cfb_upset_games.csv
"""
import itertools, json, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import hybrid_bt as HB  # noqa: E402
HEAVY = -6.5


def load():
    m = HB.games("cfb")
    m["margin"] = m.home_points - m.away_points
    b = pd.read_csv(HB.CACHE / "cfb_bcr.csv")
    b["depth"] = pd.to_numeric(b.depth, errors="coerce").fillna(1.0)
    for side, col in (("h", "home"), ("a", "away")):
        m = m.merge(b[["season", "team", "bcr", "depth"]].rename(columns={"team": col, "bcr": f"{side}_bcr", "depth": f"{side}_depth"}),
                    on=["season", col], how="left")
    m["h_bcr"] = m.h_bcr.fillna(0.0); m["a_bcr"] = m.a_bcr.fillna(0.0)
    m["h_depth"] = m.h_depth.fillna(0.85); m["a_depth"] = m.a_depth.fillna(0.85)
    return m[m.spread.notna()].reset_index(drop=True)


def hpr(m, s, lg, WO, WD, KD, KB, B0=0.40):
    (mo, so), (md, sd) = lg
    zo, zd = (m[f"{s}_off"] - mo) / so, (m[f"{s}_def"] - md) / sd
    do, dd = (m[f"{s}_off3"] - m[f"{s}_off"]) / so, (m[f"{s}_def3"] - m[f"{s}_def"]) / sd
    decay = np.minimum(1, 1 / (1 + KD * do.abs()))
    mult = 1 + np.maximum(0, m[f"{s}_bcr"] - B0) * KB * m[f"{s}_depth"]
    return (zo - zd + WO * do * decay - WD * dd) * mult * 10


def model(m, lg, p):
    x = (hpr(m, "h", lg, *p) - hpr(m, "a", lg, *p)).to_numpy()
    return np.column_stack([(~m.neutral.astype(bool)).astype(float), x])


def soft_mask(m, proj, T):
    fav_home = (m.spread <= HEAVY).to_numpy(); fav_away = (m.spread >= -HEAVY).to_numpy()
    fav_model = np.where(fav_home, proj, -proj); fav_mkt = np.where(fav_home, -m.spread, m.spread)
    heavy = fav_home | fav_away
    return heavy, heavy & (fav_model <= fav_mkt - T), fav_home


def outcomes(m, fav_home):
    ar = (m.margin + m.spread).to_numpy()
    dog_cov = np.where(fav_home, ar < 0, ar > 0); push = ar == 0
    upset = np.where(fav_home, m.margin < 0, m.margin > 0)
    return dog_cov, push, upset


def rate(ok):
    ok = np.asarray(ok); n = len(ok); w = int(ok.sum())
    return {"n": n, "hit": round(w / n, 4) if n else None, "roi": round((w * 100 / 110 - (n - w)) / n, 4) if n else None}


GRID = list(itertools.product((0.0, 0.6, 1.2), (0.0, 0.8), (0.0, 0.5, 1.0), (0.0, 0.4, 0.8), (3.0, 5.0, 7.0, 10.0)))


def run():
    m = load()
    out, fits = [], {}
    for s in sorted(m.season.unique()):
        tr, te = m[m.season < s].reset_index(drop=True), m[m.season == s].reset_index(drop=True)
        if tr.season.nunique() < 2 or te.empty:
            continue
        lg = ((tr.h_off.mean(), tr.h_off.std()), (tr.h_def.mean(), tr.h_def.std()))
        best, cache = None, {}
        for WO, WD, KD, KB, T in GRID:
            key = (WO, WD, KD, KB)
            if key not in cache:
                X = model(tr, lg, key); c = np.linalg.lstsq(X, tr.margin.to_numpy(), rcond=None)[0]
                cache[key] = (c, X @ c, float(np.mean(np.abs(X @ c - tr.margin))))
            c, proj, _ = cache[key]
            _, soft, fh = soft_mask(tr, proj, T)
            dc, push, _ = outcomes(tr, fh)
            k = soft & ~push
            if k.sum() < 60:
                continue
            r = rate(dc[k])
            if best is None or r["roi"] > best[0]:
                best = (r["roi"], (WO, WD, KD, KB, T), c, r)
        acc_key, acc = min(cache.items(), key=lambda kv: kv[1][2])
        (WO, WD, KD, KB, T), c = best[1], best[2]
        proj = model(te, lg, (WO, WD, KD, KB)) @ c
        proj_acc = model(te, lg, acc_key) @ acc[0]
        heavy, soft, fh = soft_mask(te, proj, T)
        dc, push, up = outcomes(te, fh)
        fits[int(s)] = {"WO": WO, "WD": WD, "KD": KD, "KB": KB, "T": T, "HFA": round(float(c[0]), 2), "C": round(float(c[1]), 4),
                        "train": best[3], "acc_params": acc_key, "acc_HFA_C": [round(float(x), 4) for x in acc[0]],
                        "acc_train_mae": round(acc[2], 3)}
        print(s, fits[int(s)], flush=True)
        out.append(pd.DataFrame({"season": int(s), "game_id": te.game_id, "home": te.home, "away": te.away, "spread": te.spread,
                                 "margin": te.margin, "proj": proj, "proj_acc": proj_acc, "heavy": heavy, "soft": soft,
                                 "dog_cover": dc, "push": push, "upset": up,
                                 "fav_bcr": np.where(fh, te.h_bcr, te.a_bcr), "dog_bcr": np.where(fh, te.a_bcr, te.h_bcr)}))
    df = pd.concat(out, ignore_index=True)
    r = {"fits": fits}
    mism = (df.fav_bcr > 0.70) & (df.dog_bcr < 0.45)
    def blk(mk):
        d = df[mk]
        return {**rate(d.dog_cover[~d.push]), "upset_rate": round(float(d.upset.mean()), 4) if len(d) else None, "games": int(len(d)),
                "seasons": {int(s): rate(x.dog_cover[~x.push]) for s, x in d.groupby("season")}}
    r["heavy_base"] = blk(df.heavy)
    r["soft_rule"] = blk(df.soft)
    r["soft_bcr_mismatch"] = blk(df.soft & mism)
    r["soft_no_mismatch"] = blk(df.soft & ~mism)
    r["heavy_bcr_mismatch"] = blk(df.heavy & mism)
    r["heavy_no_mismatch"] = blk(df.heavy & ~mism)
    dec = df.margin != 0
    r["ml_acc_params"] = round(float(((df.proj_acc > 0) == (df.margin > 0))[dec].mean()), 4)
    r["market_fav_ml"] = round(float(((df.spread < 0) == (df.margin > 0))[dec & (df.spread != 0)].mean()), 4)
    r["margin_mae_acc"] = round(float((df.proj_acc - df.margin).abs().mean()), 2)
    r["market_mae"] = round(float((-df.spread - df.margin).abs().mean()), 2)
    r["proven"] = {k: HB.passes(r[k]) for k in ("soft_rule", "soft_no_mismatch")}
    return r, df


if __name__ == "__main__":
    out = ROOT / "state" / "reports"
    r, df = run()
    df.to_csv(out / "cfb_upset_games.csv", index=False)
    (out / "cfb_upset_backtest.json").write_text(json.dumps(r, indent=1, default=str))
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "seasons"} if isinstance(v, dict) else v
                      for k, v in r.items() if k != "fits"}, default=str))
