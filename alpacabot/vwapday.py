"""VWAP / ORB day-trade research (owner 2026-11-02: "an alpaca style day trading bot ... 62% win rate ... VWAP or what works best").
Implements vwap_daytrade_SPEC.md v1 (copy: /opt/marv-bot/state/vwap_daytrade/SPEC.md). Research only: nothing here places orders.

Choices the spec leaves open, fixed BEFORE any run:
UNIVERSE  No point-in-time S&P 500 constituents / shares-outstanding file exists on the VM. "PIT-approx" = a hand-built pool of 72 names that
          were sector leaders at some point 2018-2026 (incl. INTC, CSCO, T, VZ, PFE, BA, GE, D, SLB, CCI, SPG, DD ...), GICS sector as of the
          month (Communication Services from 2018-09-28: GOOGL/META were XLK, NFLX/DIS/CMCSA XLY before; V/MA moved XLK -> XLF 2023-03-17),
          S&P membership start dates for TSLA / CEG / LIN, ranked at each month-end by 60-day average DOLLAR VOLUME (market-cap proxy: no
          historical share counts) -> top 3 per sector trade the next month. Still survivorship-flavoured (the pool was chosen in 2026);
          names with no Alpaca bars are logged. STATIC = today's top 3 per sector (universe.SECTOR_TOP5), comparison only.
IN PLAY   RVOL30 = 09:30-10:00 volume / mean of the prior 14 sessions' 09:30-10:00 volume >= 1.5, price > $5, 20-day ADV > $50M; top 5.
          Applies to all three strategies. Control (c) = the same rules on SPY + the 11 sector SPDRs, all of them every day (no RVOL filter).
BARS      SIP 1-min RTH bars on a full minute grid (missing minutes = previous close, zero volume); 5-min bars aligned to 09:30. A signal on a
          5-min bar that closes at minute s fills at the open of minute s+1; the marketable limit (signal close +/- 0.05%) does not fill if
          that open is beyond it ("no_fill"). Stop and target in the same 1-min bar: stop first. Gaps through a stop / target fill at the open.
SESSION   entries 10:00-15:45, flat 15:50 (half days, detected from the calendar: entries to 12:45, flat 12:50). FOMC 13:30 rule NOT applied
          (no calendar on the VM) - flagged. Max 4 new trades/day, 3 open, 1 per symbol, 2 open per sector; daily limit -2R realized ->
          everything flat at the next minute and no more entries; weekly -4% -> half risk next week. Overnight holds do not count toward the
          next day's caps.
PRIMARY   ORB (★ range 15/30/60 min) + VWAP; first 5-min close beyond the OR and VWAP with SPY on the same side of its VWAP, signal bar
          closing by 12:00; stop = tighter of the OR side and entry -/+ ★k x ATRd (0.08/0.10/0.15); skip < 0.15% or > 1.5% of price;
          after +1R (intrabar) the stop trails at max(break-even, VWAP - 0.5 sigma), updated each minute; out at the open after a 5-min close
          back through VWAP; else 15:50 / swing.
VARIANT A VWAP reclaim: last 5-min close and SPY's on the trend side of VWAP, then a 1-min low reaches VWAP; within the touch's 5-min bar
          and the next 3, a 5-min close back beyond VWAP and beyond the prior 5-min bar's extreme -> entry; a 1-min close beyond VWAP -/+
          1.5 sigma first cancels. Stop = pullback extreme -/+ 0.05 ATRd, skipped if wider than 1 sigma. 50% off at +1R (stop unchanged),
          rest out at the open after a 5-min close through VWAP, 15:50 / swing.
VARIANT B band reversion, only if |SPY 09:30-10:00| < 0.3%: a 5-min close beyond VWAP -/+ ★k sigma (1.5/2/2.5), the next 5-min bar closes
          back toward VWAP, ADX(14, 5-min, continuous) of the confirming bar < ★20/25 -> entry. Target = VWAP of the previous minute
          (limit), stop = the closer of entry -/+ (VWAP - entry) and the session extreme -/+ 0.05 ATRd, time stop 60 min. No swing.
VARIANT C Velez (README "Velez-Confirmed", owner 2026-11-03, trimmed): 2-minute bars, indicators continuous across days. Long: close > SMA20,
          SMA20 rising (vs 5 bars ago), SMA20 > SMA200, "wide state" (ATR14 above its 20-bar mean and |SMA20 slope| >= 0.1 ATR), not choppy (fewer
          than 4 SMA20 crosses in 20 bars); trigger = pullback (a low within SMA20 + 0.1 ATR in the last 5 bars, then a bullish bar closing above
          SMA20 on more volume than the bar before) OR 2-bar reversal (bearish bar with a lower low, then a bullish bar closing above its high).
          Short = the mirror image, scored separately. CONFIRMATION = only: close on the trend side of VWAP AND the bar's volume > 1.5x its
          20-bar mean (the RSI / breakout / EMA votes, the gap filter and the exhaustion-reversal mode are dropped). No SPY filter. Entry at the next
          minute's open (marketable limit, as the others). Stop = the nearer of the 5-bar pullback extreme and SMA20 -/+ 0.5 ATR(2-min), at least
          max(0.5 ATR, 0.15% of price) away, skipped above 1.5% of price. Exits: half off at +2R and the stop to break-even; the rest at +3R or after
          a 2-minute close through SMA20 (out at the next open); flat 15:50 or the same longs-only swing rule as the others. Risk 0.5%, daily cap -4R
          (= 2% at 0.5% risk, instead of -2R). Other caps (4 new trades a day, 3 open, 2 per sector) are the harness's. No tuned parameters.
SWING     longs of PRIMARY / A / C open at 15:45 with all of: 15:45 close in the top 20% of the day's range and > VWAP + 0.5 sigma; open >= +1R;
          price > EMA20 > SMA50 (daily, today's price as the close); 20-day return > its sector SPDR's; SPY > SMA200, SPY > its VWAP, SPY 20-day
          realized vol < its 1-year median. Earnings / ex-dividend / macro calendar NOT available -> not checked, flagged "unverified".
          Hold min(remaining, 50% of the original, risk from the overnight stop <= the original risk); the rest sold at 15:50.
          Overnight stop = max(VWAP at 15:49, entry); next day: open < prior close - 0.5 ATRd -> out at 09:31; open through the stop -> out at
          the open; to 10:00 the overnight stop, then the day's 30-min OR low; flat 15:50 day 2. Portfolio cap 2 names / 30% of equity.
SIZING    0.5% risk; notional cap 25% of equity scales a trade down (its R is unchanged; equity uses the scaled size). Daily equity compounding.
COSTS     3 bps a side (2 slippage + 1 half spread) on every fill, SEC fee 0.278 bps on sells; 2x run = 6 bps a side. Short borrow ~0 intraday
          (flat by 15:50), "borrow unverified"; SSR day (a print <= 90% of the prior close) -> no short entries that day.
PARAMS    train 2018-01 -> 2023-12, at most 9 combos per strategy, chosen per side by min(PF, mean PF of its one-step neighbours) with 30+
          train trades; holdout 2024-01-01 -> data end, scored once. Rolling 12-month train -> 6-month OOS folds, stitched.
CONTROLS  holdout, frozen params, 20 seeds, trades judged one by one (no portfolio caps, no swing): (a) random symbol of that day's universe,
          random entry minute drawn from the real entry minutes, same side, same stop in ATRd units, same exits; (b) real entry, random side,
          same stop distance, same exits. (c) ETFs as above.
PASS      per strategy x side on PIT: holdout PF > 1.1, 100+ trades, PF > 1.0 at 2x, train PF > 1.0, beats (a) and (b) 16+/20, PF > 1.0 after
          dropping the 3 best symbols. A pass on STATIC alone counts for nothing.
"""

import json
import math
import subprocess
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

NY = "America/New_York"
POOL = {
    "XLK": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "INTC", "CSCO", "CRM", "ADBE"],
    "XLC": ["GOOGL", "META", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS"],
    "XLY": ["AMZN", "TSLA", "HD", "MCD", "NKE", "BKNG", "LOW"],
    "XLP": ["WMT", "PG", "KO", "PEP", "COST", "PM"],
    "XLE": ["XOM", "CVX", "COP", "SLB", "EOG"],
    "XLF": ["BRK.B", "JPM", "BAC", "WFC", "GS", "V", "MA"],
    "XLV": ["UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV"],
    "XLI": ["GE", "HON", "UNP", "BA", "CAT", "RTX", "UPS"],
    "XLU": ["NEE", "DUK", "SO", "D", "CEG"],
    "XLRE": ["AMT", "PLD", "EQIX", "CCI", "SPG", "WELL"],
    "XLB": ["LIN", "APD", "SHW", "ECL", "NEM", "DD"],
}
SECTOR = {s: e for e, ss in POOL.items() for s in ss}
ETFS = ["XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC"]
MEMBER_FROM = {"TSLA": date(2020, 12, 21), "CEG": date(2022, 2, 2), "LIN": date(2018, 10, 31)}
TRAIN = (date(2018, 1, 1), date(2023, 12, 31))
HOLD0 = date(2024, 1, 1)
CB, SEC = 0.0003, 0.0000278
MIN_TRAIN = 30
DAILY_CAP_R = {"velezc": 4.0}          # realized R at which a strategy stops for the day (default 2.0); Variant C: 2% at 0.5% risk
GRIDS = {"orb": [{"or": a, "k": b} for a in (15, 30, 60) for b in (0.08, 0.10, 0.15)],
         "reclaim": [{}],
         "velezc": [{}],
         "band": [{"ks": a, "adx": b} for a in (1.5, 2.0, 2.5) for b in (20, 25)]}
