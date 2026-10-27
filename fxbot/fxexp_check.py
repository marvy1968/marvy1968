"""The owner's fx-experts bot, tested (owner 2026-10-27, "test the four fx-experts strategies"): williams-vbo, faith-turtle, tharp-random, lien-zeros.
The bot's code is in forex_bots/fx-experts (exported from /opt/fx-experts). Its own event backtester (fxexp.backtest.Sim) is reused unchanged for the
three daily systems, on OANDA H1 mid candles (the bot's own data model and costs: typical spread, 0.3 pip slippage on stop fills, 2x spread at the
17:00 NY open, 0.3 pip a night financing). lien-zeros is an intraday M5/M15 limit system the bot's backtester does not cover, so it gets its own
simulator here on real OANDA bid/ask M5 bars.

PRE-DECLARED bar (the same promotion bar as strict_fx): three back-to-back windows ending today - train, select and OOS (the last 1 year, run once).
PASS only if every window is profitable at the normal spread, the OOS window has >= 40 trades and PF >= 1.15 at 2x the spread, and the OOS average R
beats EVERY seed of the SHUFFLED-DATA PLACEBO (its best seed): the same system on the same bars with their order randomly permuted (bar shapes, gaps, volatility and the spread are
kept, only the serial structure is destroyed; 5 seeds for the daily systems, 3 for lien-zeros). This matters because the bot's engine walks each bar
O->L->H->C for a bullish bar and O->H->L->C for a bearish one, which favours breakout entries (a synthetic random walk gave williams-vbo PF 1.12);
the placebo carries the same bias, so only an edge beyond it counts. tharp-random is a random-entry system by design: it claims no edge, so a PASS there
would point at the harness. Daily systems: windows 3y / 1y / 1y. lien-zeros: 1y / 1y / 1y (M5 data).
Financing is charged as the bot's engine does. R = the initial stop distance."""

import math
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .data import mid
from .engine import _nights
from .exitlab import _spread

BOT = Path(__file__).resolve().parents[1] / "forex_bots" / "fx-experts"
DAILY = ("williams-vbo", "faith-turtle", "tharp-random")


def _bot():
    if str(BOT) not in sys.path:
        sys.path.insert(0, str(BOT))
    from fxexp import backtest, data, instruments
    from fxexp.strategies import BY_SID
    return backtest, data, instruments, BY_SID


def windows(today: date, daily: bool = True) -> dict:
    y = 365
    if daily:
        return {"train 3y": (today - timedelta(days=5 * y), today - timedelta(days=2 * y + 1)),
                "select 1y": (today - timedelta(days=2 * y), today - timedelta(days=y + 1)), "OOS last 1y": (today - timedelta(days=y), today)}
    return {"train 1y": (today - timedelta(days=3 * y), today - timedelta(days=2 * y + 1)),
            "select 1y": (today - timedelta(days=2 * y), today - timedelta(days=y + 1)), "OOS last 1y": (today - timedelta(days=y), today)}


def _mid_frame(df: pd.DataFrame) -> pd.DataFrame:
    m = mid(df)
    out = m[["open", "high", "low", "close"]].copy()
    out.insert(0, "time", out.index)
    return out.reset_index(drop=True)


def daily_trades(sid: str, h1_by_pair: dict, spread_mult: float = 1.0) -> list[dict]:
    backtest, botdata, instruments, BY_SID = _bot()
    base = dict(instruments.TYPICAL_SPREAD_PIPS)
    try:
        for k in base:
            instruments.TYPICAL_SPREAD_PIPS[k] = base[k] * spread_mult
        strat = BY_SID[sid]()
        pairs = strat.pairs or instruments.PAIRS
        out = []
        for p in pairs:
            if p not in h1_by_pair:
                continue
            h1 = h1_by_pair[p]
            sim = backtest.Sim(BY_SID[sid](), p, h1, botdata.to_daily(h1))
            out += [{"date": pd.Timestamp(t.entry_time).date(), "r": t.r, "pair": p} for t in sim.run()]
        return out
    finally:
        instruments.TYPICAL_SPREAD_PIPS.clear()
        instruments.TYPICAL_SPREAD_PIPS.update(base)


