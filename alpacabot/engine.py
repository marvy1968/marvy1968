"""Backtest engine: next-bar fills, stops/targets on bar highs/lows (stop wins ties), slippage and fees, then a portfolio layer
that sizes by risk, caps concurrent positions and applies a daily loss limit (and optionally the pattern-day-trader rule).

Intraday strategies close at the end of the same session. Swing strategies decide at the close and trade the next open.
"""

from dataclasses import dataclass, field, replace
from datetime import date

import numpy as np
import pandas as pd

from .exits import ExitState
from .strategies import Signal


@dataclass
class Costs:
    slip_bps: float = 2.0       # adverse slippage per side, in basis points of price (liquid ETFs/large caps; stress it)
    fee_bps_sell: float = 0.4   # SEC + FINRA TAF on the sell leg, roughly
    commission: float = 0.0     # per share (Alpaca stocks are commission-free)
    entry: str = "market"       # "market": fill at the next open + slippage. "limit": a limit at the signal close (minus limit_offset x the
    limit_offset: float = 0.0   #   stop distance) that waits limit_wait bars; it fills only if price trades THROUGH it (low < limit), at the
    limit_wait: int = 3         #   limit or a better open, with no slippage. Unfilled signals are skipped. Exits stay market orders.


@dataclass
class Trade:
    symbol: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_px: float
    exit_px: float
    stop_dist: float
    reason: str
    bars: int
    tag: str = ""
    side: int = 1          # +1 long, -1 short (entry_px / exit_px are always real, positive prices)
    rank: float = 0.0      # priority when several signals compete for the same slot on one day (higher first)

    def pnl_per_share(self, costs: Costs) -> float:
        sell_leg = self.exit_px if self.side == 1 else self.entry_px
        fee = sell_leg * costs.fee_bps_sell / 1e4 + 2 * costs.commission
        return self.side * (self.exit_px - self.entry_px) - fee