DEFAULT = {"orb": {"or": 30, "k": 0.10}, "reclaim": {}, "band": {"ks": 2.0, "adx": 20}, "velezc": {}}


def sector_at(sym: str, d: date) -> str:
    if d < date(2018, 9, 28):
        if sym in ("GOOGL", "META"):
            return "XLK"
        if sym in ("NFLX", "DIS", "CMCSA"):
            return "XLY"
    if d < date(2023, 3, 17) and sym in ("V", "MA"):
        return "XLK"
    return SECTOR.get(sym, sym)


# ---------------------------------------------------------------- pass 1: per-symbol daily table (past data only)
def _wilder(x: np.ndarray, n: int = 14) -> np.ndarray:
    return pd.Series(x).ewm(alpha=1 / n, adjust=False, min_periods=n).mean().to_numpy()


def _adx(h, l, c, n=14) -> np.ndarray:
    pc = np.r_[np.nan, c[:-1]]
    up, dn = h - np.r_[np.nan, h[:-1]], np.r_[np.nan, l[:-1]] - l
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.nanmax(np.c_[h - l, np.abs(h - pc), np.abs(l - pc)], axis=1)
    atr = _wilder(tr, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        pdi, ndi = 100 * _wilder(pdm, n) / atr, 100 * _wilder(ndm, n) / atr
        dx = 100 * np.abs(pdi - ndi) / (pdi + ndi)
    return _wilder(np.nan_to_num(dx, nan=0.0), n)


def minute_frame(m1: pd.DataFrame) -> pd.DataFrame:
    idx = pd.DatetimeIndex(m1.index)
    idx = idx.tz_convert(NY) if idx.tz is not None else idx.tz_localize(NY)
    mins = idx.hour * 60 + idx.minute - 570
    f = pd.DataFrame({"o": m1["open"].to_numpy(float), "h": m1["high"].to_numpy(float), "l": m1["low"].to_numpy(float),
                      "c": m1["close"].to_numpy(float), "v": m1["volume"].to_numpy(float), "m": mins,
                      "d": idx.tz_localize(None).normalize().date})
    return f[(f["m"] >= 0) & (f["m"] < 390)].reset_index(drop=True)


def daily_table(mf: pd.DataFrame, d1: pd.DataFrame) -> pd.DataFrame:
    """One row per session: RVOL30, ATRd (prior close), swing references, ranking dollar volume."""
    g = mf.groupby("d")
    t = pd.DataFrame({"first30": mf[mf["m"] < 30].groupby("d")["v"].sum(), "last_min": g["m"].max()})
    t["first30"] = t["first30"].fillna(0.0)
    t["rvol30"] = t["first30"] / t["first30"].shift(1).rolling(14, min_periods=10).mean()
    dd = d1.copy()
    di = pd.DatetimeIndex(dd.index)
    dd.index = (di.tz_convert(NY).tz_localize(None) if di.tz is not None else di).normalize().date
    dd = dd[~pd.Index(dd.index).duplicated(keep="last")]
    c, h, l, v = (dd[k].to_numpy(float) for k in ("close", "high", "low", "volume"))
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.c_[h - l, np.abs(h - pc), np.abs(l - pc)], axis=1)
    ref = pd.DataFrame(index=dd.index)
    ref["atrd"] = pd.Series(_wilder(tr), index=dd.index).shift(1)
    ref["prev_close"] = pd.Series(c, index=dd.index).shift(1)
    cs = pd.Series(c, index=dd.index)
    ref["ema20_prev"] = cs.ewm(span=20, adjust=False).mean().shift(1)
    ref["sum49_prev"] = cs.rolling(49).sum().shift(1)
    ref["close_lag20"] = cs.shift(20)
    ref["sma200_prev"] = cs.rolling(200).mean().shift(1)
    dv = cs * pd.Series(v, index=dd.index)
    ref["adv20"] = dv.rolling(20).mean().shift(1)
    ref["dvol60"] = dv.rolling(60).mean()                       # through this day (used at month-end for NEXT month's universe)
    lr = np.log(cs).diff()
    rv = lr.rolling(20).std() * math.sqrt(252)
    ref["rv20_prev"] = rv.shift(1)
    ref["rv20_med_prev"] = rv.rolling(252, min_periods=120).median().shift(1)
    return t.join(ref, how="left")


def adx5_by_day(mf: pd.DataFrame) -> dict:
    k = mf["m"].to_numpy() // 5
    g = mf.assign(k=k).groupby(["d", "k"], sort=True)
    b = pd.DataFrame({"h": g["h"].max(), "l": g["l"].min(), "c": g["c"].last()})
    a = _adx(b["h"].to_numpy(), b["l"].to_numpy(), b["c"].to_numpy())
    out = {}
    for (d, kk), val in zip(b.index, a):
        out.setdefault(d, {})[int(kk)] = val
    res = {}
    for d, m in out.items():
        arr = np.full(78, np.nan, dtype=np.float32)
        for kk, val in m.items():
            if kk < 78:
                arr[kk] = val
        res[d] = arr
    return res


# ---------------------------------------------------------------- pass 2: symbol-day arrays
def symbol_days(mf: pd.DataFrame, dates: set, sess_len: dict) -> dict:
    out = {}
    if not len(mf):
        return out
    dcol = mf["d"].to_numpy()
    order = np.argsort(dcol, kind="stable")
    dsorted = dcol[order]
    for d in dates:
        n = sess_len.get(d)
        if not n:
            continue
        lo, hi = np.searchsorted(dsorted, d, "left"), np.searchsorted(dsorted, d, "right")
        if hi <= lo:
            continue
        rows = mf.iloc[order[lo:hi]]
        m = rows["m"].to_numpy()
        keep = m < n
        m = m[keep]
        if not len(m):
            continue
        arr = {k: np.full(n, np.nan) for k in ("o", "h", "l", "c")}
        v = np.zeros(n)
        for k in arr:
            arr[k][m] = rows[k].to_numpy()[keep]
        v[m] = rows["v"].to_numpy()[keep]
        c = pd.Series(arr["c"]).ffill().bfill().to_numpy()
        for k in ("o", "h", "l"):
            x = arr[k]
            x[np.isnan(x)] = c[np.isnan(x)]
        out[d] = {"o": arr["o"].astype(np.float32), "h": arr["h"].astype(np.float32), "l": arr["l"].astype(np.float32),
                  "c": c.astype(np.float32), "v": v.astype(np.float32), "n": n}
    return out


def vwap_sigma(sd: dict):
    if "_vw" in sd:
        return sd["_vw"]
    h, l, c, v = (sd[k].astype(np.float64) for k in ("h", "l", "c", "v"))
    cv = np.cumsum(v)
    pv = np.cumsum((h + l + c) / 3 * v)
    vw = np.where(cv > 0, pv / np.where(cv > 0, cv, 1), c)
    dev = c - vw
    k = np.arange(1, len(c) + 1)
    m1, m2 = np.cumsum(dev) / k, np.cumsum(dev * dev) / k
    sig = np.sqrt(np.maximum(m2 - m1 * m1, 0.0))
    sig = np.maximum(sig, 1e-6 * c)
    return vw, sig


# ---------------------------------------------------------------- signals (closed bars only; entry at the next minute's open)
def _limits(n):
    return (195, 200) if n <= 210 else (375, 380)          # last entry minute, flat minute


def _ssr(sd, dd, s):
    pc = dd.get("prev_close")
    return pc is not None and np.isfinite(pc) and float(np.min(sd["l"][:s + 1])) <= 0.9 * pc


def _ioc(side, entry, ref):
    return side * (entry - ref) <= 0.0005 * ref