def zeros_trades(df: pd.DataFrame, pair: str, spread_mult: float = 1.0, max_hold_bars: int = 576, max_per_day: int = 3) -> list[dict]:
    """lien-zeros (fxexp.strategies.lien_zeros) on bid/ask M5 bars: every completed 15-minute bar may place a resting LIMIT 5 pips beyond the figure
    (valid 60 minutes), stop 20 pips, target 40 pips (2R), one trade per pair, at most `max_per_day` signals per trading day. Stop is checked before the
    target inside a bar; a gap through a stop fills at the open; a trade still open after `max_hold_bars` M5 bars closes at market."""
    df = _spread(df, spread_mult)
    pip = 0.01 if pair.endswith("_JPY") else 0.0001
    fig = 100 * pip
    idx = df.index
    bo, bh, bl, bc, ao, ah, al, ac = (df[k].to_numpy(float) for k in ("bo", "bh", "bl", "bc", "ao", "ah", "al", "ac"))
    mc = ((df["bc"] + df["ac"]) / 2)
    m15 = mc.resample("15min", label="left", closed="left").last().dropna()
    sma = m15.rolling(20).mean()
    pos_of = {t: i for i, t in enumerate(idx)}
    next_free, day_count, out = 0, {}, []
    fin = 0.3 * pip
    for t15, c in m15.items():
        s = sma.get(t15)
        if s is None or not np.isfinite(s):
            continue
        t_decide = t15 + pd.Timedelta(minutes=15)
        i0 = idx.searchsorted(t_decide)
        if i0 >= len(idx) - 2 or i0 < next_free:
            continue
        day = (t_decide.tz_convert("America/New_York") + pd.Timedelta(hours=7)).date()
        if day_count.get(day, 0) >= max_per_day:
            continue
        side = 0
        if c < s - 10 * pip:
            f = math.floor(c / fig + 1e-9) * fig
            if 3 * pip <= c - f <= 30 * pip:
                side, e = 1, f - 5 * pip
        elif c > s + 10 * pip:
            f = math.ceil(c / fig - 1e-9) * fig
            if 3 * pip <= f - c <= 30 * pip:
                side, e = -1, f + 5 * pip
        if not side:
            continue
        day_count[day] = day_count.get(day, 0) + 1
        stop, tp = e - side * 20 * pip, e + side * 40 * pip
        exp = idx.searchsorted(t_decide + pd.Timedelta(minutes=60))
        j, fill = i0, None
        while j < min(exp, len(idx)):
            if side == 1 and al[j] <= e:
                fill = min(e, ao[j])
                break
            if side == -1 and bh[j] >= e:
                fill = max(e, bo[j])
                break
            j += 1
        if fill is None:
            continue
        k, px = j, None
        end = min(j + max_hold_bars, len(idx) - 1)
        while k <= end:
            if side == 1:
                if k > j and bo[k] <= stop:
                    px = bo[k]
                elif bl[k] <= stop:
                    px = stop
                elif bh[k] >= tp:
                    px = max(tp, bo[k]) if k > j else tp
            else:
                if k > j and ao[k] >= stop:
                    px = ao[k]
                elif ah[k] >= stop:
                    px = stop
                elif al[k] <= tp:
                    px = min(tp, ao[k]) if k > j else tp
            if px is not None:
                break
            k += 1
        if px is None:
            k = end
            px = bc[k] if side == 1 else ac[k]
        n = _nights(idx[j], idx[min(k, len(idx) - 1)])
        out.append({"date": idx[j].date(), "r": (side * (px - fill) - n * fin) / (20 * pip), "pair": pair})
        next_free = k + 1
    return out


