"""Backtest engine: next-bar fills, stops/targets on bar highs/lows (stop wins ties), slippage and fees, then a portfolio layer
that sizes by risk, caps concurrent positions and applies a daily loss limit (and optionally the pattern-day-trader rule).

Intraday strategies close at the end of the same session. Swing strategies decide at the close and trade the next open.
"""

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from .strategies import Signal


@dataclass
class Costs:
    slip_bps: float = 2.0       # adverse slippage per side, in basis points of price (liquid ETFs/large caps; stress it)
    fee_bps_sell: float = 0.4   # SEC + FINRA TAF on the sell leg, roughly
    commission: float = 0.0     # per share (Alpaca stocks are commission-free)


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

    def pnl_per_share(self, costs: Costs) -> float:
        fee = self.exit_px * costs.fee_bps_sell / 1e4 + 2 * costs.commission
        return self.exit_px - self.entry_px - fee


# ---------------------------------------------------------------- intraday
def simulate_day(symbol: str, df: pd.DataFrame, sigs: list[Signal], costs: Costs, max_trades: int = 3) -> list[Trade]:
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    n, slip, trades, busy = len(df), costs.slip_bps / 1e4, [], -1
    for s in sorted(sigs, key=lambda s: s.i):
        if len(trades) >= max_trades:
            break
        j0 = s.i + 1
        if s.side != 1:
            raise NotImplementedError("long only")
        if j0 >= n or s.i < busy or s.stop_dist <= 0:
            continue
        fill = o[j0] * (1 + slip)
        stop = fill - s.stop_dist
        tgt = fill + s.target_r * s.stop_dist if s.target_r > 0 else np.inf
        for j in range(j0, n):
            if l[j] <= stop:
                px, why = min(stop, o[j]) * (1 - slip), "stop"
            elif h[j] >= tgt:
                px, why = max(tgt, o[j]), "target"
            elif (s.max_bars and j - j0 + 1 >= s.max_bars) or j == n - 1:
                px, why = c[j] * (1 - slip), "time" if j < n - 1 else "eod"
            else:
                continue
            trades.append(Trade(symbol, df.index[j0], df.index[j], fill, px, s.stop_dist, why, j - j0 + 1, s.tag))
            busy = j
            break
    return trades


def split_days(bars: dict[str, pd.DataFrame]) -> dict[str, list[pd.DataFrame]]:
    """symbol -> list of one-session frames (sessions with fewer than 60 bars dropped)."""
    return {sym: [d for _, d in df.groupby(df.index.date) if len(d) >= 60] for sym, df in bars.items()}


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
                sigs = strategy.placebo(day, len(sigs), rng) if sigs else []
            out += simulate_day(sym, day, sigs, costs, max_trades=getattr(strategy, "max_trades", max_trades))
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
                         "bars": t.bars, "notional": qty * t.entry_px, "tag": t.tag})
        if taken:
            history.append(day)
        equity += day_pnl
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- swing
def simulate_swing(symbol: str, d: pd.DataFrame, strat, costs: Costs) -> list[Trade]:
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
        out.append(Trade(symbol, d.index[j0], d.index[min(k, n - 1)], fill, px, sd, why, k - j0 + 1))
        i = max(k, i + 1)
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
        # exits at this date (open or stop): realize
        for p in [p for p in open_pos if p["t"].exit_time <= d]:
            cash += p["qty"] * (p["t"].exit_px - p["t"].exit_px * costs.fee_bps_sell / 1e4) - 2 * costs.commission * p["qty"]
            pnl = p["qty"] * p["t"].pnl_per_share(costs)
            rows.append({"symbol": p["t"].symbol, "entry": p["t"].entry_time, "exit": p["t"].exit_time, "qty": p["qty"], "entry_px": p["t"].entry_px,
                         "exit_px": p["t"].exit_px, "pnl": pnl, "r": p["t"].pnl_per_share(costs) / p["t"].stop_dist, "reason": p["t"].reason,
                         "bars": p["t"].bars, "notional": p["qty"] * p["t"].entry_px})
            open_pos.remove(p)
        for t in by_entry.get(d, []):
            if len(open_pos) >= pf.max_positions:
                break
            risk_ps = t.stop_dist + t.entry_px * costs.slip_bps / 1e4
            qty = int(min(last_eq * pf.risk_pct / risk_ps, last_eq * pf.max_pos_frac / t.entry_px, cash / t.entry_px))
            if qty < pf.min_qty:
                continue
            cash -= qty * t.entry_px
            open_pos.append({"t": t, "qty": qty})
        mv = sum(p["qty"] * float(closes[p["t"].symbol].get(d, p["t"].entry_px)) for p in open_pos)
        last_eq = cash + mv
        curve[d] = last_eq
    return pd.DataFrame(rows), pd.Series(curve)


# ---------------------------------------------------------------- metrics
def metrics(fills: pd.DataFrame, curve: pd.Series | None = None, start_equity: float = 100_000.0) -> dict:
    if fills is None or len(fills) == 0:
        return {"trades": 0}
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
    return out