def sig_orb(sd, spy, dd, side, p):
    o, h, l, c = (sd[k] for k in ("o", "h", "l", "c"))
    n = sd["n"]
    last, _ = _limits(n)
    orm = int(p["or"])
    vw, _ = vwap_sigma(sd)
    svw, _ = vwap_sigma(spy)
    sc = spy["c"]
    orh, orl = float(h[:orm].max()), float(l[:orm].min())
    atrd = dd.get("atrd")
    skips = []
    if not (atrd and np.isfinite(atrd)):
        return None, [("no_atr", None)]
    av = len(c)
    for k5 in range(orm // 5, n // 5):
        s = 5 * k5 + 4
        j = s + 1
        if j >= av:
            break
        if j < 30:
            continue
        if s > 149 or j > last:
            break
        cs = float(c[s])
        ok = (cs > orh and cs > vw[s] and sc[s] > svw[s]) if side == 1 else (cs < orl and cs < vw[s] and sc[s] < svw[s])
        if not ok:
            continue
        if side == -1 and _ssr(sd, dd, s):
            return None, skips + [("ssr", s)]
        e = float(o[j])
        if not _ioc(side, e, cs):
            skips.append(("no_fill", s))
            continue
        stop = max(orl, e - p["k"] * atrd) if side == 1 else min(orh, e + p["k"] * atrd)
        dist = side * (e - stop) / e
        if dist < 0.0015:
            skips.append(("risk_too_small", s))
            continue
        if dist > 0.015:
            skips.append(("risk_too_large", s))
            continue
        return {"j": j, "s": s, "entry": e, "stop": float(stop), "or_hi": orh, "or_lo": orl, "vwap": float(vw[s])}, skips
    return None, skips


def sig_reclaim(sd, spy, dd, side, p):
    o, h, l, c = (sd[k] for k in ("o", "h", "l", "c"))
    n = sd["n"]
    last, _ = _limits(n)
    vw, sg = vwap_sigma(sd)
    svw, _ = vwap_sigma(spy)
    sc = spy["c"]
    atrd = dd.get("atrd")
    if not (atrd and np.isfinite(atrd)):
        return None, [("no_atr", None)]
    skips, touch, tk, pb = [], None, None, None
    for i in range(30, min(n, last, len(c) - 1)):
        if touch is None:
            kp = i // 5 - 1
            if kp < 0:
                continue
            sp = 5 * kp + 4
            if side * (c[sp] - vw[sp]) > 0 and side * (sc[sp] - svw[sp]) > 0 and \
                    ((l[i] <= vw[i - 1]) if side == 1 else (h[i] >= vw[i - 1])):
                touch, tk, pb = i, i // 5, float(l[i] if side == 1 else h[i])
            continue
        pb = min(pb, float(l[i])) if side == 1 else max(pb, float(h[i]))
        if side * (c[i] - (vw[i] - side * 1.5 * sg[i])) < 0:
            touch = None
            continue
        if (i + 1) % 5 == 0:
            k = i // 5
            if k > tk + 3:
                touch = None
                continue
            if k < 1:
                continue
            ph = float(h[5 * (k - 1):5 * k].max()) if side == 1 else float(l[5 * (k - 1):5 * k].min())
            ok = side * (c[i] - vw[i]) > 0 and side * (c[i] - ph) > 0 and side * (sc[i] - svw[i]) > 0
            if not ok:
                continue
            j = i + 1
            if j > last:
                break
            if side == -1 and _ssr(sd, dd, i):
                return None, skips + [("ssr", i)]
            e = float(o[j])
            if not _ioc(side, e, float(c[i])):
                skips.append(("no_fill", i))
                touch = None
                continue
            stop = pb - side * 0.05 * atrd
            risk = side * (e - stop)
            if risk <= 0:
                skips.append(("risk_too_small", i))
                touch = None
                continue
            if risk > sg[i]:
                skips.append(("risk_too_large", i))
                touch = None
                continue
            return {"j": j, "s": i, "entry": e, "stop": float(stop), "vwap": float(vw[i]), "sigma": float(sg[i])}, skips
    return None, skips


def band_day_ok(spy) -> bool:
    return abs(float(spy["c"][29]) / float(spy["o"][0]) - 1) < 0.003


def sig_band(sd, spy, dd, side, p):
    o, h, l, c = (sd[k] for k in ("o", "h", "l", "c"))
    n = sd["n"]
    last, _ = _limits(n)
    if not band_day_ok(spy):
        return None, [("regime_off", None)]
    vw, sg = vwap_sigma(sd)
    atrd = dd.get("atrd")
    if not (atrd and np.isfinite(atrd)):
        return None, [("no_atr", None)]
    adx = sd.get("adx5")
    skips = []
    for k5 in range(0, n // 5 - 1):
        s, s2 = 5 * k5 + 4, 5 * k5 + 9
        j = s2 + 1
        if j >= len(c):
            break
        if j < 30:
            continue
        if j > last:
            break
        setup = (c[s] < vw[s] - p["ks"] * sg[s]) if side == 1 else (c[s] > vw[s] + p["ks"] * sg[s])
        if not setup or not (side * (c[s2] - c[s]) > 0):
            continue
        a = float(adx[k5 + 1]) if adx is not None and k5 + 1 < len(adx) else float("nan")
        if not np.isfinite(a) or a >= p["adx"]:
            skips.append(("adx", s2))
            continue
        if side == -1 and _ssr(sd, dd, s2):
            return None, skips + [("ssr", s2)]
        e = float(o[j])
        if not _ioc(side, e, float(c[s2])):
            skips.append(("no_fill", s2))
            continue
        tgt = float(vw[s2])
        if side * (tgt - e) <= 0:
            continue
        ext = float(l[:j].min()) if side == 1 else float(h[:j].max())
        sa, sb = e - side * abs(tgt - e), ext - side * 0.05 * atrd
        stop = max(sa, sb) if side == 1 else min(sa, sb)
        if side * (e - stop) <= 0:
            skips.append(("risk_too_small", s2))
            continue
        return {"j": j, "s": s2, "entry": e, "stop": float(stop), "vwap": tgt, "sigma": float(sg[s2])}, skips
    return None, skips


def velezc_by_day(mf: pd.DataFrame, days) -> dict:
    """Variant C signals on 2-minute bars (continuous history, past bars only) -> {date: dict of arrays indexed by 2-min bar k} for `days`."""
    if not len(mf) or not days:
        return {}
    k2 = mf["m"].to_numpy() // 2
    g = mf.assign(k=k2).groupby(["d", "k"], sort=True)
    b = pd.DataFrame({"o": g["o"].first(), "h": g["h"].max(), "l": g["l"].min(), "c": g["c"].last(), "v": g["v"].sum()})
    typ = (mf["h"] + mf["l"] + mf["c"]) / 3
    pv = (typ * mf["v"]).groupby(mf["d"]).cumsum()
    cv = mf["v"].groupby(mf["d"]).cumsum()
    vw1 = (pv / cv.replace(0, np.nan)).groupby([mf["d"], k2]).last()
    vw = vw1.reindex(b.index).groupby(level=0).ffill().to_numpy()
    c, o, h, l, v = b["c"], b["o"], b["h"], b["l"], b["v"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    sma20, sma200 = c.rolling(20).mean(), c.rolling(200).mean()
    slope = sma20 - sma20.shift(5)
    vavg = v.rolling(20).mean().shift(1)
    side_sma = np.sign(c - sma20)
    crosses = (side_sma != side_sma.shift(1)).astype(float).rolling(20).sum()
    wide = (atr > atr.rolling(20).mean()) & (slope.abs() >= 0.1 * atr) & (crosses < 4)
    low5, high5 = l.rolling(5).min(), h.rolling(5).max()
    volup = v > v.shift(1)
    vconf = v > 1.5 * vavg
    vwa = pd.Series(vw, index=b.index)
    pull_l = (low5 <= sma20 + 0.1 * atr) & (c > o) & (c > sma20) & volup
    two_l = (c.shift(1) < o.shift(1)) & (l.shift(1) < l.shift(2)) & (c > h.shift(1)) & (c > o)
    pull_s = (high5 >= sma20 - 0.1 * atr) & (c < o) & (c < sma20) & volup
    two_s = (c.shift(1) > o.shift(1)) & (h.shift(1) > h.shift(2)) & (c < l.shift(1)) & (c < o)
    long_ = (c > sma20) & (slope > 0) & (sma20 > sma200) & wide & (pull_l | two_l) & (c > vwa) & vconf
    short_ = (c < sma20) & (slope < 0) & (sma20 < sma200) & wide & (pull_s | two_s) & (c < vwa) & vconf
    raw_l, raw_s = np.maximum(low5, sma20 - 0.5 * atr), np.minimum(high5, sma20 + 0.5 * atr)
    day_idx = b.index.get_level_values("d")
    kk = b.index.get_level_values("k").to_numpy()
    arrs = {"l": long_.fillna(False).to_numpy(bool), "s": short_.fillna(False).to_numpy(bool), "raw_l": raw_l.to_numpy(np.float32),
            "raw_s": raw_s.to_numpy(np.float32), "atr": atr.to_numpy(np.float32), "sma20": sma20.to_numpy(np.float32)}
    out = {}
    want = set(days)
    pos = {}
    for i, d in enumerate(day_idx):
        if d in want:
            pos.setdefault(d, []).append(i)
    for d, idx in pos.items():
        idx = np.asarray(idx)
        n2 = 195
        rec = {}
        for name, a in arrs.items():
            full = np.zeros(n2, dtype=bool) if name in ("l", "s") else np.full(n2, np.nan, dtype=np.float32)
            keep = kk[idx] < n2
            full[kk[idx][keep]] = a[idx][keep]
            rec[name] = full
        out[d] = rec
    return out


def sig_velezc(sd, spy, dd, side, p):
    vc = sd.get("vc")
    if vc is None:
        return None, [("no_data", None)]
    n = sd["n"]
    last, _ = _limits(n)
    flags, raw = (vc["l"], vc["raw_l"]) if side == 1 else (vc["s"], vc["raw_s"])
    atr = vc["atr"]
    c, o = sd["c"], sd["o"]
    skips = []
    for k in np.flatnonzero(flags):
        s = 2 * int(k) + 1
        j = s + 1
        if j >= len(c):
            break
        if j < 30:
            continue
        if j > last:
            break
        if side == -1 and _ssr(sd, dd, s):
            return None, skips + [("ssr", s)]
        e = float(o[j])
        if not _ioc(side, e, float(c[s])):
            skips.append(("no_fill", s))
            continue
        a = float(atr[k])
        r = float(raw[k])
        if not np.isfinite(a) or a <= 0:
            continue
        dist = max(side * (e - r) if np.isfinite(r) else 0.0, 0.5 * a, 0.0015 * e)
        if dist > 0.015 * e:
            skips.append(("risk_too_large", s))
            continue
        vwm, _ = vwap_sigma(sd)
        return {"j": j, "s": s, "entry": e, "stop": float(e - side * dist), "vwap": float(vwm[s])}, skips
    return None, skips


SIGNALS = {"orb": sig_orb, "reclaim": sig_reclaim, "band": sig_band, "velezc": sig_velezc}


# ---------------------------------------------------------------- one trade, minute by minute
def r_of(events, e, R0, side, mult):
    cb = CB * mult
    num = -e * (cb + (SEC if side == -1 else 0.0))
    for _m, f, px, _why in events:
        num += f * (side * (px - e) - px * (cb + (SEC if side == 1 else 0.0)))
    return num / R0


def walk(strat, sd, side, j, e, stop, start_stop=None):
    """Day-1 walk. Returns (events [(minute, frac, px, reason)], mfe_R, mae_R, state at 15:45 for the swing check or None)."""
    o, h, l, c = (sd[k] for k in ("o", "h", "l", "c"))
    n = sd["n"]
    _, flat = _limits(n)
    vw, sg = vwap_sigma(sd)
    R0 = side * (e - stop)
    ev, rem, best, worst, trail, why = [], 1.0, e, e, False, "stop"
    tp_done = False
    end = flat if strat != "band" else min(flat, j + 60)
    st45 = None
    for i in range(j, end):
        oi, hi, li = float(o[i]), float(h[i]), float(l[i])
        if side * (oi - stop) <= 0:
            ev.append((i, rem, oi, why))
            return ev, side * (best - e) / R0, side * (worst - e) / R0, None
        if (li <= stop) if side == 1 else (hi >= stop):
            ev.append((i, rem, stop, why))
            worst = min(worst, li) if side == 1 else max(worst, hi)
            return ev, side * (best - e) / R0, side * (worst - e) / R0, None
        if strat == "band":
            tgt = float(vw[i - 1]) if i > 0 else float(vw[0])
            if side * (oi - tgt) >= 0:
                ev.append((i, rem, oi, "target"))
                return ev, side * (best - e) / R0, side * (worst - e) / R0, None
            if (hi >= tgt) if side == 1 else (li <= tgt):
                ev.append((i, rem, tgt, "target"))
                return ev, side * (tgt - e) / R0, side * (worst - e) / R0, None
        if strat == "velezc":
            if not tp_done:
                t1 = e + side * 2 * R0
                if (hi >= t1) if side == 1 else (li <= t1):
                    ev.append((i, 0.5, oi if side * (oi - t1) >= 0 else t1, "tp1"))
                    rem, tp_done = 0.5, True
                    stop, why = e, "be"                            # break-even for the rest from the next bar
                    t2 = e + side * 3 * R0
                    if (hi >= t2) if side == 1 else (li <= t2):
                        ev.append((i, rem, oi if side * (oi - t2) >= 0 else t2, "t2"))
                        return ev, side * ((max(best, hi) if side == 1 else min(best, li)) - e) / R0, side * (worst - e) / R0, None
            else:
                t2 = e + side * 3 * R0
                if (hi >= t2) if side == 1 else (li <= t2):
                    ev.append((i, rem, oi if side * (oi - t2) >= 0 else t2, "t2"))
                    return ev, side * ((max(best, hi) if side == 1 else min(best, li)) - e) / R0, side * (worst - e) / R0, None
                vcd = sd.get("vc")
                if vcd is not None and (i + 1) % 2 == 0 and i + 1 < end:
                    sm = float(vcd["sma20"][i // 2])
                    if np.isfinite(sm) and side * (float(c[i]) - sm) < 0:
                        ev.append((i + 1, rem, float(o[i + 1]), "trail"))
                        return ev, side * (best - e) / R0, side * (worst - e) / R0, None
        if strat == "reclaim" and not tp_done:
            tp = e + side * R0
            if (hi >= tp) if side == 1 else (li <= tp):
                ev.append((i, 0.5, oi if side * (oi - tp) >= 0 else tp, "tp1"))
                rem, tp_done = 0.5, True
        best = max(best, hi) if side == 1 else min(best, li)
        worst = min(worst, li) if side == 1 else max(worst, hi)
        if strat == "orb":
            if not trail and side * (best - e) >= R0:
                trail = True
            if trail:
                new = max(e, float(vw[i]) - 0.5 * float(sg[i])) if side == 1 else min(e, float(vw[i]) + 0.5 * float(sg[i]))
                if side * (new - stop) > 0:
                    stop, why = new, "trail"
        if strat in ("orb", "reclaim") and (i + 1) % 5 == 0 and side * (float(c[i]) - float(vw[i])) < 0 and i + 1 < end:
            ev.append((i + 1, rem, float(o[i + 1]), "vwap"))
            return ev, side * (best - e) / R0, side * (worst - e) / R0, None
        if i == flat - 6:                                    # end of the 15:44 bar = 15:45
            st45 = {"px": float(c[i]), "hod": float(h[:i + 1].max()), "lod": float(l[:i + 1].min()), "vwap": float(vw[i]),
                    "sigma": float(sg[i]), "rem": rem, "stop": stop}
    reason = "time" if (strat == "band" and end < flat) else "eod_1550"
    ev.append((end, rem, float(o[end]) if end < n else float(c[n - 1]), reason))
    return ev, side * (best - e) / R0, side * (worst - e) / R0, st45


def swing_check(sym, d, sd, spy, side, e, R0, st45, dd, spy_dd, etf_dd) -> dict:
    """All §6 criteria with their values; ok only if every checkable one passes."""
    out = {"symbol": sym, "date": str(d), "earnings": "unverified", "macro": "unverified", "exdiv": "unverified"}
    if side != 1 or st45 is None:
        out.update(ok=False, why="not a long open at 15:45")
        return out
    p = st45["px"]
    rng = st45["hod"] - st45["lod"]
    c1 = p >= st45["hod"] - 0.2 * rng and p > st45["vwap"] + 0.5 * st45["sigma"]
    ema = dd.get("ema20_prev")
    ema_t = ema + (2 / 21) * (p - ema) if ema is not None and np.isfinite(ema) else np.nan
    s49 = dd.get("sum49_prev")
    sma50 = (s49 + p) / 50 if s49 is not None and np.isfinite(s49) else np.nan
    lag = dd.get("close_lag20")
    r20 = p / lag - 1 if lag else np.nan
    sec = etf_dd or {}
    er20 = sec.get("ret20_today", np.nan)
    c2 = side * (p - e) >= R0 and np.isfinite(sma50) and p > ema_t > sma50 and (not np.isfinite(er20) or r20 > er20)
    svw, _ = vwap_sigma(spy)
    k = min(374, len(spy["c"]) - 1)
    sp = float(spy["c"][k])
    c3 = bool(spy_dd.get("sma200_prev") and sp > spy_dd["sma200_prev"] and sp > float(svw[k])
              and spy_dd.get("rv20_prev") is not None and np.isfinite(spy_dd.get("rv20_med_prev", np.nan))
              and spy_dd["rv20_prev"] < spy_dd["rv20_med_prev"])
    out.update(px=round(p, 4), range_top=c1, ema20=round(float(ema_t), 4) if np.isfinite(ema_t) else None,
               sma50=round(float(sma50), 4) if np.isfinite(sma50) else None, r20=r20, sector_r20=er20, trend=bool(c2), regime=bool(c3),
               ok=bool(c1 and c2 and c3))
    return out


def walk_day2(sd2, ostop, prev_close, atrd):
    o, h, l = sd2["o"], sd2["h"], sd2["l"]
    n = sd2["n"]
    _, flat = _limits(n)
    if float(o[0]) < prev_close - 0.5 * atrd:
        return [(1001, float(o[1]), "gap_stop")]
    if float(o[0]) <= ostop:
        return [(1000, float(o[0]), "gap_stop")]
    orl = float(l[:30].min())
    for i in range(0, flat):
        stp = ostop if i < 30 else orl
        if float(o[i]) <= stp:
            return [(1000 + i, float(o[i]), "stop_day2")]
        if float(l[i]) <= stp:
            return [(1000 + i, stp, "stop_day2")]
    return [(1000 + flat, float(o[flat]), "eod_day2")]


# ---------------------------------------------------------------- context
class Ctx:
    def __init__(self):
        self.days = []            # trading days (date), sorted
        self.sess = {}            # date -> session length in minutes
        self.daily = {}           # sym -> DataFrame (daily_table)
        self.sd = {}              # (sym, date) -> arrays
        self.adx = {}             # sym -> {date: array}
        self.univ = {"pit": {}, "static": {}}     # universe name -> {month (y, m) -> [syms]}
        self.inplay = {"pit": {}, "static": {}, "etf": {}}  # name -> {date -> [syms]}
        self.etf_ret20 = {}       # (etf, date) -> 20-day return using today's 15:45 price is approximated by the prior close
        self.log = print

    def dd(self, sym, d) -> dict:
        t = self.daily.get(sym)
        if t is None or d not in t.index:
            return {}
        return t.loc[d].to_dict()


def build_universes(ctx: Ctx, static_list: list):
    months = sorted({(d.year, d.month) for d in ctx.days})
    rows = []
    for (y, m) in months:
        first = date(y, m, 1)
        prev_days = [d for d in ctx.days if d < first]
        if not prev_days:
            continue
        me = prev_days[-1]
        by_sec = {}
        for sym, t in ctx.daily.items():
            if sym not in SECTOR or (sym in MEMBER_FROM and me < MEMBER_FROM[sym]):
                continue
            if me not in t.index or not np.isfinite(t.at[me, "dvol60"]):
                continue
            by_sec.setdefault(sector_at(sym, me), []).append((float(t.at[me, "dvol60"]), sym))
        names = []
        for sec in ETFS:
            top = sorted(by_sec.get(sec, []), reverse=True)[:3]
            for r, (dv, s) in enumerate(top):
                names.append(s)
                rows.append({"month": f"{y}-{m:02d}", "sector": sec, "rank": r + 1, "symbol": s, "dvol60": round(dv), "data_ok": True})
            if len(top) < 3:
                rows.append({"month": f"{y}-{m:02d}", "sector": sec, "rank": len(top) + 1, "symbol": "", "dvol60": 0, "data_ok": False})
        ctx.univ["pit"][(y, m)] = names
        ctx.univ["static"][(y, m)] = [s for s in static_list if s in ctx.daily]
    return rows


def build_inplay(ctx: Ctx):
    for name in ("pit", "static"):
        for d in ctx.days:
            names = ctx.univ[name].get((d.year, d.month), [])
            cand = []
            for s in names:
                t = ctx.daily.get(s)
                if t is None or d not in t.index:
                    continue
                r = t.at[d, "rvol30"]
                pc, adv = t.at[d, "prev_close"], t.at[d, "adv20"]
                if np.isfinite(r) and r >= 1.5 and np.isfinite(pc) and pc > 5 and np.isfinite(adv) and adv > 50e6:
                    cand.append((float(r), s))
            ctx.inplay[name][d] = [s for _, s in sorted(cand, reverse=True)[:5]]
    for d in ctx.days:
        if d >= HOLD0:
            ctx.inplay["etf"][d] = [e for e in ["SPY"] + ETFS if e in ctx.daily]


# ---------------------------------------------------------------- portfolio (one strategy, one side)
def run(ctx: Ctx, strat: str, side: int, p: dict, mult: float = 1.0, universe: str = "pit", period=None, swing=True,
        logs: dict | None = None):
    trades, signals, eq_rows, swings = [], [], [], []
    equity, risk_pct = 100_000.0, 0.005
    week, week_start_eq, next_week_half = None, equity, False
    pending_swing = []          # swing holds closing today (already in trades; nothing to do)
    days = [d for d in ctx.days if (period is None or period[0] <= d <= period[1])]
    sig_fn = SIGNALS[strat]
    for di, d in enumerate(days):
        wk = d.isocalendar()[:2]
        if wk != week:
            if week is not None:
                next_week_half = equity <= week_start_eq * 0.96
            week, week_start_eq = wk, equity
            risk_pct = 0.0025 if next_week_half else 0.005
        spy = ctx.sd.get(("SPY", d))
        if spy is None:
            continue
        cands = []
        for rank, sym in enumerate(ctx.inplay[universe].get(d, [])):
            sd = ctx.sd.get((sym, d))
            if sd is None:
                if logs is not None:
                    signals.append({"date": str(d), "symbol": sym, "strategy": strat, "side": side, "taken": False, "skip_reason": "no_data"})
                continue
            dd = ctx.dd(sym, d)
            sig, skips = sig_fn(sd, spy, dd, side, p)
            if logs is not None:
                for why, s in skips:
                    signals.append({"date": str(d), "symbol": sym, "strategy": strat, "side": side, "minute": s, "taken": False, "skip_reason": why})
            if sig:
                cands.append((sig["j"], rank, sym, sig, sd, dd))
        cands.sort(key=lambda x: (x[0], x[1]))
        open_, realized, n_new, stopped, day_R, traded = [], 0.0, 0, None, 0.0, set()
        day_trades = []

        def settle(upto):
            nonlocal realized, stopped
            for t in sorted([t for t in open_ if t["exit_min"] < upto], key=lambda t: t["exit_min"]):
                if t not in open_:
                    continue
                open_.remove(t)
                if stopped is None:
                    realized += t["w"] * t["R"]
                    if realized <= -DAILY_CAP_R.get(strat, 2.0):   # daily limit: everything still open goes out at the next minute
                        stopped = t["exit_min"] + 1
                        for u in list(open_):
                            if u["exit_min"] >= stopped:
                                flatten(u, stopped)
                        open_.clear()
                        return

        def flatten(u, m):
            sd_ = u["_sd"]
            ev = [x for x in u["_ev"] if x[0] < m]
            rem = 1.0 - sum(x[1] for x in ev)
            if m < sd_["n"] and rem > 0:
                ev.append((m, rem, float(sd_["o"][m]), "daily_limit"))
            u["_ev"], u["exit_min"], u["exit_reason"], u["swing"] = ev, m, "daily_limit", False
            u["R"] = r_of(ev, u["entry"], u["risk"], side, mult)

        for j, rank, sym, sig, sd, dd in cands:
            settle(j)
            base = {"date": str(d), "symbol": sym, "strategy": strat, "side": side, "minute": sig["s"], "rvol30": dd.get("rvol30")}
            reason = None
            if stopped is not None:
                reason = "daily_limit"
            elif n_new >= 4:
                reason = "max_trades"
            elif len(open_) >= 3:
                reason = "max_open"
            elif sum(1 for t in open_ if t["sector"] == sector_at(sym, d)) >= 2:
                reason = "sector_cap"
            elif sym in traded:
                reason = "one_per_symbol"
            ev, mfe, mae, st45 = walk(strat, sd, side, j, sig["entry"], sig["stop"])
            R0 = side * (sig["entry"] - sig["stop"])
            if reason:
                if logs is not None:
                    signals.append({**base, "taken": False, "skip_reason": reason, "would_be_R": round(r_of(ev, sig["entry"], R0, side, mult), 4)})
                continue
            notional = risk_pct * sig["entry"] / R0
            w = min(1.0, 0.25 / notional) if notional > 0 else 0.0
            t = {"date": d, "symbol": sym, "sector": sector_at(sym, d), "strategy": strat, "side": side, "entry_min": j, "signal_min": sig["s"],
                 "entry": sig["entry"], "stop": sig["stop"], "risk": R0, "w": w, "risk_pct": risk_pct, "mfe_R": mfe, "mae_R": mae,
                 "atrd": dd.get("atrd"), "rvol30": dd.get("rvol30"), "_sd": sd, "_ev": ev, "swing": False, "cost_mult": mult,
                 "split": "holdout" if d >= HOLD0 else "train", "universe": universe}
            # swing (longs, PRIMARY / A only)
            if swing and side == 1 and strat in ("orb", "reclaim", "velezc") and st45 is not None and ev and ev[-1][3] == "eod_1550" and di + 1 < len(days):
                chk = swing_check(sym, d, sd, spy, side, sig["entry"], R0, st45, dd, ctx.dd("SPY", d),
                                  {"ret20_today": ctx.etf_ret20.get((sector_at(sym, d), d), np.nan)})
                nd = days[di + 1]
                sd2 = ctx.sd.get((sym, nd))
                ostop = max(float(vwap_sigma(sd)[0][min(len(sd["c"]) - 1, _limits(sd["n"])[1] - 1)]), sig["entry"])
                p45 = st45["px"]
                held = min(st45["rem"], 0.5, R0 / (p45 - ostop)) if p45 > ostop else 0.0
                over_frac = held * notional * w
                n_over = sum(1 for x in trades if x.get("swing") and x["date"] == d)
                chk.update(held_frac=round(held, 3), overnight_stop=round(ostop, 4), next_day_data=sd2 is not None)
                go = chk["ok"] and sd2 is not None and held > 0 and n_over < 2 and \
                    over_frac + sum(x.get("over_frac", 0) for x in trades if x.get("swing") and x["date"] == d) <= 0.30
                chk["decision"] = "hold" if go else "flat"
                if go:
                    flat_ev = ev[:-1]
                    m, rem_, px_, _ = ev[-1]
                    sell = rem_ - held
                    if sell > 1e-9:
                        flat_ev.append((m, sell, px_, "eod_1550"))
                    ndd = ctx.dd(sym, nd)
                    for mm, pxx, why in walk_day2(sd2, ostop, float(sd["c"][-1]), ndd.get("atrd") or dd.get("atrd")):
                        flat_ev.append((mm, held, pxx, why))
                    t.update(swing=True, over_frac=over_frac, flat_R=r_of(ev, sig["entry"], R0, side, mult))
                    ev = flat_ev
                    chk["outcome_R"] = r_of(ev, sig["entry"], R0, side, mult)
                    chk["flat_R"] = t["flat_R"]
                swings.append(chk)
            t["_ev"] = ev
            t["R"] = r_of(ev, sig["entry"], R0, side, mult)
            t["exit_min"] = 999 if t["swing"] else ev[-1][0]
            t["exit_reason"] = ev[-1][3]
            open_.append(t)
            trades.append(t)
            day_trades.append(t)
            traded.add(sym)
            n_new += 1
            if logs is not None:
                signals.append({**base, "taken": True, "skip_reason": "", "would_be_R": round(t["R"], 4)})
        settle(10 ** 6)
        day_pnl = sum(t["w"] * t["risk_pct"] * t["R"] for t in day_trades)
        day_R = sum(t["w"] * t["R"] for t in day_trades)
        start = equity
        equity *= 1 + day_pnl
        eq_rows.append({"date": str(d), "start_equity": round(start, 2), "end_equity": round(equity, 2), "n_trades": len(day_trades),
                        "n_wins": sum(t["R"] > 0 for t in day_trades), "day_R": round(day_R, 4), "daily_limit_hit": stopped is not None,
                        "overnight_positions": sum(t["swing"] for t in day_trades)})
    for t in trades:
        ev = t["_ev"]
        t["exit_px"] = ev[-1][2]
        t["exit_min_last"] = ev[-1][0]
    return trades, signals, eq_rows, swings


# ---------------------------------------------------------------- statistics
def pf(rs):
    x = np.asarray(list(rs), float)
    lo = -x[x < 0].sum()
    return float(x[x > 0].sum() / lo) if lo > 0 else (99.0 if len(x) and x.sum() > 0 else 0.0)


def stats(trades) -> dict:
    x = np.asarray([t["R"] for t in trades], float)
    if not len(x):
        return {"n": 0, "win": 0.0, "pf": 0.0, "exp": 0.0, "avg_win": 0.0, "avg_loss": 0.0, "net": 0.0}
    return {"n": len(x), "win": float(np.mean(x > 0)), "pf": pf(x), "exp": float(x.mean()), "avg_win": float(x[x > 0].mean()) if (x > 0).any() else 0.0,
            "avg_loss": float(x[x <= 0].mean()) if (x <= 0).any() else 0.0, "net": float(x.sum())}


def window(trades, a, b):
    return [t for t in trades if a <= t["date"] <= b]


def eq_stats(eq_rows, a=None, b=None):
    rows = [r for r in eq_rows if (a is None or r["date"] >= str(a)) and (b is None or r["date"] <= str(b))]
    if not rows:
        return {"ret": 0.0, "dd": 0.0, "sharpe": 0.0}
    e = np.array([r["end_equity"] for r in rows]) / rows[0]["start_equity"]
    dr = np.diff(np.r_[1.0, e]) / np.r_[1.0, e][:-1]
    peak = np.maximum.accumulate(e)
    return {"ret": float(100 * (e[-1] - 1)), "dd": float(100 * np.max(1 - e / peak)),
            "sharpe": float(dr.mean() / dr.std() * math.sqrt(252)) if dr.std() > 0 else 0.0}


def pick(runs: dict, grid: list, a, b, key) -> dict | None:
    """Best combo by min(PF, mean PF of one-step neighbours) with 30+ trades in [a, b]."""
    vals = {}
    for p in grid:
        tr = window(runs[key(p)], a, b)
        vals[key(p)] = (pf(t["R"] for t in tr), len(tr), p)
    best, bs = None, -1
    keys = list(vals)
    for k, (v, n, p) in vals.items():
        if n < MIN_TRAIN:
            continue
        nb = [vals[k2][0] for k2 in keys if k2 != k and sum(1 for x, y in zip(k2, k) if x != y) == 1]
        score = min(v, float(np.mean(nb))) if nb else v
        if score > bs:
            best, bs = p, score
    return best


def controls(ctx: Ctx, strat: str, trades: list, seeds: int = 20):
    rnd, shf = [], []
    mins = [t["entry_min"] for t in trades]
    for seed in range(seeds):
        rng = np.random.default_rng(seed)
        r1, r2 = [], []
        for t in trades:
            d, side = t["date"], t["side"]
            ratio = t["risk"] / t["atrd"] if t.get("atrd") else None
            names = [s for s in ctx.univ["pit"].get((d.year, d.month), []) if (s, d) in ctx.sd]
            if names and ratio:
                s2 = names[int(rng.integers(len(names)))]
                sd2 = ctx.sd[(s2, d)]
                j = int(mins[int(rng.integers(len(mins)))])
                last, _ = _limits(sd2["n"])
                j = min(max(j, 30), last)
                atr2 = ctx.dd(s2, d).get("atrd")
                if atr2 and np.isfinite(atr2):
                    e = float(sd2["o"][j])
                    st = e - side * ratio * atr2
                    ev, *_ = walk(strat, sd2, side, j, e, st)
                    r1.append(r_of(ev, e, side * (e - st), side, 1.0))
            side2 = int(rng.choice([1, -1]))
            sd = t["_sd"]
            e = t["entry"]
            st = e - side2 * t["risk"]
            ev, *_ = walk(strat, sd, side2, t["entry_min"], e, st)
            r2.append(r_of(ev, e, t["risk"], side2, 1.0))
        rnd.append(pf(r1))
        shf.append(pf(r2))
    return rnd, shf


def gao_diagnostic(ctx: Ctx) -> dict:
    rs = []
    for d in ctx.days:
        sd = ctx.sd.get(("SPY", d))
        if sd is None or sd["n"] < 390:
            continue
        if float(sd["c"][359]) / float(sd["o"][0]) - 1 > 0:
            e, x = float(sd["o"][360]), float(sd["o"][380])
            rs.append((x * (1 - CB - SEC) - e * (1 + CB)) / e * 1e4)
    x = np.asarray(rs)
    if not len(x):
        return {"n": 0}
    return {"n": len(x), "mean_bps": float(x.mean()), "win": float(np.mean(x > 0)), "t": float(x.mean() / (x.std() / math.sqrt(len(x)))) if x.std() > 0 else 0.0,
            "sum_bps": float(x.sum())}


# ---------------------------------------------------------------- entry point
def git_hash() -> str:
    try:
        return subprocess.run(["git", "-C", str(Path(__file__).resolve().parents[1]), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except Exception:
        return ""


def _csv(rows, path, drop=("_sd", "_ev")):
    if not rows:
        path.write_text("empty\n")
        return
    pd.DataFrame([{k: v for k, v in r.items() if k not in drop} for r in rows]).to_csv(path, index=False)


def download(load, start: date, end: date, log=print):
    from .universe import STOCKS
    names = sorted(set(SECTOR) | set(STOCKS) | set(ETFS) | {"SPY"})
    for i, s in enumerate(names):
        try:
            load(s, "1Day", start - timedelta(days=500), end)
            m = load(s, "1Min", start, end)
            log(f"[{i + 1}/{len(names)}] {s}: {len(m)} minute bars")
        except Exception as exc:
            log(f"[{i + 1}/{len(names)}] {s}: FAILED {exc!r}")


def main(load, today: date, out_root: Path, log=print, seeds: int = 20, start: date = date(2018, 1, 1)) -> list[str]:
    from .universe import SECTOR_TOP5
    run_id = today.strftime("%Y%m%d") + "-v1"
    out = out_root / run_id
    out.mkdir(parents=True, exist_ok=True)
    end = today - timedelta(days=1)
    static = [s for ss in SECTOR_TOP5.values() for s in ss[:3]]
    names = sorted(set(SECTOR) | set(static))
    ctx = Ctx()
    ctx.log = log
    # SPY first: calendar and session lengths
    spy_mf = minute_frame(load("SPY", "1Min", start, end))
    spy_d1 = load("SPY", "1Day", start - timedelta(days=500), end)
    ctx.daily["SPY"] = daily_table(spy_mf, spy_d1)
    last_min = spy_mf.groupby("d")["m"].max()
    ctx.days = sorted(d for d in last_min.index if d >= start)
    ctx.sess = {d: (210 if last_min[d] < 230 else 390) for d in ctx.days}
    ctx.sd.update({("SPY", d): v for d, v in symbol_days(spy_mf, set(ctx.days), ctx.sess).items()})
    del spy_mf
    for e in ETFS:
        try:
            d1 = load(e, "1Day", start - timedelta(days=500), end)
            dd = d1.copy()
            di = pd.DatetimeIndex(dd.index)
            dd.index = (di.tz_convert(NY).tz_localize(None) if di.tz is not None else di).normalize().date
            r20 = dd["close"] / dd["close"].shift(20) - 1
            for d, v in r20.shift(1).items():             # the prior close's 20-day return (no 15:45 ETF price in the daily table)
                ctx.etf_ret20[(e, d)] = float(v) if np.isfinite(v) else np.nan
        except Exception as exc:
            log(f"etf {e}: {exc!r}")
    mfs_needed = {}
    for i, s in enumerate(names):
        try:
            mf = minute_frame(load(s, "1Min", start, end))
            d1 = load(s, "1Day", start - timedelta(days=500), end)
            if not len(mf) or not len(d1):
                log(f"{s}: no data")
                continue
            ctx.daily[s] = daily_table(mf, d1)
            ctx.adx[s] = adx5_by_day(mf)
            log(f"pass1 [{i + 1}/{len(names)}] {s}: {len(ctx.daily[s])} days")
        except Exception as exc:
            log(f"pass1 {s}: FAILED {exc!r}")
    uni_rows = build_universes(ctx, static)
    _csv(uni_rows, out / "universe_log.csv")
    build_inplay(ctx)
    need = {}
    di = {d: k for k, d in enumerate(ctx.days)}
    for name in ("pit", "static"):
        for d, syms in ctx.inplay[name].items():
            for s in syms:
                need.setdefault(s, set()).add(d)
                if di[d] + 1 < len(ctx.days):
                    need[s].add(ctx.days[di[d] + 1])
    for d in ctx.days:                                 # controls: every universe name on holdout days
        if d >= HOLD0:
            for s in ctx.univ["pit"].get((d.year, d.month), []):
                need.setdefault(s, set()).add(d)
    for i, (s, ds) in enumerate(sorted(need.items())):
        mf = minute_frame(load(s, "1Min", start, end))
        got = symbol_days(mf, ds, ctx.sess)
        vcd = velezc_by_day(mf, ds)
        for d, v in got.items():
            a = ctx.adx.get(s, {}).get(d)
            if a is not None:
                v["adx5"] = a
            v["vc"] = vcd.get(d)
            ctx.sd[(s, d)] = v
        log(f"pass2 [{i + 1}/{len(need)}] {s}: {len(got)}/{len(ds)} days")
    for e in ETFS:                                     # control (c): ETFs on holdout days
        try:
            mf = minute_frame(load(e, "1Min", start, end))
            d1 = load(e, "1Day", start - timedelta(days=500), end)
            ctx.daily[e] = daily_table(mf, d1)
            hold_days = {d for d in ctx.days if d >= HOLD0}
            vcd = velezc_by_day(mf, hold_days)
            for d, v in symbol_days(mf, hold_days, ctx.sess).items():
                v["adx5"] = None
                v["vc"] = vcd.get(d)
                ctx.sd[(e, d)] = v
        except Exception as exc:
            log(f"etf minute {e}: {exc!r}")
    ctx.adx.clear()

    lines = [f"## VWAP / ORB day-trade spec v1 - run {run_id}, data {ctx.days[0]}..{ctx.days[-1]}, train {TRAIN[0]}..{TRAIN[1]}, "
             f"holdout {HOLD0}..{ctx.days[-1]}",
             "Universe PIT-approx (pool of past + present sector leaders, monthly top 3 by 60-day dollar volume; see alpacabot/vwapday.py). "
             "Earnings / FOMC / ex-dividend calendars not available: swing earnings check and FOMC 13:30 rule NOT applied; short borrow unverified.",
             "", "| Strategy | Side | Params | Train PF (n) | Holdout PF | n | Win% | Avg win R | Avg loss R | Exp R | Net R | PF @2x | Random /20 | "
             "Shuffled /20 | Drop top-3 PF | Max sym share | Ret% | MaxDD% | Sharpe | 2025 PF | Last 6m PF | WF OOS PF | STATIC holdout PF | "
             "ETF ctrl PF | PASS |", "|" + "---|" * 25]
    end_d = ctx.days[-1]
    summary = []
    cfg = {"run_id": run_id, "git": git_hash(), "data": [str(ctx.days[0]), str(end_d)], "train": [str(TRAIN[0]), str(TRAIN[1])],
           "holdout": [str(HOLD0), str(end_d)], "costs": {"bps_side": CB * 1e4, "sec_bps_sells": SEC * 1e4, "stress": 2},
           "grids": GRIDS, "sizing": {"risk_pct": 0.005, "notional_cap": 0.25}, "chosen": {}}
    all_tr, all_sig, all_sw = [], [], []
    for strat in ("orb", "velezc", "reclaim", "band"):
        for side in (1, -1):
            sname = "long" if side == 1 else "short"
            key = lambda p: tuple(sorted(p.items()))
            runs = {}
            for p in GRIDS[strat]:
                tr, *_ = run(ctx, strat, side, p)
                runs[key(p)] = tr
                log(f"  {strat} {sname} {p}: {len(tr)} trades, train PF {pf(t['R'] for t in window(tr, *TRAIN)):.2f}")
            p = pick(runs, GRIDS[strat], TRAIN[0], TRAIN[1], key)
            if p is None:
                lines.append(f"| {strat} | {sname} | - | fewer than 30 train trades for every combo |" + " |" * 21)
                continue
            cfg["chosen"][f"{strat}_{sname}"] = p
            sig_log = {}
            tr, sigs, eq, sw = run(ctx, strat, side, p, logs=sig_log)
            tr2, *_ = run(ctx, strat, side, p, mult=2.0)
            hold, train = window(tr, HOLD0, end_d), window(tr, *TRAIN)
            s_h, s_t = stats(hold), stats(train)
            h2 = window(tr2, HOLD0, end_d)
            rnd, shf = controls(ctx, strat, hold, seeds)
            beat_r, beat_s = sum(s_h["pf"] > x for x in rnd), sum(s_h["pf"] > x for x in shf)
            by_sym = {}
            for t in hold:
                by_sym[t["symbol"]] = by_sym.get(t["symbol"], 0.0) + t["R"]
            top3 = [s for s, _ in sorted(by_sym.items(), key=lambda x: -x[1])[:3]]
            drop = pf(t["R"] for t in hold if t["symbol"] not in top3)
            pos = sum(v for v in by_sym.values() if v > 0)
            share = max(by_sym.values()) / s_h["net"] if s_h["net"] > 0 and by_sym else float("nan")
            es = eq_stats(eq, HOLD0, end_d)
            y25 = window(tr, date(2025, 1, 1), date(2025, 12, 31))
            l6 = window(tr, end_d - timedelta(days=183), end_d)
            wf, f0 = [], date(2019, 1, 1)
            while f0 <= end_d:
                f1 = (pd.Timestamp(f0) + pd.DateOffset(months=6)).date() - timedelta(days=1)
                p2 = pick(runs, GRIDS[strat], (pd.Timestamp(f0) - pd.DateOffset(months=12)).date(), f0 - timedelta(days=1), key)
                if p2 is not None:
                    wf += window(runs[key(p2)], f0, f1)
                f0 = f1 + timedelta(days=1)
            st_tr, *_ = run(ctx, strat, side, p, universe="static", period=(HOLD0, end_d))
            etf_tr, *_ = run(ctx, strat, side, p, universe="etf", period=(HOLD0, end_d), swing=False)
            ok = (s_h["pf"] > 1.1 and s_h["n"] >= 100 and pf(t["R"] for t in h2) > 1.0 and s_t["pf"] > 1.0 and beat_r >= 16 and beat_s >= 16
                  and drop > 1.0)
            row = {"strategy": strat, "side": sname, "params": p, "train_pf": s_t["pf"], "train_n": s_t["n"], "hold": s_h, "pf2": pf(t["R"] for t in h2),
                   "random": beat_r, "shuffled": beat_s, "rand_med": float(np.median(rnd)) if rnd else 0, "shuf_med": float(np.median(shf)) if shf else 0,
                   "drop3": drop, "share": share, "eq": es, "pf25": pf(t["R"] for t in y25), "pf6": pf(t["R"] for t in l6), "wf": pf(t["R"] for t in wf),
                   "wf_n": len(wf), "static": stats(st_tr), "etf": stats(etf_tr), "pass": bool(ok)}
            summary.append(row)
            lines.append(f"| {strat} | {sname} | {p} | {s_t['pf']:.2f} ({s_t['n']}) | {s_h['pf']:.2f} | {s_h['n']} | {s_h['win']:.0%} | "
                         f"{s_h['avg_win']:+.2f} | {s_h['avg_loss']:+.2f} | {s_h['exp']:+.3f} | {s_h['net']:+.1f} | {row['pf2']:.2f} | {beat_r}/{seeds} | "
                         f"{beat_s}/{seeds} | {drop:.2f} | {share:.0%} | {es['ret']:+.2f} | {es['dd']:.2f} | {es['sharpe']:.2f} | {row['pf25']:.2f} | "
                         f"{row['pf6']:.2f} | {row['wf']:.2f} ({len(wf)}) | {row['static']['pf']:.2f} ({row['static']['n']}) | "
                         f"{row['etf']['pf']:.2f} ({row['etf']['n']}) | {'PASS' if ok else 'FAIL'} |")
            # breakdowns
            yr = {y: stats([t for t in tr if t["date"].year == y]) for y in range(ctx.days[0].year, end_d.year + 1)}
            sec = {}
            for t in hold:
                sec.setdefault(t["sector"], []).append(t)
            hrs = {}
            for t in hold:
                hrs.setdefault(10 + (t["entry_min"] + 30) // 60 - 1, []).append(t)
            why = pd.Series([t["exit_reason"] for t in hold]).value_counts().to_dict() if hold else {}
            swh = [t for t in hold if t["swing"]]
            lines.append(f"- {strat} {sname}: per year " + ", ".join(f"{y} {v['pf']:.2f} ({v['n']})" for y, v in yr.items()))
            lines.append(f"  holdout by sector: " + ", ".join(f"{k} {pf(t['R'] for t in v):.2f} ({len(v)}, {sum(t['R'] for t in v):+.1f}R)"
                                                                for k, v in sorted(sec.items())))
            lines.append(f"  by entry hour: " + ", ".join(f"{h}:00 {pf(t['R'] for t in v):.2f} ({len(v)})" for h, v in sorted(hrs.items())))
            lines.append(f"  exits: {why} | swing trades {len(swh)}: R {sum(t['R'] for t in swh):+.2f} vs flat at 15:50 "
                         f"{sum(t.get('flat_R', t['R']) for t in swh):+.2f} | random median PF {row['rand_med']:.2f}, shuffled {row['shuf_med']:.2f}")
            for t in tr:
                t.update(run_id=run_id, variant=f"{strat}_{sname}", split="holdout" if t["date"] >= HOLD0 else "train")
            all_tr += tr
            all_sig += sigs
            all_sw += sw
            _csv(eq, out / f"equity_daily_{strat}_{sname}.csv")
            pd.DataFrame([{"seed": k, "strategy": f"{strat}_{sname}", "random_pf": rnd[k], "shuffled_pf": shf[k],
                           "beat_random": s_h["pf"] > rnd[k], "beat_shuffled": s_h["pf"] > shf[k]} for k in range(len(rnd))]).to_csv(
                out / f"controls_{strat}_{sname}.csv", index=False)
    g = gao_diagnostic(ctx)
    lines.append(f"- Gao et al. diagnostic (SPY long 15:30-15:50 when 09:30-15:30 is up, 3 bps a side): {g}")
    lines.append(f"- verdict: {sum(r['pass'] for r in summary)} of {len(summary)} strategy x side rows PASS")
    lines.append("- ORB primary vs Variant C (Velez), pass/fail vs the §8 bar: " + "; ".join(
        f"{r['strategy']} {r['side']}: {'PASS' if r['pass'] else 'FAIL'} (holdout PF {r['hold']['pf']:.2f}, n {r['hold']['n']}, "
        f"2x PF {r['pf2']:.2f}, random {r['random']}/{seeds}, shuffled {r['shuffled']}/{seeds})" for r in summary if r["strategy"] in ("orb", "velezc")))
    _csv(all_tr, out / "trades.csv")
    _csv(all_sig, out / "signals.csv")
    _csv(all_sw, out / "swing_decisions.csv")
    cfg["summary"] = [{k: v for k, v in r.items()} for r in summary]
    (out / "config.json").write_text(json.dumps(cfg, indent=1, default=str))
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    return lines


# ---------------------------------------------------------------- quick sample (owner 2026-11-02: "do a 2 week backtest")
def quick(load, today: date, out_root: Path, sessions: int = 10, log=print) -> list[str]:
    """The last `sessions` completed sessions with the spec's DEFAULT parameters (nothing tuned on these days), PIT-approx universe,
    in-play filter, all three strategies x both sides, 1x and 2x costs, no swing (the live paper sleeve is flat by 15:50)."""
    end = today - timedelta(days=1)
    start = end - timedelta(days=120)                    # warm-up: 14 sessions of RVOL history, 60-day dollar volume
    ctx = Ctx()
    spy_mf = minute_frame(load("SPY", "1Min", start, end))
    ctx.daily["SPY"] = daily_table(spy_mf, load("SPY", "1Day", start - timedelta(days=500), end))
    last_min = spy_mf.groupby("d")["m"].max()
    ctx.days = sorted(last_min.index)
    ctx.sess = {d: (210 if last_min[d] < 230 else 390) for d in ctx.days}
    test_days = ctx.days[-sessions:]
    ctx.sd.update({("SPY", d): v for d, v in symbol_days(spy_mf, set(test_days), ctx.sess).items()})
    mfs = {}
    for s in sorted(SECTOR):
        try:
            mf = minute_frame(load(s, "1Min", start, end))
            d1 = load(s, "1Day", start - timedelta(days=500), end)
            if len(mf) and len(d1):
                ctx.daily[s] = daily_table(mf, d1)
                ctx.adx[s] = adx5_by_day(mf)
                mfs[s] = mf
        except Exception as exc:
            log(f"{s}: {exc!r}")
    build_universes(ctx, [])
    build_inplay(ctx)
    for s, mf in mfs.items():
        ds = {d for d in test_days if s in ctx.inplay["pit"].get(d, [])}
        vcd_q = velezc_by_day(mf, ds)
        for d, v in symbol_days(mf, ds, ctx.sess).items():
            v["adx5"] = ctx.adx[s].get(d)
            v["vc"] = vcd_q.get(d)
            ctx.sd[(s, d)] = v
    period = (test_days[0], test_days[-1])
    out = out_root / f"quick-{today:%Y%m%d}"
    out.mkdir(parents=True, exist_ok=True)
    lines = [f"## VWAP / ORB day-trade - 2-week sample {period[0]}..{period[1]} ({len(test_days)} sessions), spec DEFAULT params, PIT-approx "
             "universe + in-play filter, flat by 15:50. A SAMPLE, NOT EVIDENCE: too few trades to separate skill from luck.",
             "| Strategy | Side | Params | Trades | Win% | PF | Avg win R | Avg loss R | Exp R | Net R | PF @2x | Net R @2x | exits |",
             "|" + "---|" * 13]
    allt = []
    for strat in ("orb", "velezc", "reclaim", "band"):
        for side in (1, -1):
            p = DEFAULT[strat]
            tr, sig, *_ = run(ctx, strat, side, p, period=period, swing=False, logs={})
            tr2, *_ = run(ctx, strat, side, p, mult=2.0, period=period, swing=False)
            st, st2 = stats(tr), stats(tr2)
            why = pd.Series([t["exit_reason"] for t in tr]).value_counts().to_dict() if tr else {}
            lines.append(f"| {strat} | {'long' if side == 1 else 'short'} | {p} | {st['n']} | {st['win']:.0%} | {st['pf']:.2f} | {st['avg_win']:+.2f} | "
                         f"{st['avg_loss']:+.2f} | {st['exp']:+.3f} | {st['net']:+.1f} | {st2['pf']:.2f} | {st2['net']:+.1f} | {why} |")
            for t in tr:
                t["variant"] = f"{strat}_{'long' if side == 1 else 'short'}"
            allt += tr
    a = stats(allt)
    lines.append(f"- all strategies together: {a['n']} trades, win {a['win']:.0%}, PF {a['pf']:.2f}, net {a['net']:+.1f}R")
    lines.append("- in-play names per day: " + ", ".join(f"{d}: {'/'.join(ctx.inplay['pit'].get(d, [])) or '-'}" for d in test_days))
    _csv(allt, out / "trades.csv")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    return lines
