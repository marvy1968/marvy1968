"""Daily take-profit estimator for trend stocks (owner 2026-10-30: "for every trend stock add an estimator that calculates its relative volume x
volatility x trend strength and 20-day angle upward compared to the 200, to estimate the daily TP").

PRE-DECLARED. Trend stock = fxbot.strongtrend long_state == 1 at the last close. For the NEXT session it estimates the upside from the open,
in ATR(14) units, from four inputs known at the close:
  rvol   = last volume / 20-day average volume
  vol    = ATR(14) / close (volatility), and its ratio to the 60-day average of the same
  trend  = ADX(14) / 25 (trend strength)
  angle  = 5-day slope of the 20-day average minus 5-day slope of the 200-day average, in ATRs per day ("20-day angle vs the 200")
Model: upside/ATR = (H - O)/ATR of the next day; quantile regression (median, and the 35th percentile) on log rvol, log vol ratio, trend, angle,
fitted on trend-stock days before the test window. TP = open + predicted median x ATR (hit about half the time) and a "safer" TP at the
35th percentile (hit about 65% of the time).
TEST (last 6 months, never fitted on): does the estimator beat a plain constant (each stock's own median upside/ATR over the prior year)?
  - pinball (quantile) loss of the median, model vs constant, lower is better
  - hit rates of both TPs (should be near 50% / 65%)
  - a simple trade: buy the open of every trend-stock day, take profit at the TP, else out at the close, stop 1 ATR below the open,
    10 bps a side; model TP vs constant TP vs no TP.
USABLE if the model's pinball loss is lower than the constant's AND the model-TP trade beats the constant-TP trade, in both halves.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd

COST = 10.0 / 1e4
FEATS = ["lrvol", "lvolr", "trend", "angle"]


def _dates(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    idx = pd.DatetimeIndex(d.index)
    d.index = pd.to_datetime((idx.tz_convert("America/New_York") if idx.tz is not None else idx).date)
    return d[~d.index.duplicated(keep="last")].sort_index().astype(float)


def adx(d: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = d["high"], d["low"], d["close"]
    up, dn = h.diff(), -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(pdm, index=d.index).ewm(alpha=1 / n, adjust=False).mean() / atr_
    ndi = 100 * pd.Series(ndm, index=d.index).ewm(alpha=1 / n, adjust=False).mean() / atr_
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def features(d: pd.DataFrame) -> pd.DataFrame:
    from fxbot.strongtrend import votes
    o, h, l, c, v = (d[k] for k in ("open", "high", "low", "close", "volume"))
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    f = pd.DataFrame(index=d.index)
    f["atr"], f["close"] = atr, c
    f["lrvol"] = np.log((v / v.rolling(20).mean()).clip(0.1, 10))
    volp = atr / c
    f["lvolr"] = np.log((volp / volp.rolling(60).mean()).clip(0.2, 5))
    f["trend"] = adx(d) / 25
    s20, s200 = c.rolling(20).mean(), c.rolling(200).mean()
    f["angle"] = ((s20 - s20.shift(5)) - (s200 - s200.shift(5))) / 5 / atr
    f["long_state"] = votes(d)["long_state"]
    f["up_next"] = (h.shift(-1) - o.shift(-1)) / atr          # tomorrow's upside from its open, in today's ATRs
    f["o_n"], f["h_n"], f["l_n"], f["c_n"] = o.shift(-1), h.shift(-1), l.shift(-1), c.shift(-1)
    return f


def _fit(tr: pd.DataFrame, q: float):
    from sklearn.linear_model import QuantileRegressor
    t = tr.dropna(subset=FEATS + ["up_next"])
    if len(t) > 6000:
        t = t.sample(6000, random_state=0)
    return QuantileRegressor(quantile=q, alpha=0.0005, solver="highs").fit(t[FEATS], t["up_next"].clip(0, 6))


def _pinball(y, p, q):
    e = y - p
    return float(np.mean(np.maximum(q * e, (q - 1) * e)))


def _trade(r, tp_mult) -> float:
    entry = r.o_n * (1 + COST)
    stop = r.o_n - r.atr
    if r.l_n <= stop:
        px = stop
    elif tp_mult is not None and r.h_n >= r.o_n + tp_mult * r.atr:
        px = r.o_n + tp_mult * r.atr
    else:
        px = r.c_n
    return (px * (1 - COST) - entry) / r.atr


def report(daily_raw: dict, today: date, state_dir=None) -> list[str]:
    daily = {s: _dates(d) for s, d in daily_raw.items() if len(d) > 300}
    daily = {s: d[d.index < pd.Timestamp(today)] for s, d in daily.items()}
    p = pd.concat([features(d).assign(symbol=s) for s, d in daily.items()]).sort_index()
    trend = p[(p["long_state"] == 1)].dropna(subset=FEATS + ["atr"])
    t0 = pd.Timestamp(today - timedelta(days=183))
    train, test = trend[trend.index < t0], trend[(trend.index >= t0)].dropna(subset=["up_next"])
    m50, m35 = _fit(train, 0.5), _fit(train, 0.35)
    test = test.copy()
    test["p50"], test["p35"] = m50.predict(test[FEATS]).clip(0.1, 4), m35.predict(test[FEATS]).clip(0.05, 4)
    const = {}
    for s, g in trend.groupby("symbol"):
        const[s] = g["up_next"].shift(1).rolling(250, min_periods=60).median()
    test["c50"] = [const[s].get(t, np.nan) for t, s in zip(test.index, test["symbol"])]
    test = test.dropna(subset=["c50"])
    mid = test.index.min() + (test.index.max() - test.index.min()) / 2
    out = [f"## daytp: daily TP estimator for trend stocks (rel volume, volatility, ADX, 20 vs 200 angle); fit before {t0.date()}, "
           f"test {test.index.min().date()}..{test.index.max().date()}, {len(test)} trend-stock days"]
    coef = dict(zip(FEATS, np.round(m50.coef_, 3)))
    out.append(f"- median model: upside/ATR = {m50.intercept_:.3f} + " + " + ".join(f"{v} x {k}" for k, v in coef.items()))
    ok_all = True
    for label, seg in (("whole", test), ("1st half", test[test.index < mid]), ("2nd half", test[test.index >= mid])):
        y = seg["up_next"].clip(0, 6)
        lm, lc = _pinball(y, seg["p50"], 0.5), _pinball(y, seg["c50"], 0.5)
        hit50, hit35, hitc = (y >= seg["p50"]).mean(), (y >= seg["p35"]).mean(), (y >= seg["c50"]).mean()
        tr_m = [_trade(r, r.p50) for r in seg.itertuples()]
        tr_s = [_trade(r, r.p35) for r in seg.itertuples()]
        tr_c = [_trade(r, r.c50) for r in seg.itertuples()]
        tr_n = [_trade(r, None) for r in seg.itertuples()]
        good = lm < lc and sum(tr_m) > sum(tr_c)
        ok_all &= good if label != "whole" else True
        out.append(f"- {label}: pinball model {lm:.4f} vs constant {lc:.4f} | TP hit: model median {hit50:.0%}, safer {hit35:.0%}, constant {hitc:.0%} | "
                   f"trade R/day avg: model TP {np.mean(tr_m):+.3f} ({sum(tr_m):+.0f}R), safer TP {np.mean(tr_s):+.3f} ({sum(tr_s):+.0f}R), "
                   f"constant TP {np.mean(tr_c):+.3f} ({sum(tr_c):+.0f}R), no TP {np.mean(tr_n):+.3f} ({sum(tr_n):+.0f}R)")
    out.append(f"- verdict: {'USABLE (beats the constant in both halves)' if ok_all else 'NOT better than a plain per-stock constant - shown for reference only'}")
    last = p.index.max()
    now = p[(p.index == last) & (p["long_state"] == 1)].dropna(subset=FEATS + ["atr"]).copy()
    if len(now):
        now["p50"], now["p35"] = m50.predict(now[FEATS]).clip(0.1, 4), m35.predict(now[FEATS]).clip(0.05, 4)
        out.append(f"### next-session TP for trend stocks (from the {last.date()} close; TP = open + estimate x ATR, ATR shown)")
        for r in now.sort_values("p50", ascending=False).itertuples():
            out.append(f"  {r.symbol:5s} close {r.close:9.2f} ATR {r.atr:7.2f} | rvol {np.exp(r.lrvol):.2f} vol-ratio {np.exp(r.lvolr):.2f} ADX {25 * r.trend:4.1f} "
                       f"angle {r.angle:+.3f} | TP median open+{r.p50:.2f} ATR (~{r.p50 * r.atr / r.close:.2%}), safer open+{r.p35:.2f} ATR")
        if state_dir is not None:
            from pathlib import Path
            sd = Path(state_dir)
            sd.mkdir(parents=True, exist_ok=True)
            now[["symbol", "close", "atr", "p50", "p35", "lrvol", "lvolr", "trend", "angle"]].to_json(
                sd / f"tp-{last.date():%Y%m%d}.json", orient="records", indent=1)
    return out