# ---------------------------------------------------------------- intraday
def simulate_day(symbol: str, df: pd.DataFrame, sigs: list[Signal], costs: Costs, max_trades: int = 3, cooldown: int = 0) -> list[Trade]:
    """Shorts are simulated as longs on the mirrored series (open -> -open, high -> -low, low -> -high, close -> -close): every level, stop,
    trail and tie rule is then the same code as for longs, and slippage is always applied against the trader (abs(price) * slip)."""
    long_arr = tuple(df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    short_arr = (-long_arr[0], -long_arr[2], -long_arr[1], -long_arr[3])
    n, slip, trades, busy = len(df), costs.slip_bps / 1e4, [], -1
    buy = lambda px: px + abs(px) * slip      # noqa: E731  (entry in the trade's own direction)
    sell = lambda px: px - abs(px) * slip     # noqa: E731  (exit)
    for s in sorted(sigs, key=lambda s: s.i):
        if len(trades) >= max_trades:
            break
        j0 = s.i + 1
        if j0 >= n or s.i < busy + (cooldown if busy >= 0 else 0) or s.stop_dist <= 0:
            continue
        side = -1 if s.side == -1 else 1
        o, h, l, c = long_arr if side == 1 else short_arr
        xcol = (s.exit_col if side == 1 else "m_" + s.exit_col) if s.exit_col else ""
        real = lambda px: side * px           # noqa: E731
        if costs.entry == "limit":
            lim = c[s.i] - costs.limit_offset * s.stop_dist
            j0 = next((j for j in range(s.i + 1, min(n, s.i + 1 + costs.limit_wait)) if l[j] < lim), None)
            if j0 is None:
                continue                                  # never traded through the limit: no trade (that is the price of no slippage)
            fill = min(lim, o[j0])
        else:
            fill = buy(o[j0])
        stop = fill - s.stop_dist
        tgt = fill + s.target_r * s.stop_dist if s.target_r > 0 else np.inf
        if s.managed:
            st = ExitState.open(fill, s.atr, s.adx, s.trail, s.tp_scale)
            for j in range(j0, n):
                hit = st.check(o[j], h[j], l[j])
                if hit:
                    px, why = sell(hit[1]), hit[0]          # virtual exit = market close after the cross: adverse slippage
                elif (s.max_bars and j - j0 + 1 >= s.max_bars) or j == n - 1:
                    px, why = sell(c[j]), "time" if j < n - 1 else "eod"
                else:
                    st.ratchet(h[j])
                    continue
                trades.append(Trade(symbol, df.index[j0], df.index[j], real(fill), real(px), s.stop_dist, why, j - j0 + 1, s.tag, side))
                busy = j
                break
            continue
        for j in range(j0, n):
            if l[j] <= stop:
                px, why = sell(min(stop, o[j])), "stop"
            elif h[j] >= tgt:
                px, why = max(tgt, o[j]), "target"
            elif (s.max_bars and j - j0 + 1 >= s.max_bars) or j == n - 1:
                px, why = sell(c[j]), "time" if j < n - 1 else "eod"
            elif xcol and bool(df[xcol].iloc[j]) and j + 1 < n:      # the expert says "exit": decided at this close, filled at the next open
                j, px, why = j + 1, sell(o[j + 1]), "signal"
            else:
                continue
            trades.append(Trade(symbol, df.index[j0], df.index[j], real(fill), real(px), s.stop_dist, why, j - j0 + 1, s.tag, side))
            busy = j
            break
    return trades


def resample_bars(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if minutes <= 1 or df.empty:
        return df
    r = df.resample(f"{minutes}min", label="left", closed="left", origin="start_day")
    out = pd.DataFrame({"open": r["open"].first(), "high": r["high"].max(), "low": r["low"].min(), "close": r["close"].last(), "volume": r["volume"].sum()})
    return out.dropna(subset=["open"])


FLAT_BY = "15:50"   # intraday positions are closed by 3:50 pm ET (the live runner's EntryWindow.flatten_at); the last kept bar closes at that time


def split_days(bars: dict[str, pd.DataFrame], minutes: int = 1, feature_fn=None, min_bars: int | None = None, flat_by: str | None = FLAT_BY) -> dict[str, list[pd.DataFrame]]:
    """symbol -> list of one-session frames. minutes: aggregate to that bar size first. feature_fn: causal indicator columns computed once on the
    continuous series (so the first bar of a day already has warm indicators). flat_by: drop bars that close after this time, so the backtest's
    end-of-day exit happens at the same clock time as the live flatten."""
    need = min_bars if min_bars is not None else max(60 // minutes, 12)
    out = {}
    for sym, df in bars.items():
        df = resample_bars(df, minutes)
        if feature_fn is not None and len(df):
            f = feature_fn(df)
            f = f.astype({c: "float32" for c in f.columns if f[c].dtype == "float64"})     # halves memory on 10 years of 5-minute bars
            df = df.join(f)
        frames = [d for _, d in df.groupby(df.index.date)]
        if flat_by and len(df):
            h, m = map(int, flat_by.split(":"))
            cut = []
            for d in frames:
                close_min = d.index.hour * 60 + d.index.minute + max(minutes, 1)
                session_end = int(close_min.max())                                # 960 (4:00 pm), or 780 on an early-close day
                limit = min(h * 60 + m, session_end - 10) if session_end < 16 * 60 else h * 60 + m
                cut.append(d[close_min <= limit])
            frames = cut
        out[sym] = [d for d in frames if len(d) >= need]
    return out


def raw_intraday(strategy, days: dict[str, list[pd.DataFrame]], costs: Costs, placebo_seed: int | None = None, max_trades: int = 3,
                 start: date | None = None, end: date | None = None) -> list[Trade]:
    """All trades, before position sizing. placebo_seed: replace each day's signals by the same number at random times."""
    rng = np.random.default_rng(placebo_seed) if placebo_seed is not None else None
    out = []
    for sym, frames in days.items():
        for day in frames:
            dd = day.index[0].date()
            if (start and dd < start) or (end and dd > end):
                continue
            sigs = strategy.signals(day)
            if rng is not None:
                sides = [s.side for s in sigs]
                sigs = strategy.placebo(day, len(sigs), rng) if sigs else []
                if any(x == -1 for x in sides):          # same long/short mix as the real signals, at random times
                    rng.shuffle(sides)
                    sigs = [replace(p, side=sd) for p, sd in zip(sigs, sides)]
            out += simulate_day(sym, day, sigs, costs, max_trades=getattr(strategy, "max_trades", max_trades), cooldown=getattr(strategy, "cooldown", 0))
    return sorted(out, key=lambda t: t.entry_time)


@dataclass
class Portfolio:
    equity: float = 100_000.0
    risk_pct: float = 0.005         # equity risked per trade (distance to the stop)
    max_positions: int = 4
    max_pos_frac: float = 0.25      # notional cap per position (no leverage)
    daily_loss_limit: float = 0.02  # stop opening trades after losing this much in a day
    pdt: bool = False               # enforce 3 day trades per 5 sessions below $25k
    min_qty: int = 1


def size_intraday(trades: list[Trade], costs: Costs, pf: Portfolio) -> pd.DataFrame:
    """Turn raw trades into sized fills day by day. Returns one row per executed trade."""
    rows, equity, history = [], pf.equity, []   # history: dates of day trades (for PDT)
    for day, todays in pd.Series(trades).groupby([t.entry_time.date() for t in trades]):
        start, day_pnl, open_until, taken = equity, 0.0, [], 0
        window = [d for d in history if (day - d).days < 7][-5:]
        for t in sorted(todays, key=lambda t: t.entry_time):
            open_until = [x for x in open_until if x > t.entry_time]
            if len(open_until) >= pf.max_positions or day_pnl <= -pf.daily_loss_limit * start:
                continue
            if pf.pdt and start < 25_000 and len([d for d in window]) + taken >= 3:
                continue
            risk_ps = t.stop_dist + t.entry_px * costs.slip_bps / 1e4
            qty = int(min(start * pf.risk_pct / risk_ps, start * pf.max_pos_frac / t.entry_px))
            if qty < pf.min_qty:
                continue
            pnl = qty * t.pnl_per_share(costs)
            day_pnl += pnl
            taken += 1
            open_until.append(t.exit_time)
            rows.append({"day": day, "symbol": t.symbol, "entry": t.entry_time, "exit": t.exit_time, "qty": qty, "entry_px": t.entry_px,
                         "exit_px": t.exit_px, "pnl": pnl, "r": t.pnl_per_share(costs) / t.stop_dist, "reason": t.reason,
                         "bars": t.bars, "notional": qty * t.entry_px, "tag": t.tag, "side": t.side})
        if taken:
            history.append(day)
        equity += day_pnl
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- swing
def simulate_swing(symbol: str, d: pd.DataFrame, strat, costs: Costs, all_signals: bool = False) -> list[Trade]:
    """all_signals=False: one trade at a time per symbol. True: every entry signal becomes a candidate trade and the PORTFOLIO decides
    (size_swing takes a candidate only when the symbol is not already held), which is what a live bot does when its slots are full."""
    f = strat.frame(d)
    o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    entry, exit_, n, slip = f["entry"].to_numpy(bool), f["exit"].to_numpy(bool), len(d), costs.slip_bps / 1e4
    out, i = [], 0
    while i < n - 1:
        if not entry[i]:
            i += 1
            continue
        j0 = i + 1
        sd = strat.stop_dist(f, i)
        if not np.isfinite(sd) or sd <= 0:
            i += 1
            continue
        fill = o[j0] * (1 + slip)
        stop = fill - sd
        if hasattr(strat, "stop_price"):              # a stop that is a fixed PRICE decided at the signal bar, not a distance from the fill
            stop = float(strat.stop_price(f, i))
            sd = fill - stop                          # risk per share is measured from the actual fill (a gap up widens it)
            if not np.isfinite(sd) or sd <= 0:
                i += 1
                continue
        k, px, why = j0, None, ""
        while k < n:
            if k > j0 and o[k] <= stop:
                px, why = o[k] * (1 - slip), "stop"
            elif l[k] <= stop:
                px, why = stop * (1 - slip), "stop"
            elif exit_[k] or (k - j0 + 1) >= strat.max_days:
                if k + 1 < n:
                    why = "signal" if exit_[k] else "time"
                    k, px = k + 1, o[k + 1] * (1 - slip)
                else:
                    px, why = c[k] * (1 - slip), "end"
            elif k == n - 1:
                px, why = c[k] * (1 - slip), "end"
            if px is not None:
                break
            k += 1
        out.append(Trade(symbol, d.index[j0], d.index[min(k, n - 1)], fill, px, sd, why, k - j0 + 1,
                         rank=float(strat.rank(f, i)) if hasattr(strat, "rank") else 0.0))
        i = i + 1 if all_signals else max(k, i + 1)
    return out


def size_swing(trades: list[Trade], closes: dict[str, pd.Series], costs: Costs, pf: Portfolio, start: date, end: date) -> tuple[pd.DataFrame, pd.Series]:
    """Daily portfolio with mark-to-market equity. closes: symbol -> daily close series (same index type as trade times)."""
    days = sorted({d for s in closes.values() for d in s.index if start <= d.date() <= end})
    by_entry = {}
    for t in trades:
        by_entry.setdefault(t.entry_time, []).append(t)
    cash, open_pos, rows, curve = pf.equity, [], [], {}
    last_eq = pf.equity
    for d in days:
        def realize(p):
            nonlocal cash
            cash += p["qty"] * (p["t"].exit_px - p["t"].exit_px * costs.fee_bps_sell / 1e4) - 2 * costs.commission * p["qty"]
            pnl = p["qty"] * p["t"].pnl_per_share(costs)
            rows.append({"symbol": p["t"].symbol, "entry": p["t"].entry_time, "exit": p["t"].exit_time, "qty": p["qty"], "entry_px": p["t"].entry_px,
                         "exit_px": p["t"].exit_px, "pnl": pnl, "r": p["t"].pnl_per_share(costs) / p["t"].stop_dist, "reason": p["t"].reason,
                         "bars": p["t"].bars, "notional": p["qty"] * p["t"].entry_px})
            open_pos.remove(p)

        # exits that happen AT this open (signal / time) free their slot and cash for today's entries; a stop that hits later TODAY does not
        # (a live bot placing orders before the open cannot know about it), so it is realized after today's entries
        for p in [p for p in open_pos if p["t"].exit_time < d or (p["t"].exit_time == d and p["t"].reason != "stop")]:
            realize(p)
        for t in by_entry.get(d, []):
            if len(open_pos) >= pf.max_positions:
                break
            if any(p["t"].symbol == t.symbol for p in open_pos):
                continue                                  # already holding this name
            risk_ps = t.stop_dist + t.entry_px * costs.slip_bps / 1e4
            qty = int(min(last_eq * pf.risk_pct / risk_ps, last_eq * pf.max_pos_frac / t.entry_px, cash / t.entry_px))
            if qty < pf.min_qty:
                continue
            cash -= qty * t.entry_px
            open_pos.append({"t": t, "qty": qty})
        for p in [p for p in open_pos if p["t"].exit_time == d and p["t"].reason == "stop"]:
            realize(p)
        mv = sum(p["qty"] * float(closes[p["t"].symbol].get(d, p["t"].entry_px)) for p in open_pos)
        last_eq = cash + mv
        curve[d] = last_eq
    return pd.DataFrame(rows), pd.Series(curve)


# ---------------------------------------------------------------- metrics
def metrics(fills: pd.DataFrame, curve: pd.Series | None = None, start_equity: float = 100_000.0) -> dict:
    if fills is None or len(fills) == 0:
        return {"trades": 0}
    fills = fills.assign(pnl=fills.pnl.astype(float), r=fills.r.astype(float), notional=fills.notional.astype(float))
    w, lo = fills[fills.pnl > 0], fills[fills.pnl <= 0]
    out = {"trades": int(len(fills)), "win_rate": float((fills.pnl > 0).mean()), "avg_r": float(fills.r.mean()),
           "expectancy_pct_notional": float((fills.pnl / fills.notional).mean() * 100),
           "profit_factor": float(w.pnl.sum() / abs(lo.pnl.sum())) if len(lo) and lo.pnl.sum() != 0 else float("inf"),
           "total_pnl": float(fills.pnl.sum()), "avg_bars": float(fills.bars.mean())}
    if curve is None:
        daily = fills.groupby("day").pnl.sum() if "day" in fills else fills.groupby(pd.to_datetime(fills.exit).dt.date).pnl.sum()
        curve = start_equity + daily.cumsum()
    ret = curve.pct_change().dropna()
    if len(ret) > 2 and ret.std() > 0:
        out["sharpe"] = float(ret.mean() / ret.std() * np.sqrt(252))
        years = max(len(curve) / 252, 1e-9)
        out["cagr"] = float((curve.iloc[-1] / start_equity) ** (1 / years) - 1)
        out["max_drawdown"] = float((curve / curve.cummax() - 1).min())
        out["daily_ret_pct_mean"] = float(ret.mean() * 100)
        out["daily_ret_pct_median"] = float(ret.median() * 100)
        out["pct_days_up"] = float((ret > 0).mean())
    return out
