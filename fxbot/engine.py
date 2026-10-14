"""FX backtest engine on bid/ask bars.

Rules (same discipline as alpacabot): a signal is decided on a completed bar and filled at the NEXT bar's open, longs buy the ASK and sell
the BID, shorts the reverse, so every round trip pays the real spread of that moment. The stop is checked first on every bar; a gap
through it (weekend, news) fills at the gap open, not at the stop. Financing is not in OANDA candles, so every position held through a
17:00 ET rollover is charged FIN_PER_NIGHT of its notional (conservative flat rate; real swaps can be positive or negative).
Portfolio: fixed fractional risk per trade (stop distance), max open positions, one per instrument, notional capped by leverage.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .data import mid, usd_per_unit

FIN_PER_NIGHT = 0.0001      # 0.01% of notional per night held (~3.65%/yr)


@dataclass
class Trade:
    instrument: str
    side: int
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_px: float
    exit_px: float
    stop_dist: float
    reason: str
    bars: int
    nights: int
    rank: float = 0.0


def _nights(t0: pd.Timestamp, t1: pd.Timestamp) -> int:
    """Number of 17:00 New York rollovers between two times (Wednesday's counts triple, as brokers charge the weekend then)."""
    a, b = t0.tz_convert("America/New_York"), t1.tz_convert("America/New_York")
    n = 0
    d = (a - pd.Timedelta(hours=17)).normalize() + pd.Timedelta(hours=17)
    while True:
        d += pd.Timedelta(days=1)
        if d > b:
            break
        if d.weekday() < 5:
            n += 3 if d.weekday() == 2 else 1
    return n


def simulate(instrument: str, df: pd.DataFrame, strat, spread_mult: float = 1.0, placebo_seed: int | None = None) -> list[Trade]:
    """One position at a time per instrument. strat.frame(mid_df) -> columns long, short (entry signals), exit_long, exit_short,
    stop (distance in price), rank; strat.max_bars."""
    m = mid(df)
    f = strat.frame(m)
    n = len(df)
    bo, bh, bl, ao, ah, al = (df[k].to_numpy(float) for k in ("bo", "bh", "bl", "ao", "ah", "al"))
    if spread_mult != 1.0:                         # stress: widen the spread around the mid
        mo, mh, ml = ((df["b" + k] + df["a" + k]) / 2 for k in "ohl")
        half = {k: (df["a" + k] - df["b" + k]).to_numpy(float) / 2 * spread_mult for k in "ohl"}
        bo, bh, bl = mo.to_numpy() - half["o"], mh.to_numpy() - half["h"], ml.to_numpy() - half["l"]
        ao, ah, al = mo.to_numpy() + half["o"], mh.to_numpy() + half["h"], ml.to_numpy() + half["l"]
    lg, sh = f["long"].to_numpy(bool), f["short"].to_numpy(bool)
    if placebo_seed is not None:                   # same number of entries per side, random bars, same exits and stops
        rng = np.random.default_rng(placebo_seed)
        ok = np.flatnonzero(np.isfinite(f["stop"].to_numpy(float)) & (f["stop"].to_numpy(float) > 0))
        newl, news = np.zeros(n, bool), np.zeros(n, bool)
        for arr, cnt in ((newl, int(lg.sum())), (news, int(sh.sum()))):
            if cnt and len(ok):
                arr[rng.choice(ok, size=min(cnt, len(ok)), replace=False)] = True
        lg, sh = newl, news
    xl, xs, stop_d, rank = (f[k].to_numpy() for k in ("exit_long", "exit_short", "stop", "rank"))
    tgt_d = f["target"].to_numpy(float) if "target" in f else np.full(n, np.nan)    # optional take-profit distance (checked after the stop)
    fam = f["family"].to_numpy() if "family" in f else None          # RegimeSwitch: a range trade exits by the range rule
    xlr, xsr = (f["exit_long_range"].to_numpy(), f["exit_short_range"].to_numpy()) if fam is not None else (xl, xs)
    zones = getattr(strat, "zones", None)
    if zones:
        zh, zl, zc, za = (f[k].to_numpy(float) for k in ("zone_hi", "zone_lo", "zone_close", "zone_atr"))
    out, i = [], 0
    while i < n - 1:
        side = 1 if lg[i] else -1 if sh[i] else 0
        sd = float(stop_d[i])
        if side == 0 or not np.isfinite(sd) or sd <= 0:
            i += 1
            continue
        j0 = i + 1
        ex_l, ex_s = (xlr, xsr) if fam is not None and fam[i] == "range" else (xl, xs)
        fill = ao[j0] if side == 1 else bo[j0]
        stop = fill - side * sd
        tgt = fill + side * tgt_d[i] if np.isfinite(tgt_d[i]) and tgt_d[i] > 0 else None
        if zones:                                  # trade zones decided at entry: entry (no chase), invalidation (= stop), exhaustion
            if side * (fill - zc[i]) > zones["chase_atr"] * za[i]:
                i += 1                              # the fill would be too far past the signal: outside the buy/sell zone
                continue
            zt = zh[i] if side == 1 else zl[i]
            ztgt = zt if np.isfinite(zt) and side * (zt - fill) >= zones["min_r"] * sd else fill + side * zones["default_r"] * sd
            tgt = ztgt if tgt is None else (min(tgt, ztgt) if side == 1 else max(tgt, ztgt))
        lock = getattr(strat, "lock", None)        # (be_at_r, trail_after_r, trail_r): profit lock, applied from the NEXT bar
        best = fill
        k, px, why = j0, None, ""
        while k < n:
            lo, hi, op = (bl[k], bh[k], bo[k]) if side == 1 else (al[k], ah[k], ao[k])
            if k > j0 and side * (op - stop) <= 0:
                px, why = op, "stop"                # gapped through
            elif (side == 1 and lo <= stop) or (side == -1 and hi >= stop):
                px, why = stop, ("stop" if side * (stop - fill) < 0 else "lock")
            elif tgt is not None and k > j0 and side * (op - tgt) >= 0:
                px, why = op, "target"              # gapped through the target: the better open
            elif tgt is not None and ((side == 1 and bh[k] >= tgt) or (side == -1 and al[k] <= tgt)):
                px, why = tgt, "target"
            elif (ex_l[k] if side == 1 else ex_s[k]) or (k - j0 + 1) >= strat.max_bars:
                if k + 1 < n:
                    why = "signal" if (ex_l[k] if side == 1 else ex_s[k]) else "time"
                    k += 1
                    px = bo[k] if side == 1 else ao[k]
                else:
                    px, why = (df["bc"].iloc[k] if side == 1 else df["ac"].iloc[k]), "end"
            elif k == n - 1:
                px, why = (df["bc"].iloc[k] if side == 1 else df["ac"].iloc[k]), "end"
            if px is None and zones and k > j0 and k + 1 < n:      # exhaustion bar: up >= exhaust_r and this bar closes past the last bar's extreme
                best_z = max(bh[j0:k + 1]) if side == 1 else min(al[j0:k + 1])
                rev = df["bc"].iloc[k] < bl[k - 1] if side == 1 else df["ac"].iloc[k] > ah[k - 1]
                if side * (best_z - fill) >= zones["exhaust_r"] * sd and rev:
                    k += 1
                    px, why = (bo[k] if side == 1 else ao[k]), "exhausted"
            if px is not None:
                break
            if lock:                                # update the stop with this bar's best price; it only acts from the next bar on
                best = max(best, bh[k]) if side == 1 else min(best, al[k])
                gain = side * (best - fill)
                be_at, trail_after, trail_r = lock
                if gain >= be_at * sd:
                    stop = max(stop, fill) if side == 1 else min(stop, fill)
                if gain >= trail_after * sd:
                    stop = max(stop, best - trail_r * sd) if side == 1 else min(stop, best + trail_r * sd)
            k += 1
        k = min(k, n - 1)
        out.append(Trade(instrument, side, df.index[j0], df.index[k], float(fill), float(px), sd, why, k - j0 + 1,
                         _nights(df.index[j0], df.index[k]), float(rank[i]) if np.isfinite(rank[i]) else 0.0))
        i = max(k, i + 1)
    return out


@dataclass
class Book:
    equity: float = 100_000.0
    risk_pct: float = 0.005
    max_positions: int = 4
    leverage: float = 20.0          # notional cap per position = equity x leverage / max_positions
    max_per_currency: int = 2       # at most 2 open trades leaning the same way on one currency (e.g. long EUR/USD + short USD/JPY = 2x short USD)


def portfolio(trades: list[Trade], book: Book) -> tuple[pd.DataFrame, pd.Series]:
    """Takes trades in entry order (best rank first on ties), skips one if the book is full or the instrument is held.
    Equity is marked at each closed trade (realized); the daily curve is built from realized P&L by exit day."""
    eq, open_, rows = book.equity, [], []        # eq = realized equity: a trade's P&L counts only once it has closed (no look-ahead)
    for t in sorted(trades, key=lambda t: (t.entry_time, -t.rank)):
        for p in sorted([p for p in open_ if p["t"].exit_time <= t.entry_time], key=lambda p: p["t"].exit_time):
            eq += p["pnl"]
            open_.remove(p)
        if len(open_) >= book.max_positions or any(p["t"].instrument == t.instrument for p in open_):
            continue
        if book.max_per_currency and _currency_load(open_, t) > book.max_per_currency:
            continue                                # currency firewall: too much of one currency on the same side already
        risk_usd = usd_per_unit(t.instrument, t.stop_dist, t.entry_px)
        units = int(min(eq * book.risk_pct / risk_usd, eq * book.leverage / book.max_positions / _usd_notional(t.instrument, t.entry_px)))
        if units < 1:
            continue
        move = t.side * (t.exit_px - t.entry_px)
        pnl = units * usd_per_unit(t.instrument, move, t.exit_px)
        fin = units * _usd_notional(t.instrument, t.entry_px) * FIN_PER_NIGHT * t.nights
        pnl -= fin
        open_.append({"t": t, "pnl": pnl})
        rows.append({"instrument": t.instrument, "side": t.side, "entry": t.entry_time, "exit": t.exit_time, "units": units, "pnl": pnl,
                     "financing": fin, "r": pnl / eq / book.risk_pct, "reason": t.reason, "bars": t.bars})
    fills = pd.DataFrame(rows)
    if not len(fills):
        return fills, pd.Series(dtype=float)
    daily = fills.groupby(fills["exit"].dt.tz_convert("America/New_York").dt.date)["pnl"].sum()
    curve = book.equity + daily.cumsum()
    return fills, curve


def _currency_load(open_: list, t: Trade) -> int:
    """Largest same-direction count on either of t's currencies if t is added (base gets +side, quote gets -side)."""
    load = {}
    for p in open_ + [{"t": t}]:
        base, quote = p["t"].instrument.split("_")
        load[base] = load.get(base, 0) + p["t"].side
        load[quote] = load.get(quote, 0) - p["t"].side
    base, quote = t.instrument.split("_")
    return max(abs(load[base]), abs(load[quote]))


def _usd_notional(instrument: str, price: float) -> float:
    return price if instrument.endswith("_USD") else 1.0       # 1 unit of EUR = price USD; 1 unit of USD = 1 USD


def metrics(fills: pd.DataFrame, curve: pd.Series, equity: float, days: int) -> dict:
    if fills is None or not len(fills):
        return {"trades": 0}
    w, lo = fills[fills.pnl > 0], fills[fills.pnl <= 0]
    total = float(fills.pnl.sum())
    years = max(days / 365.25, 1e-9)
    dd = float((curve / curve.cummax() - 1).min()) if len(curve) else 0.0
    end = equity + total
    return {"trades": int(len(fills)), "win_rate": float((fills.pnl > 0).mean()), "avg_r": float(fills.r.mean()),
            "profit_factor": float(w.pnl.sum() / -lo.pnl.sum()) if len(lo) and lo.pnl.sum() < 0 else float("inf"),
            "total_return": total / equity, "cagr": float((end / equity) ** (1 / years) - 1) if end > 0 else -1.0,
            "max_drawdown": min(dd, 0.0), "avg_daily_pct": total / equity / max(days * 252 / 365.25, 1) * 100,
            "financing_paid": float(fills.financing.sum())}
