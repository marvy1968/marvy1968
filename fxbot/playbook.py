"""Owner 2026-10-25: the "Break & Retest Trading Playbook" (pasted reference guide), built as written and simulated on OANDA bid/ask bars.

Zones (higher timeframe, causal): swing highs / lows on `htf` bars (a pivot = the extreme of 2*wing+1 bars, known `wing` bars later); a swing
high's zone runs from its candle's body top [Z_L] to its wick high [Z_U] (a swing low: wick low [Z_L] to body bottom [Z_U]). A zone qualifies
only when price reacted there more than once (another pivot inside it, +-0.25 HTF ATR, within the lookback).

Setup on the entry timeframe (completed candles only), long (short mirrors):
  1 breakout   a candle BODY closes above Z_U (the previous close was not above it);
  2 retest     within `retest_bars` candles a candle trades back into the zone (low <= Z_U); any close <= Z_U from the breakout until entry
               cancels the setup;
  3 confirm    a qualifying pin bar (lower wick >= 2/3 of the range, close in the top third, upper wick <= 10%, low into the zone, close
               above Z_U) or bullish engulfing (bearish then bullish candle, body engulfs body, both closes above Z_U, the pattern at the
               zone); the retest structure's low (first touch .. confirmation) is frozen;
  entry        buy-stop at the confirmation high + entry_buf (x entry-TF ATR), valid `expiry` candles; skipped if price already trades
               through the trigger on the next candle's open (chase rule) or trades below the frozen retest low before the fill;
  stop         frozen retest low - stop_buf x ATR (the whole structure, never inside it);
  target       the nearest qualifying opposing HTF swing level above the entry; skipped when it is less than min_rr x the risk away;
  manage       exit at market (next open) on a completed close <= Z_U; stop to break-even after +be_at_r R (0 = off); time cap max_bars.
Fills: a long buys at the ask and sells at the bid (shorts mirror), a stop / trigger that gaps is filled at the open."""

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class PlaybookRules:
    htf: str = "4h"
    wing: int = 3
    zone_lookback: int = 120          # HTF bars a zone stays active
    retest_bars: int = 15
    expiry: int = 5
    entry_buf: float = 0.02           # x ATR14 of the entry timeframe (the playbook's "5 ticks")
    stop_buf: float = 0.10            # x ATR14 (the playbook's "10 ticks")
    min_rr: float = 2.0
    be_at_r: float = 1.0
    max_bars: int = 120
    require_two_touches: bool = True


def _atr(h, l, c, n=14):
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().to_numpy()