def shuffled_mid(df: pd.DataFrame, seed: int) -> pd.DataFrame:
    """time/open/high/low/close frame -> same timestamps, bars permuted (gap, high, low, close relative to the open travel together)."""
    o, h, lo, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    gap = o[1:] / c[:-1] - 1
    unit = np.c_[gap, h[1:] / o[1:] - 1, lo[1:] / o[1:] - 1, c[1:] / o[1:] - 1]
    unit = unit[np.random.default_rng(seed).permutation(len(unit))]
    n, px = len(unit), np.empty((len(unit) + 1, 4))
    prev = c[0]
    px[0] = (o[0], h[0], lo[0], c[0])
    for i in range(n):
        oo = prev * (1 + unit[i, 0])
        px[i + 1] = (oo, oo * (1 + unit[i, 1]), oo * (1 + unit[i, 2]), oo * (1 + unit[i, 3]))
        prev = px[i + 1, 3]
    out = df[["time"]].copy()
    out["open"], out["high"], out["low"], out["close"] = px[:, 0], px[:, 1], px[:, 2], px[:, 3]
    return out


def shuffled_bidask(df: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Bid/ask bars with the mid path permuted as above and each bar's own half spread kept in place."""
    m = pd.DataFrame({"time": df.index, "open": ((df["bo"] + df["ao"]) / 2).to_numpy(), "high": ((df["bh"] + df["ah"]) / 2).to_numpy(),
                      "low": ((df["bl"] + df["al"]) / 2).to_numpy(), "close": ((df["bc"] + df["ac"]) / 2).to_numpy()})
    sm = shuffled_mid(m, seed)
    out = df.copy()
    for k in "ohlc":
        half = ((df["a" + k] - df["b" + k]) / 2).to_numpy()
        col = {"o": "open", "h": "high", "l": "low", "c": "close"}[k]
        out["b" + k], out["a" + k] = sm[col].to_numpy() - half, sm[col].to_numpy() + half
    return out


def stats(rs: list[float]) -> dict:
    if not rs:
        return {"trades": 0, "win": 0.0, "pf": None, "net_r": 0.0, "avg_r": 0.0, "maxdd_r": 0.0}
    w, lo = [r for r in rs if r > 0], [r for r in rs if r <= 0]
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {"trades": len(rs), "win": len(w) / len(rs), "pf": (sum(w) / -sum(lo)) if lo and sum(lo) < 0 else 99.0, "net_r": sum(rs),
            "avg_r": sum(rs) / len(rs), "maxdd_r": dd}


def evaluate(trades_1x: list[dict], trades_2x: list[dict], wins: dict, ref_avg_r: float | None) -> dict:
    sel = lambda tr, a, b: [t["r"] for t in sorted(tr, key=lambda x: x["date"]) if a <= t["date"] <= b]   # noqa: E731
    res = {k: stats(sel(trades_1x, a, b)) for k, (a, b) in wins.items()}
    oos_key = list(wins)[-1]
    a, b = wins[oos_key]
    oos2 = stats(sel(trades_2x, a, b))
    ok = (all(v["net_r"] > 0 for v in res.values()) and res[oos_key]["trades"] >= 40 and (oos2["pf"] or 0) >= 1.15
          and (ref_avg_r is None or res[oos_key]["avg_r"] > ref_avg_r))
    return {"windows": res, "oos_2x": oos2, "pass": bool(ok)}


def _fmt(s: dict) -> str:
    pf = "n/a" if s["pf"] is None else f"{s['pf']:.2f}"
    return f"{s['trades']}t win {s['win']:.0%} PF {pf} net {s['net_r']:+.1f}R avg {s['avg_r']:+.3f}R maxDD {s['maxdd_r']:.1f}R"


def lines(sid: str, ev: dict, ref_avg_r: float | None) -> list[str]:
    body = " | ".join(f"{k}: {_fmt(v)}" for k, v in ev["windows"].items())
    ref = "" if ref_avg_r is None else f" | shuffled-data placebo OOS avg (best seed) {ref_avg_r:+.3f}R"
    verdict = "PASS" if ev["pass"] else "reject"
    if sid == "tharp-random":
        verdict = "RANDOM-ENTRY SYSTEM BY DESIGN (no edge claim)" + (": a PASS would point at the harness" if ev["pass"] else "")
    return [f"### {sid}: {body} | OOS at 2x spread: {_fmt(ev['oos_2x'])}{ref} | {verdict}"]
