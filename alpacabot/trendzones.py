"""Trend experts' buy / sell / take-profit zones on STOCKS (owner 2026-10-28: "try on 5 large caps, 5 mid caps and 5 small caps").

PRE-DECLARED before any run - the forex rules of fxbot.consensus (trend_tp_zones) moved to daily stock bars:
- Stocks: large NVDA MSFT AAPL GOOGL AMZN; mid AFRM SOFI RKLB HIMS DKNG; small MARA RIOT IONQ SOUN BBAI (fixed before the run).
- Trend experts: the book trend methods that work on any OHLC series (Holy Grail, TTM squeeze, MACD + 200 EMA, Williams volatility breakout, inside-bar
  breakout), on daily bars. Yardstick zd = (close - 20-day average) / ATR14, both from the days before (no look-ahead).
- Zones per size group, from the 12 months before the test: BUY = the experts' average median zd at long signals, SELL = at short signals, LONG TP =
  at the exit of their winning longs (each expert's own backtest), SHORT TP = winning shorts; each +- 0.25.
- Trade: long when a close is in the buy zone (fill next open), take profit when zd reaches the long TP zone, out early if zd falls into the sell zone,
  stop 1.5 ATR, 20 days at most; shorts mirror. 2.5 bps a side (and 5 bps), no financing (cash stocks; short borrow cost ignored - a caveat).
- Tests: the last 21 days (few trades on daily bars - read with care) and the last 92 days for context, both unseen; 3 shuffled-bar seeds as the control."""

from datetime import date, timedelta

import numpy as np
import pandas as pd

GROUPS = {"large": ("NVDA", "MSFT", "AAPL", "GOOGL", "AMZN"), "mid": ("AFRM", "SOFI", "RKLB", "HIMS", "DKNG"),
          "small": ("MARA", "RIOT", "IONQ", "SOUN", "BBAI")}
TREND_METHODS = ("raschke_holy_grail", "carter_ttm_squeeze", "macd_200ema", "williams_vol_breakout", "inside_bar_breakout")
STOP_ATR, MAX_BARS, HALF = 1.5, 20, 0.25


def to_bidask(d: pd.DataFrame, half_bps: float = 2.5) -> pd.DataFrame:
    h = half_bps / 1e4
    out = pd.DataFrame(index=d.index)
    for k in "ohlc":
        col = {"o": "open", "h": "high", "l": "low", "c": "close"}[k]
        out["b" + k], out["a" + k] = d[col] * (1 - h), d[col] * (1 + h)
    out["volume"] = d["volume"] if "volume" in d else 1.0
    return out