def htf_zones(m: pd.DataFrame, r: PlaybookRules) -> list[dict]:
    """Swing zones from mid-price bars resampled to r.htf: [{'known': ts, 'kind': 'hi'|'lo', 'zl', 'zu', 'px'}], qualified ones only."""
    g = m.resample(r.htf, label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    o, h, l, c = (g[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a = _atr(h, l, c)
    w = r.wing
    piv = []
    for j in range(w, len(g) - w):
        known = g.index[min(j + w + 1, len(g) - 1)] if j + w + 1 < len(g) else g.index[-1] + pd.Timedelta(r.htf)
        if h[j] == h[j - w:j + w + 1].max():
            piv.append({"i": j, "known": known, "kind": "hi", "zl": max(o[j], c[j]), "zu": h[j], "px": h[j], "atr": a[j]})
        if l[j] == l[j - w:j + w + 1].min():
            piv.append({"i": j, "known": known, "kind": "lo", "zl": l[j], "zu": min(o[j], c[j]), "px": l[j], "atr": a[j]})
    out = []
    for k, p in enumerate(piv):
        if not np.isfinite(p["atr"]):
            continue
        tol = 0.25 * p["atr"]
        twice = any(q is not p and abs(q["i"] - p["i"]) <= r.zone_lookback and q["i"] < p["i"] + 1 and
                    q["px"] >= p["zl"] - tol and q["px"] <= p["zu"] + tol for q in piv[max(0, k - 60):k])
        if twice or not r.require_two_touches:
            p["expires"] = p["known"] + pd.Timedelta(r.htf) * r.zone_lookback
            out.append(p)
    return out


def simulate_playbook(inst: str, df: pd.DataFrame, r: PlaybookRules, a_date, b_date) -> list[dict]:
    """Trades (dicts with r multiple) for one instrument; df = OANDA bid/ask bars on the entry timeframe (bo/bh/bl/bc, ao/ah/al/ac)."""
    mid = pd.DataFrame({k: (df["b" + k[0]] + df["a" + k[0]]) / 2 for k in ("open", "high", "low", "close")}, index=df.index)
    zones = htf_zones(mid, r)
    O, H, L, C = (mid[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    BO, BH, BL, BC = (df[k].to_numpy(float) for k in ("bo", "bh", "bl", "bc"))
    AO, AH, AL, AC = (df[k].to_numpy(float) for k in ("ao", "ah", "al", "ac"))
    A = _atr(H, L, C)
    idx = df.index
    n = len(df)
    trades = []
    for side in (1, -1):
        # mirrored view: a short is a long on the negated chart
        o, h, l, c = (O, H, L, C) if side == 1 else (-O, -L, -H, -C)
        for z in zones:
            if side == 1:
                zl, zu = z["zl"], z["zu"]
            else:
                zl, zu = -z["zu"], -z["zl"]
            s0 = idx.searchsorted(z["known"])
            s1 = min(n, idx.searchsorted(z["expires"]))
            for k in range(max(s0, 1), s1):
                if not (c[k] > zu and c[k - 1] <= zu and min(o[k], c[k]) > zl):   # body closes above the zone (from at / below it)
                    continue
                if not (a_date <= idx[k].date() <= b_date):
                    break
                first, conf = None, None
                for i in range(k + 1, min(n, k + 1 + r.retest_bars)):
                    if c[i] <= zu:
                        break                                        # closed back into / below the zone: invalidated
                    if l[i] <= zu:
                        first = i if first is None else first
                        rng = h[i] - l[i]
                        pin = rng > 0 and (min(o[i], c[i]) - l[i]) >= 2 / 3 * rng and c[i] >= l[i] + 2 / 3 * rng and \
                            (h[i] - max(o[i], c[i])) <= 0.10 * rng
                        eng = i > first and c[i - 1] < o[i - 1] and c[i] > o[i] and o[i] <= c[i - 1] and c[i] >= o[i - 1] and \
                            (o[i] < c[i - 1] or c[i] > o[i - 1]) and c[i - 1] > zu
                        if pin or eng:
                            conf = i
                            break
                if conf is None:
                    break
                rlow = l[first:conf + 1].min()
                atr_c = A[conf] if np.isfinite(A[conf]) else (h[conf] - l[conf])
                trig = h[conf] + r.entry_buf * atr_c
                stop = rlow - r.stop_buf * atr_c
                # nearest qualifying opposing HTF level above the trigger, known by the confirmation candle
                opp = [(-q["px"] if side == -1 else q["px"]) for q in zones
                       if q["kind"] == ("hi" if side == 1 else "lo") and q["known"] <= idx[conf] and q["expires"] > idx[conf]]
                opp = [p for p in opp if p > trig]
                if not opp:
                    break
                tgt = min(opp)
                if tgt - trig < r.min_rr * (trig - stop):
                    break                                            # the premarked structure does not allow 2R: skip
                fill_i = None
                for j in range(conf + 1, min(n, conf + 1 + r.expiry)):
                    if o[j] > trig:
                        break                                        # chase rule: already through the trigger
                    if l[j] < rlow:
                        break                                        # traded below the frozen retest low before the fill
                    if h[j] >= trig:
                        fill_i = j
                        break
                    if c[j] <= zu:
                        break
                if fill_i is None:
                    break
                half = (AO[fill_i] - BO[fill_i]) / 2                  # the entry pays half the spread on top of the mid trigger
                fill = max(trig, o[fill_i]) + half                   # (mirrored: a short sells at the bid, below the mid)
                risk = fill - stop
                if risk <= 0:
                    break
                cur_stop, exit_px, why, j = stop, None, "", fill_i
                be_done = False
                for j in range(fill_i, min(n, fill_i + r.max_bars)):
                    # exit side of the book (bid for longs / ask for shorts), mirrored
                    xo, xh, xl, xc = (BO[j], BH[j], BL[j], BC[j]) if side == 1 else (-AO[j], -AL[j], -AH[j], -AC[j])
                    if j > fill_i and xo <= cur_stop:
                        exit_px, why = xo, "stop"
                    elif xl <= cur_stop:
                        exit_px, why = cur_stop, ("stop" if not be_done else "breakeven")
                    elif j > fill_i and xo >= tgt:
                        exit_px, why = xo, "target"
                    elif xh >= tgt:
                        exit_px, why = tgt, "target"
                    elif c[j] <= zu and j + 1 < n:                   # completed close back into the zone: exit at the next open
                        exit_px, why = (BO[j + 1] if side == 1 else -AO[j + 1]), "invalidated"
                        j += 1
                    if exit_px is not None:
                        break
                    if r.be_at_r and not be_done and xh - fill >= r.be_at_r * risk:
                        cur_stop, be_done = max(cur_stop, fill), True
                if exit_px is None:
                    exit_px, why = ((BC[j] if side == 1 else -AC[j]), "time")
                trades.append({"inst": inst, "side": "long" if side == 1 else "short", "entry": idx[fill_i], "exit": idx[j],
                               "r": (exit_px - fill) / risk, "why": why, "rr_target": (tgt - fill) / risk})
                break
    trades.sort(key=lambda t: t["entry"])                         # one position per pair at a time: an overlapping later setup is dropped
    kept, free = [], None
    for t in trades:
        if free is None or t["entry"] > free:
            kept.append(t)
            free = t["exit"]
    return kept


def playbook_report(data: dict, a, b, rules: PlaybookRules, label: str) -> list[str]:
    rows = [t for inst, df in data.items() for t in simulate_playbook(inst, df, rules, a, b)]
    if not rows:
        return [f"- {label}: 0 trades"]
    d = pd.DataFrame(rows)
    gw, gl = d.loc[d.r > 0, "r"].sum(), -d.loc[d.r <= 0, "r"].sum()
    ex = ", ".join(f"{k} {v}" for k, v in d["why"].value_counts().items())
    return [f"- {label}: {len(d)} trades, win {(d.r > 0).mean():.0%}, avg {d.r.mean():+.2f}R, PF {gw / gl if gl > 0 else (float('inf') if gw > 0 else 0.0):.2f}, "
            f"total {d.r.sum():+.1f}R (= {0.5 * d.r.sum():+.1f}% at 0.5% risk), avg target {d.rr_target.mean():.1f}R | exits: {ex}"]
