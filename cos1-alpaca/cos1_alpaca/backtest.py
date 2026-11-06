"""Short walk-forward backtest. Parameters are frozen. Nothing is optimized.

Out of sample is the last 3-6 months. The universe is four liquid names.
``--source synthetic`` needs no keys. ``--source alpaca`` pulls IEX daily bars
(with 200-day warmup) and 5-minute bars for the scored window.
Costs, the position cap, and the feed label come from btcosts (the same field
names as alpacabot.engine.Costs / Portfolio). Treat the numbers as a harness
check, not evidence of an edge.
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from . import btcosts
from .bars import MIN_DAILY_BARS
from .btcosts import apply_entry, apply_stop_exit, feed_label, pnl_per_share, scale_costs
from .clock import ET, bar_session_date
from .config import Config
from .regime import classify_regime
from .signals import day_pullback, donchian, mean_reversion, range_box, scalp, swing_ema
from .types import Bar

BACKTEST_SYMBOLS = ("SPY", "XLK", "AAPL", "MSFT")
# Real-data runs stay on a handful of liquid names so three months of 5-minute bars finish quickly.
ALPACA_SYMBOLS = ("SPY", "AAPL", "MSFT", "XLK")
ALPACA_EXPERTS = ("swing_ema", "swing_donchian", "day_pullback")


def _next_day(day: date) -> date:
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def make_spy(n: int, seed: int) -> list[Bar]:
    rng = random.Random(seed)
    # Piecewise drift so the regime filter actually changes. Not fitted.
    plan = ([(0.0015, 1.1)] * 140) + ([(-0.0016, 1.2)] * 90) + ([(0.0, 1.4)] * 70) + ([(0.0012, 1.0)] * 80)
    while len(plan) < n:
        plan += plan
    price = 100.0
    day = date(2023, 1, 3)
    bars = []
    for i in range(n):
        drift, vol = plan[i]
        shock = rng.gauss(0, vol)
        opened = price
        close = max(8.0, price * (1.0 + drift) + shock * 0.15)
        high = max(opened, close) + abs(shock) * 0.2 + 0.05
        low = min(opened, close) - abs(shock) * 0.2 - 0.05
        low = max(1.0, low)
        bars.append(Bar(datetime.combine(day, time(16, 0), tzinfo=ET), opened, high, low, close, 2_500_000.0))
        price = close
        day = _next_day(day)
    return bars


def make_stock(spy: list[Bar], beta: float, seed: int) -> list[Bar]:
    rng = random.Random(seed)
    price = spy[0].open
    out = []
    for bar in spy:
        ret = bar.close / bar.open - 1.0
        close = max(5.0, price * (1.0 + beta * ret + rng.gauss(0, 0.004)))
        high = max(price, close) * (1.0 + abs(rng.gauss(0, 0.002)))
        low = min(price, close) * (1.0 - abs(rng.gauss(0, 0.002)))
        out.append(Bar(bar.ts, price, high, max(1.0, low), close, bar.volume * 0.8))
        price = close
    return out


def intraday(day_bar: Bar, minutes: int, rng: random.Random) -> list[Bar]:
    steps = int(390 / minutes)
    start = datetime.combine(bar_session_date(day_bar.ts), time(9, 30), tzinfo=ET)
    price = day_bar.open
    step = (day_bar.close - day_bar.open) / steps
    width = max(0.02, (day_bar.high - day_bar.low) / 8.0)
    bars = []
    for i in range(steps):
        noise = rng.gauss(0, width)
        opened = price
        close = price + step + noise
        high = max(opened, close) + abs(noise) * 0.25
        low = max(0.5, min(opened, close) - abs(noise) * 0.25)
        if i == steps - 1:
            close = day_bar.close
            high = max(high, close, opened)
            low = min(low, close, opened)
        volume = day_bar.volume / steps
        bars.append(Bar(start + timedelta(minutes=minutes * i), opened, high, low, close, volume))
        price = close
    return bars


def _metrics(trades: list[dict], start_equity: float, end_equity: float, curve: list[float]) -> dict:
    pnls = [t["pnl"] for t in trades]
    wins = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    if losses == 0:
        profit_factor = None if wins == 0 else float("inf")
    else:
        profit_factor = wins / losses
    peak = curve[0] if curve else start_equity
    max_dd = 0.0
    for equity in curve:
        peak = max(peak, equity)
        if peak:
            max_dd = max(max_dd, (peak - equity) / peak)
    costs = [t["cost_r"] for t in trades if t.get("cost_r") is not None]
    return {
        "trades": len(trades),
        "profit_factor": profit_factor,
        "roi": (end_equity / start_equity - 1.0) if start_equity else 0.0,
        "max_dd": max_dd,
        "avg_cost_r": (sum(costs) / len(costs)) if costs else 0.0,
    }


def _simulate_daily(name, expert, picker, bars_by_symbol, spy, cfg, costs, portfolio, oos_start: int) -> dict:
    equity = portfolio.equity
    cash_curve = [equity]
    open_pos: dict[str, dict] = {}
    trades = []
    day_key = ""
    day_start = equity
    day_pnl = 0.0
    symbols = [s for s in bars_by_symbol if s != "SPY"] + ["SPY"]
    n = len(spy)
    for t in range(oos_start, n - 1):
        stamp = bar_session_date(spy[t].ts).isoformat()
        if stamp != day_key:
            day_key = stamp
            day_start = equity
            day_pnl = 0.0
        for symbol in list(open_pos):
            pos = open_pos[symbol]
            bar = bars_by_symbol[symbol][t]
            exit_raw, reason = _daily_exit(pos, bar, bars_by_symbol[symbol][: t + 1], cfg)
            if reason is None:
                continue
            side = pos["side_sign"]
            exit_px = apply_stop_exit(exit_raw, side, costs) if reason == "stop" else exit_raw
            net = pnl_per_share(side, pos["fill"], exit_px, costs)
            pnl = net * pos["qty"]
            frictionless = side * (exit_raw - pos["raw_open"]) * pos["qty"]
            cost_r = ((frictionless - pnl) / (pos["stop_dist"] * pos["qty"])) if pos["stop_dist"] else 0.0
            equity += pnl
            day_pnl += pnl
            trades.append({"pnl": pnl, "cost_r": cost_r, "reason": reason, "symbol": symbol})
            del open_pos[symbol]
        regime = classify_regime(spy[: t + 1], cfg.regime)
        if day_pnl <= -portfolio.daily_loss_limit * day_start:
            cash_curve.append(equity)
            continue
        for symbol in symbols:
            if symbol in open_pos or len(open_pos) >= portfolio.max_positions:
                continue
            sig = picker(symbol, bars_by_symbol[symbol][: t + 1], regime, cfg)
            if sig is None:
                continue
            nxt = bars_by_symbol[symbol][t + 1]
            side = 1 if sig.side == "long" else -1
            raw_open = nxt.open
            fill = apply_entry(raw_open, side, costs)
            stop = sig.stop
            if side == 1 and fill <= stop:
                continue
            if side == -1 and fill >= stop:
                continue
            stop_dist = abs(fill - stop)
            if stop_dist <= 0:
                continue
            qty = int(min(equity * portfolio.risk_pct / stop_dist, equity * portfolio.max_pos_frac / fill))
            if qty < portfolio.min_qty:
                continue
            risk = abs(fill - stop)
            target = sig.target if sig.target is not None else (
                fill + sig.target_r * risk if side == 1 else fill - sig.target_r * risk
            )
            open_pos[symbol] = {
                "side_sign": side,
                "fill": fill,
                "raw_open": raw_open,
                "stop": stop,
                "tp": target,
                "qty": qty,
                "stop_dist": stop_dist,
                "expert": sig.expert,
                "opened": t + 1,
                "entry": sig.entry,
            }
        cash_curve.append(equity)
    # Mark residual positions to the last close, with exit slippage.
    last = n - 1
    for symbol, pos in list(open_pos.items()):
        raw_exit = bars_by_symbol[symbol][last].close
        exit_px = apply_stop_exit(raw_exit, pos["side_sign"], costs)
        net = pnl_per_share(pos["side_sign"], pos["fill"], exit_px, costs)
        pnl = net * pos["qty"]
        frictionless = pos["side_sign"] * (raw_exit - pos["raw_open"]) * pos["qty"]
        cost_r = ((frictionless - pnl) / (pos["stop_dist"] * pos["qty"])) if pos["stop_dist"] else 0.0
        equity += pnl
        trades.append({"pnl": pnl, "cost_r": cost_r, "reason": "end", "symbol": symbol})
    cash_curve.append(equity)
    out = _metrics(trades, portfolio.equity, equity, cash_curve)
    out.update({"mode": name, "expert": expert, "log_only": expert in {"mean_reversion", "range_box", "scalp"}})
    return out


def _daily_exit(pos, bar, history, cfg):
    side = pos["side_sign"]
    if side == 1:
        if bar.open <= pos["stop"]:
            return bar.open, "stop"
        if bar.low <= pos["stop"]:
            return pos["stop"], "stop"
        if bar.high >= pos["tp"]:
            return pos["tp"], "target"
    else:
        if bar.open >= pos["stop"]:
            return bar.open, "stop"
        if bar.high >= pos["stop"]:
            return pos["stop"], "stop"
        if bar.low <= pos["tp"]:
            return pos["tp"], "target"
    if pos["expert"] == "swing_donchian":
        from .signals import donchian_exit

        if donchian_exit(history, "long" if side == 1 else "short", cfg.swing.donchian_exit):
            return bar.close, "donchian"
    if pos["expert"] == "swing_ema":
        from .indicators import ema_series

        line = ema_series([b.close for b in history], cfg.swing.ema_exit)
        if line[-1] is not None:
            if side == 1 and bar.close < line[-1]:
                return bar.close, "ema"
            if side == -1 and bar.close > line[-1]:
                return bar.close, "ema"
    held = len(history) - pos["opened"]
    if held >= cfg.swing.max_hold_days:
        return bar.close, "time"
    return None, None


def _simulate_intraday(name, expert, minutes, days, daily_by_symbol, spy, cfg, costs, portfolio, seed: int) -> dict:
    rng = random.Random(seed)
    equity = portfolio.equity
    curve = [equity]
    trades = []
    oos = spy[-days:]
    for day_bar in oos:
        day = bar_session_date(day_bar.ts)
        spy_hist = [b for b in spy if bar_session_date(b.ts) < day]
        if len(spy_hist) < MIN_DAILY_BARS:
            continue
        regime = classify_regime(spy_hist, cfg.regime)
        day_pnl = 0.0
        open_pos = {}
        series = {}
        for symbol, daily in daily_by_symbol.items():
            match = [b for b in daily if bar_session_date(b.ts) == day]
            if match:
                series[symbol] = intraday(match[0], minutes, rng)
        if not series:
            continue
        steps = len(next(iter(series.values())))
        for i in range(steps):
            for symbol, pos in list(open_pos.items()):
                bar = series[symbol][i]
                side = pos["side_sign"]
                hit = None
                if side == 1 and bar.low <= pos["stop"]:
                    hit = (bar.open if bar.open < pos["stop"] else pos["stop"], "stop")
                elif side == -1 and bar.high >= pos["stop"]:
                    hit = (bar.open if bar.open > pos["stop"] else pos["stop"], "stop")
                elif side == 1 and bar.high >= pos["tp"]:
                    hit = (pos["tp"], "target")
                elif side == -1 and bar.low <= pos["tp"]:
                    hit = (pos["tp"], "target")
                if hit is None and i == steps - 1 and name == "day":
                    hit = (bar.close, "flatten")
                if hit is None:
                    continue
                raw_exit, reason = hit
                exit_px = apply_stop_exit(raw_exit, side, costs) if reason in {"stop", "flatten"} else raw_exit
                net = pnl_per_share(side, pos["fill"], exit_px, costs)
                pnl = net * pos["qty"]
                frictionless = side * (raw_exit - pos["raw_open"]) * pos["qty"]
                cost_r = ((frictionless - pnl) / (pos["stop_dist"] * pos["qty"])) if pos["stop_dist"] else 0.0
                equity += pnl
                day_pnl += pnl
                trades.append({"pnl": pnl, "cost_r": cost_r, "reason": reason})
                del open_pos[symbol]
            if i < 20 or day_pnl <= -portfolio.daily_loss_limit * portfolio.equity:
                continue
            if len(open_pos) >= portfolio.max_positions:
                continue
            for symbol, bars in series.items():
                if symbol in open_pos or symbol == "SPY":
                    continue
                window = bars[max(0, i - 40): i + 1]
                if name == "day":
                    sig = day_pullback(symbol, window, regime, cfg, day)
                else:
                    sig = scalp(symbol, window, regime, cfg, day)
                if sig is None:
                    continue
                if i + 1 >= steps:
                    continue
                nxt = bars[i + 1]
                side = 1 if sig.side == "long" else -1
                fill = apply_entry(nxt.open, side, costs)
                stop = sig.stop
                if (side == 1 and fill <= stop) or (side == -1 and fill >= stop):
                    continue
                stop_dist = abs(fill - stop)
                qty = int(min(equity * portfolio.risk_pct / stop_dist, equity * portfolio.max_pos_frac / fill))
                if qty < 1:
                    continue
                risk = abs(fill - stop)
                target = fill + sig.target_r * risk if side == 1 else fill - sig.target_r * risk
                open_pos[symbol] = {
                    "side_sign": side,
                    "fill": fill,
                    "raw_open": nxt.open,
                    "stop": stop,
                    "tp": target,
                    "qty": qty,
                    "stop_dist": stop_dist,
                }
                if len(open_pos) >= portfolio.max_positions:
                    break
        curve.append(equity)
    out = _metrics(trades, portfolio.equity, equity, curve)
    out.update({"mode": name, "expert": expert, "log_only": name == "scalp"})
    return out


def _pick_ema(symbol, bars, regime, cfg):
    return swing_ema(symbol, bars, regime, cfg)


def _pick_donchian(symbol, bars, regime, cfg):
    return donchian(symbol, bars, regime, cfg)


def _pick_mr(symbol, bars, regime, cfg):
    return mean_reversion(symbol, bars, cfg)


def _pick_box(symbol, bars, regime, cfg):
    return range_box(symbol, bars, regime, cfg)


def _cost_mults(cost_mult: float) -> list[float]:
    mult = float(cost_mult)
    if mult <= 0:
        raise ValueError("cost_mult must be positive")
    if abs(mult - 1.0) < 1e-9:
        return [1.0]
    return [1.0, mult]


def _stamp_rows(rows: list[dict], mult: float) -> list[dict]:
    for row in rows:
        row["cost_mult"] = mult
    return rows


def _synthetic_rows(book, spy, cfg, costs, portfolio, oos_start: int, intraday_days: int, oos_days: int, seed: int) -> list[dict]:
    return [
        _simulate_daily("swing", "swing_ema", _pick_ema, book, spy, cfg, costs, portfolio, oos_start),
        _simulate_daily("swing", "swing_donchian", _pick_donchian, book, spy, cfg, costs, portfolio, oos_start),
        _simulate_daily("swing", "mean_reversion", _pick_mr, book, spy, cfg, costs, portfolio, oos_start),
        _simulate_daily("swing", "range_box", _pick_box, book, spy, cfg, costs, portfolio, oos_start),
        _simulate_intraday("day", "day_pullback", 5, min(intraday_days, oos_days), book, spy, cfg, costs, portfolio, seed + 99),
        _simulate_intraday("scalp", "scalp", 5, min(10, intraday_days), book, spy, cfg, costs, portfolio, seed + 100),
    ]


def _result(costs, portfolio, source: str, oos_days: int, symbols, rows: list[dict], extra: dict | None = None) -> dict:
    payload = {
        "feed": feed_label(costs),
        "data_source": source,
        "oos_days": oos_days,
        "universe": list(symbols),
        "cost_model": {
            "slip_bps": costs.slip_bps,
            "fee_bps_sell": costs.fee_bps_sell,
            "commission": costs.commission,
            "spread_bps": getattr(costs, "spread_bps", 0.0),
            "max_pos_frac": portfolio.max_pos_frac,
            "max_positions": portfolio.max_positions,
            "risk_pct": portfolio.risk_pct,
            "daily_loss_limit": portfolio.daily_loss_limit,
        },
        "rows": rows,
    }
    if extra:
        payload.update(extra)
    return payload


def run_backtest(
    *,
    oos_days: int = 126,
    daily_len: int = 360,
    intraday_days: int = 63,
    seed: int = 7,
    symbols: tuple[str, ...] = BACKTEST_SYMBOLS,
    source: str = "synthetic",
    cost_mult: float = 2.0,
    months: int | None = None,
) -> dict:
    if source == "alpaca":
        return run_alpaca_backtest(months=months or 3, cost_mult=cost_mult, symbols=symbols if symbols != BACKTEST_SYMBOLS else ALPACA_SYMBOLS)
    if source != "synthetic":
        raise ValueError(source)
    harness = btcosts.load_external() or btcosts
    costs = harness.Costs()
    portfolio = harness.Portfolio()
    cfg = Config()
    cfg.universe = list(symbols)
    # Frozen. The vol band is wide enough that a synthetic path can leave chop.
    spy = make_spy(daily_len, seed)
    book = {"SPY": spy}
    for i, symbol in enumerate(symbols):
        if symbol == "SPY":
            continue
        book[symbol] = make_stock(spy, 0.8 + 0.1 * i, seed + 1 + i)
    oos_start = max(MIN_DAILY_BARS, len(spy) - oos_days)
    rows: list[dict] = []
    for mult in _cost_mults(cost_mult):
        rows.extend(
            _stamp_rows(
                _synthetic_rows(book, spy, cfg, scale_costs(costs, mult), portfolio, oos_start, intraday_days, oos_days, seed),
                mult,
            )
        )
    return _result(costs, portfolio, "synthetic", oos_days, symbols, rows)


def _bars_by_day(bars: list[Bar]) -> dict:
    grouped: dict = {}
    for bar in bars:
        grouped.setdefault(bar_session_date(bar.ts), []).append(bar)
    for day_bars in grouped.values():
        day_bars.sort(key=lambda bar: bar.ts)
    return grouped


def _simulate_day_real(minute_by_symbol, spy: list[Bar], cfg, costs, portfolio, oos_dates: set) -> dict:
    """Day pullback on real 5-minute bars. Flat by the last bar of the session."""
    equity = portfolio.equity
    curve = [equity]
    trades = []
    grouped = {symbol: _bars_by_day(bars) for symbol, bars in minute_by_symbol.items()}
    days = sorted(day for day in oos_dates if any(day in grouped.get(symbol, {}) for symbol in minute_by_symbol))
    for day in days:
        spy_hist = [bar for bar in spy if bar_session_date(bar.ts) < day]
        if len(spy_hist) < MIN_DAILY_BARS:
            continue
        regime = classify_regime(spy_hist, cfg.regime)
        series = {symbol: grouped.get(symbol, {}).get(day, []) for symbol in minute_by_symbol if symbol != "SPY"}
        stamps = sorted({bar.ts for bars in series.values() for bar in bars})
        if len(stamps) < 25:
            continue
        lookup = {symbol: {bar.ts: bar for bar in bars} for symbol, bars in series.items()}
        ordered = {symbol: bars for symbol, bars in series.items()}
        day_pnl = 0.0
        open_pos = {}
        for i, ts in enumerate(stamps):
            for symbol, pos in list(open_pos.items()):
                bar = lookup[symbol].get(ts)
                if bar is None:
                    continue
                side = pos["side_sign"]
                hit = None
                if side == 1 and bar.low <= pos["stop"]:
                    hit = (bar.open if bar.open < pos["stop"] else pos["stop"], "stop")
                elif side == -1 and bar.high >= pos["stop"]:
                    hit = (bar.open if bar.open > pos["stop"] else pos["stop"], "stop")
                elif side == 1 and bar.high >= pos["tp"]:
                    hit = (pos["tp"], "target")
                elif side == -1 and bar.low <= pos["tp"]:
                    hit = (pos["tp"], "target")
                if hit is None and i == len(stamps) - 1:
                    hit = (bar.close, "flatten")
                if hit is None:
                    continue
                raw_exit, reason = hit
                exit_px = apply_stop_exit(raw_exit, side, costs) if reason in {"stop", "flatten"} else raw_exit
                net = pnl_per_share(side, pos["fill"], exit_px, costs)
                pnl = net * pos["qty"]
                frictionless = side * (raw_exit - pos["raw_open"]) * pos["qty"]
                cost_r = ((frictionless - pnl) / (pos["stop_dist"] * pos["qty"])) if pos["stop_dist"] else 0.0
                equity += pnl
                day_pnl += pnl
                trades.append({"pnl": pnl, "cost_r": cost_r, "reason": reason})
                del open_pos[symbol]
            if i < 20 or day_pnl <= -portfolio.daily_loss_limit * portfolio.equity:
                continue
            if len(open_pos) >= portfolio.max_positions:
                continue
            for symbol, bars in ordered.items():
                if symbol in open_pos:
                    continue
                window = [bar for bar in bars if bar.ts <= ts][-40:]
                sig = day_pullback(symbol, window, regime, cfg, day)
                if sig is None or i + 1 >= len(stamps):
                    continue
                nxt = lookup[symbol].get(stamps[i + 1])
                if nxt is None:
                    continue
                side = 1 if sig.side == "long" else -1
                fill = apply_entry(nxt.open, side, costs)
                stop = sig.stop
                if (side == 1 and fill <= stop) or (side == -1 and fill >= stop):
                    continue
                stop_dist = abs(fill - stop)
                qty = int(min(equity * portfolio.risk_pct / stop_dist, equity * portfolio.max_pos_frac / fill))
                if qty < 1:
                    continue
                risk = abs(fill - stop)
                target = fill + sig.target_r * risk if side == 1 else fill - sig.target_r * risk
                open_pos[symbol] = {
                    "side_sign": side,
                    "fill": fill,
                    "raw_open": nxt.open,
                    "stop": stop,
                    "tp": target,
                    "qty": qty,
                    "stop_dist": stop_dist,
                }
                if len(open_pos) >= portfolio.max_positions:
                    break
        curve.append(equity)
    out = _metrics(trades, portfolio.equity, equity, curve)
    out.update({"mode": "day", "expert": "day_pullback", "log_only": False})
    return out


def _alpaca_rows(daily, minute, cfg, costs, portfolio, oos_start: int) -> list[dict]:
    spy = daily["SPY"]
    oos_dates = {bar_session_date(bar.ts) for bar in spy[oos_start:]}
    return [
        _simulate_daily("swing", "swing_ema", _pick_ema, daily, spy, cfg, costs, portfolio, oos_start),
        _simulate_daily("swing", "swing_donchian", _pick_donchian, daily, spy, cfg, costs, portfolio, oos_start),
        _simulate_day_real(minute, spy, cfg, costs, portfolio, oos_dates),
    ]


def run_alpaca_backtest(
    *,
    months: int = 3,
    cost_mult: float = 2.0,
    symbols: tuple[str, ...] = ALPACA_SYMBOLS,
    now: datetime | None = None,
) -> dict:
    """Last ``months`` of out-of-sample IEX bars.

    Daily history includes warmup for the 200-day average. Five-minute bars cover
    only the out-of-sample window. Experts are swing_ema, swing_donchian, and day_pullback.
    """
    from .datafeed import AlpacaData
    from .envfile import load_keys

    months = max(1, min(6, int(months)))
    now = now or datetime.now(ET)
    api_key, secret = load_keys()
    cfg = Config()
    cfg.universe = list(symbols)
    data = AlpacaData(api_key, secret, cfg)
    # ~14 months of daily bars so the 50/200 regime is defined; the scored window is still `months`.
    daily_start = now - timedelta(days=460)
    intra_start = now - timedelta(days=months * 31 + 5)
    end = now.astimezone(timezone.utc)
    daily = data.history(list(symbols), "day", daily_start, end, now)
    minute = data.history(list(symbols), "5", intra_start, end, now)
    spy = daily.get("SPY") or []
    if len(spy) < 30:
        raise RuntimeError("SPY daily history is too short")
    oos_days = min(len(spy), months * 21)
    oos_start = max(0, len(spy) - oos_days)
    harness = btcosts.load_external() or btcosts
    costs = harness.Costs()
    portfolio = harness.Portfolio()
    rows: list[dict] = []
    for mult in _cost_mults(cost_mult):
        rows.extend(_stamp_rows(_alpaca_rows(daily, minute, cfg, scale_costs(costs, mult), portfolio, oos_start), mult))
    return _result(
        costs,
        portfolio,
        "alpaca",
        oos_days,
        symbols,
        rows,
        extra={"months": months, "experts": list(ALPACA_EXPERTS), "daily_bars": len(spy), "warmup": "sma200"},
    )


def write_report(result: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, default=str))