def zd_daily(m: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    c = m["close"]
    pc = c.shift(1)
    atr = pd.concat([m["high"] - m["low"], (m["high"] - pc).abs(), (m["low"] - pc).abs()], axis=1).max(axis=1).rolling(14).mean().shift(1)
    sma = c.rolling(20).mean().shift(1)
    return ((c - sma) / atr).to_numpy(float), atr.to_numpy(float)


def expert_points(make, ba: pd.DataFrame, a: date, b: date) -> tuple[list, list, list, list]:
    """(long-signal zd, short-signal zd, winning-long exit zd, winning-short exit zd) for one stock, signals / entries in [a, b]."""
    from fxbot.data import mid
    from fxbot.engine import simulate
    m = mid(ba)
    z, _ = zd_daily(m)
    f = make().frame(m)
    dts = np.asarray(m.index.date)
    sel = (dts >= a) & (dts <= b) & np.isfinite(z)
    lz = list(z[sel & f["long"].fillna(False).to_numpy(bool)])
    sz = list(z[sel & f["short"].fillna(False).to_numpy(bool)])
    tl, ts = [], []
    pos = {t: i for i, t in enumerate(m.index)}
    for t in simulate("x", ba, make()):
        if not (a <= t.entry_time.date() <= b) or t.side * (t.exit_px - t.entry_px) <= 0:
            continue
        k = pos.get(t.exit_time)
        if k is not None and np.isfinite(z[k]):
            (tl if t.side == 1 else ts).append(float(z[k]))
    return lz, sz, tl, ts


def zones(points: dict) -> dict:
    """points: expert -> (lz, sz, tl, ts) pooled over the group's stocks."""
    def avg(idx, min_n=10):
        meds = [float(np.median(v[idx])) for v in points.values() if len(v[idx]) >= min_n]
        return ((np.mean(meds) - HALF, np.mean(meds) + HALF), len(meds)) if meds else (None, 0)
    return {"buy": avg(0), "sell": avg(1), "tp_long": avg(2), "tp_short": avg(3)}


def trade(ba: pd.DataFrame, Z: dict, a: date, b: date, sym: str = "") -> list[dict]:
    from fxbot.data import mid
    bz, sz, tpl, tps = Z["buy"][0], Z["sell"][0], Z["tp_long"][0], Z["tp_short"][0]
    if not all((bz, sz, tpl, tps)):
        return []
    m = mid(ba)
    z, at = zd_daily(m)
    c = m["close"].to_numpy(float)
    bo, bh, bl, ao, ah, al = (ba[k].to_numpy(float) for k in ("bo", "bh", "bl", "ao", "ah", "al"))
    dates = np.asarray(ba.index.date)
    out, i, n = [], 0, len(ba)
    while i < n - 1:
        if not (a <= dates[i] <= b) or not np.isfinite(z[i]) or not np.isfinite(at[i]):
            i += 1
            continue
        side = 1 if bz[0] <= z[i] <= bz[1] else -1 if sz[0] <= z[i] <= sz[1] else 0
        if not side:
            i += 1
            continue
        j = i + 1
        fill = ao[j] if side == 1 else bo[j]
        sd = STOP_ATR * at[i]
        stop = fill - side * sd
        k, px, why = j, None, ""
        while k < n:
            lo, hi, op = (bl[k], bh[k], bo[k]) if side == 1 else (al[k], ah[k], ao[k])
            tp_hit = np.isfinite(z[k]) and ((side == 1 and z[k] >= tpl[0]) or (side == -1 and z[k] <= tps[1]))
            bail = np.isfinite(z[k]) and ((side == 1 and z[k] <= sz[1]) or (side == -1 and z[k] >= bz[0]))
            if k > j and side * (op - stop) <= 0:
                px, why = op, "stop"
            elif (side == 1 and lo <= stop) or (side == -1 and hi >= stop):
                px, why = stop, "stop"
            elif tp_hit or bail or k - j + 1 >= MAX_BARS:
                why = "tp" if tp_hit else "sell zone" if bail else "time"
                if k + 1 < n:
                    k += 1
                    px = bo[k] if side == 1 else ao[k]
                else:
                    px = c[k]
            elif k == n - 1:
                px, why = c[k], "end"
            if px is not None:
                break
            k += 1
        out.append({"date": dates[j], "side": side, "r": side * (px - fill) / sd, "sym": sym, "why": why})
        i = max(k, i + 1)
    return out


def summary(tr: list[dict]) -> str:
    rs = [t["r"] for t in tr]
    if not rs:
        return "0 trades"
    w, lo = [r for r in rs if r > 0], [r for r in rs if r <= 0]
    pf = (sum(w) / -sum(lo)) if lo and sum(lo) < 0 else 99.0
    return f"{len(rs)} trades, win {len(w) / len(rs):.0%}, PF {pf:.2f}, net {sum(rs):+.1f}R, avg {np.mean(rs):+.3f}R"


def report(daily: dict, today: date) -> list[str]:
    from fxbot.fxexp_check import shuffled_bidask
    from fxbot.methods import METHODS
    t3a = today - timedelta(days=92)
    t3w = today - timedelta(days=21)
    ca, cb = t3a - timedelta(days=365), t3a - timedelta(days=1)
    out = [f"## trend_zones_stocks: trend experts' buy / sell / take-profit zones on daily stock bars, calibrated {ca}..{cb}; tests: last 21 days ({t3w}..{today}) "
           f"and last 92 days ({t3a}..{today}), 2.5 bps a side (and 5)"]
    for grp, syms in GROUPS.items():
        bas = {s: to_bidask(daily[s]) for s in syms if s in daily and len(daily[s]) > 300}
        if not bas:
            out.append(f"### {grp}: no data")
            continue
        pts = {}
        for name in TREND_METHODS:
            acc = ([], [], [], [])
            for s, ba in bas.items():
                try:
                    r = expert_points(METHODS[name][0], ba, ca, cb)
                except Exception as exc:
                    print(f"skip {name} {s}: {exc}", flush=True)
                    continue
                for k in range(4):
                    acc[k].extend(r[k])
            pts[name] = acc
        Z = zones(pts)
        fz = lambda k: f"{Z[k][0][0]:+.2f}..{Z[k][0][1]:+.2f} ({Z[k][1]})" if Z[k][0] else "n/a"   # noqa: E731
        out.append(f"### {grp.upper()} caps ({', '.join(bas)}): BUY {fz('buy')} | SELL {fz('sell')} | LONG TP {fz('tp_long')} | SHORT TP {fz('tp_short')}")
        for label, a in (("last 21 days", t3w), ("last 92 days", t3a)):
            for hb in (2.5, 5.0):
                tr = [t for s, d in daily.items() if s in bas for t in trade(to_bidask(d, hb), Z, a, today, s)]
                why = {w: sum(1 for t in tr if t["why"] == w) for w in ("tp", "sell zone", "stop", "time", "end")}
                out.append(f"- {label} | {hb:g} bps a side: all {summary(tr)} | longs {summary([t for t in tr if t['side'] == 1])} | "
                           f"shorts {summary([t for t in tr if t['side'] == -1])} | exits {why}")
            plc = [summary([t for s, ba in bas.items() for t in trade(shuffled_bidask(ba, k), Z, a, today, s)]) for k in range(3)]
            out.append(f"  {label} placebo (shuffled bars, 3 seeds): " + " | ".join(plc))
        tr = [t for s, ba in bas.items() for t in trade(ba, Z, t3a, today, s)]
        out.append("  last 92 days by stock: " + " | ".join(f"{s} {sum(t['r'] for t in tr if t['sym'] == s):+.1f}R ({sum(1 for t in tr if t['sym'] == s)})" for s in bas))
    return out
