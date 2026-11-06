"""Pre-registered IEX validation. The pass rule below was frozen before any result.

A study that fails stays off. This module does not edit config and does not
pass ``--paper``. Switches that fail, or that were not run, stay disabled.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

from .bars import MIN_DAILY_BARS
from .btcosts import Costs, apply_entry, apply_stop_exit, pnl_per_share, scale_costs
from .clock import ET, bar_session_date
from .config import Config
from .indicators import ema_series, sma
from .spyopen import spy_open_bias, spy_open_decision
from .universe import sector_of

# Frozen 2026-11-06, before any Alpaca IEX validation result was inspected.
# Do not relax these after seeing a table.
PASS_RULE = {
    "id": "cos1-validate-2026-11-06",
    "split": "chronological half of the scored window; the second half is unseen",
    "min_trades_unseen": 30,
    "unseen_pf_gt": 1.0,
    "unseen_avg_r_gt": 0.0,
    "seen_pf_gt": 1.0,
    "seen_avg_r_gt": 0.0,
    "unseen_x2_pf_gt": 1.0,
    "beat_random": "unseen average R must be greater than random-timing average R",
    "random_seed": 20261106,
    "on_fail": "leave the switch off; do not run --paper for that study",
}

TOPDOWN_START = date(2016, 1, 1)
INTRADAY_MONTHS = 3


def _pf(pnls: list[float]) -> float | None:
    wins = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    if not pnls:
        return None
    if losses == 0:
        return None if wins == 0 else float("inf")
    return wins / losses


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _gt(value: float | None, floor: float) -> bool:
    return value is not None and value > floor


def judge(seen: list[dict], unseen: list[dict], unseen_x2: list[dict], random_avg_r: float | None) -> dict:
    """Apply PASS_RULE. ``seen`` / ``unseen`` trades need ``pnl`` and ``r``."""
    failures = []
    if len(unseen) < PASS_RULE["min_trades_unseen"]:
        failures.append(f"unseen trades {len(unseen)} < {PASS_RULE['min_trades_unseen']}")
    unseen_pf = _pf([t["pnl"] for t in unseen])
    unseen_r = _avg([t["r"] for t in unseen])
    seen_pf = _pf([t["pnl"] for t in seen])
    seen_r = _avg([t["r"] for t in seen])
    x2_pf = _pf([t["pnl"] for t in unseen_x2])
    if not _gt(unseen_pf, PASS_RULE["unseen_pf_gt"]):
        failures.append("unseen profit factor")
    if not _gt(unseen_r, PASS_RULE["unseen_avg_r_gt"]):
        failures.append("unseen average R")
    if not _gt(seen_pf, PASS_RULE["seen_pf_gt"]):
        failures.append("seen half")
    if not _gt(seen_r, PASS_RULE["seen_avg_r_gt"]):
        failures.append("seen average R")
    if not _gt(x2_pf, PASS_RULE["unseen_x2_pf_gt"]):
        failures.append("unseen profit factor at cost x2")
    if random_avg_r is None or unseen_r is None or not (unseen_r > random_avg_r):
        failures.append("random-timing control")
    return {
        "passed": not failures,
        "failures": failures,
        "seen_trades": len(seen),
        "unseen_trades": len(unseen),
        "seen_pf": seen_pf,
        "unseen_pf": unseen_pf,
        "seen_avg_r": seen_r,
        "unseen_avg_r": unseen_r,
        "unseen_x2_pf": x2_pf,
        "random_avg_r": random_avg_r,
        "paper": False,
    }


def _split(trades: list[dict], midpoint: date) -> tuple[list[dict], list[dict]]:
    seen = [t for t in trades if t["entry_day"] < midpoint]
    unseen = [t for t in trades if t["entry_day"] >= midpoint]
    return seen, unseen


def _midpoint(days: list[date]) -> date:
    ordered = sorted(set(days))
    return ordered[len(ordered) // 2]


def _prep_stock(bars: list):
    bars = sorted(bars, key=lambda bar: bar.ts)
    closes = [bar.close for bar in bars]
    ema = ema_series(closes, 20)
    atrs = [None] * len(bars)
    if len(bars) > 15:
        # Wilder ATR aligned to the bar index.
        trs = []
        for i in range(1, len(bars)):
            prev = bars[i - 1].close
            trs.append(max(bars[i].high - bars[i].low, abs(bars[i].high - prev), abs(bars[i].low - prev)))
        val = sum(trs[:14]) / 14
        atrs[14] = val
        for i, tr in enumerate(trs[14:], start=15):
            val = (val * 13 + tr) / 14
            atrs[i] = val
    return bars, closes, ema, atrs


def _spy_map(spy: list):
    ordered = sorted(spy, key=lambda bar: bar.ts)
    closes = [bar.close for bar in ordered]
    return ordered, {bar_session_date(bar.ts): bar.close for bar in ordered}, closes


def simulate_topdown(daily: dict, costs: Costs, equity0: float = 100_000.0) -> list[dict]:
    """Top-down longs. One per sector, at most 5 open, stop new entries after -1% on the day."""
    cfg = Config()
    spy = daily.get("SPY") or []
    if len(spy) < MIN_DAILY_BARS:
        return []
    spy_bars, spy_by_day, spy_closes = _spy_map(spy)
    spy_ma200 = [None] * len(spy_closes)
    if len(spy_closes) >= 200:
        acc = sum(spy_closes[:200])
        spy_ma200[199] = acc / 200
        for i in range(200, len(spy_closes)):
            acc += spy_closes[i] - spy_closes[i - 200]
            spy_ma200[i] = acc / 200
    prepared = {symbol: _prep_stock(bars) for symbol, bars in daily.items() if symbol != "SPY"}
    dates = [bar_session_date(bar.ts) for bar in spy_bars]
    equity = equity0
    open_pos: dict[str, dict] = {}
    trades = []
    start = MIN_DAILY_BARS
    for i in range(start, len(spy_bars) - 1):
        day = dates[i]
        nxt = dates[i + 1]
        day_start = equity
        day_pnl = 0.0
        for symbol, pos in list(open_pos.items()):
            bars, closes, ema, atrs = prepared[symbol]
            today = pos["index"].get(day)
            if today is None:
                continue
            bar = bars[today]
            side = 1
            reason = None
            raw_exit = None
            if bar.open <= pos["stop"]:
                raw_exit, reason = bar.open, "stop"
            elif bar.low <= pos["stop"]:
                raw_exit, reason = pos["stop"], "stop"
            elif bar.high >= pos["tp"]:
                raw_exit, reason = pos["tp"], "target"
            elif ema[today] is not None and closes[today] < ema[today] and i + 1 < len(dates):
                nxt_i = pos["index"].get(nxt)
                if nxt_i is not None:
                    raw_exit, reason = bars[nxt_i].open, "ema"
            if reason is None:
                continue
            exit_px = apply_stop_exit(raw_exit, side, costs) if reason in {"stop", "ema"} else raw_exit
            net = pnl_per_share(side, pos["fill"], exit_px, costs)
            risk = pos["stop_dist"] * pos["qty"]
            pnl = net * pos["qty"]
            equity += pnl
            day_pnl += pnl
            trades.append(
                {
                    "entry_day": pos["entry_day"],
                    "pnl": pnl,
                    "r": (pnl / risk) if risk else 0.0,
                    "symbol": symbol,
                    "reason": reason,
                }
            )
            del open_pos[symbol]
        if day_start > 0 and day_pnl <= -0.01 * day_start:
            continue
        if len(open_pos) >= 5:
            continue
        candidates = []
        sectors_open = {sector_of(sym) for sym in open_pos}
        for symbol, (bars, closes, ema, atrs) in prepared.items():
            if symbol in open_pos or sector_of(symbol) in sectors_open:
                continue
            index = {bar_session_date(bar.ts): n for n, bar in enumerate(bars)}
            today = index.get(day)
            if today is None or today < 50 or atrs[today] is None or ema[today] is None:
                continue
            slow = sma(closes[: today + 1], cfg.topdown.sma)
            if slow is None or not (closes[today] > ema[today] > slow):
                continue
            day20 = bar_session_date(bars[today - 20].ts)
            if day not in spy_by_day or day20 not in spy_by_day:
                continue
            spy_ma = spy_ma200[i]
            if spy_ma is None or spy_by_day[day] <= spy_ma:
                continue
            rs = (closes[today] / closes[today - 20] - 1.0) - (spy_by_day[day] / spy_by_day[day20] - 1.0)
            if rs < cfg.topdown.rs_margin:
                continue
            nxt_i = index.get(nxt)
            if nxt_i is None:
                continue
            candidates.append((rs, symbol, today, nxt_i, bars, ema, atrs, index))
        candidates.sort(key=lambda row: row[0], reverse=True)
        for _rs, symbol, today, nxt_i, bars, ema, atrs, index in candidates:
            if len(open_pos) >= 5 or sector_of(symbol) in sectors_open:
                continue
            raw_open = bars[nxt_i].open
            fill = apply_entry(raw_open, 1, costs)
            stop = ema[today] - cfg.topdown.atr_mult * atrs[today]
            if fill <= stop:
                continue
            stop_dist = fill - stop
            qty = int(min(equity * cfg.topdown.risk_pct / stop_dist, equity * 0.25 / fill))
            if qty < 1:
                continue
            open_pos[symbol] = {
                "fill": fill,
                "stop": stop,
                "tp": fill + cfg.topdown.target_r * stop_dist,
                "qty": qty,
                "stop_dist": stop_dist,
                "entry_day": nxt,
                "index": index,
            }
            sectors_open.add(sector_of(symbol))
    return trades


def random_topdown_avg_r(daily: dict, costs: Costs, n: int, midpoint: date, seed: int) -> float | None:
    """Same count of entries on the unseen half, random timing, top-down exits."""
    if n <= 0:
        return None
    cfg = Config()
    spy = sorted(daily.get("SPY") or [], key=lambda bar: bar.ts)
    dates = [bar_session_date(bar.ts) for bar in spy]
    prepared = {symbol: _prep_stock(bars) for symbol, bars in daily.items() if symbol != "SPY"}
    pool = []
    for symbol, (bars, closes, ema, atrs) in prepared.items():
        index = {bar_session_date(bar.ts): i for i, bar in enumerate(bars)}
        for i, day in enumerate(dates[:-1]):
            if day < midpoint:
                continue
            today = index.get(day)
            nxt = index.get(dates[i + 1])
            if today is None or nxt is None or today < 20 or atrs[today] is None or ema[today] is None:
                continue
            pool.append((symbol, today, nxt, bars, ema, atrs))
    if not pool:
        return None
    rng = random.Random(seed)
    picks = [pool[rng.randrange(len(pool))] for _ in range(n)]
    rs = []
    for symbol, today, nxt, bars, ema, atrs in picks:
        fill = apply_entry(bars[nxt].open, 1, costs)
        stop = ema[today] - atrs[today]
        if fill <= stop:
            continue
        stop_dist = fill - stop
        target = fill + cfg.topdown.target_r * stop_dist
        exit_px = None
        reason = "end"
        for j in range(nxt, len(bars)):
            bar = bars[j]
            if bar.low <= stop:
                exit_px = apply_stop_exit(bar.open if bar.open < stop else stop, 1, costs)
                reason = "stop"
                break
            if bar.high >= target:
                exit_px = target
                reason = "target"
                break
            if j + 1 < len(bars) and ema[j] is not None and bar.close < ema[j]:
                exit_px = apply_stop_exit(bars[j + 1].open, 1, costs)
                reason = "ema"
                break
        if exit_px is None:
            exit_px = apply_stop_exit(bars[-1].close, 1, costs)
        pnl = pnl_per_share(1, fill, exit_px, costs)
        rs.append(pnl / stop_dist)
        _ = reason
    return _avg(rs)


def study_topdown(daily: dict) -> dict:
    costs = Costs()
    trades = simulate_topdown(daily, costs)
    days = sorted({t["entry_day"] for t in trades})
    if not days:
        judged = judge([], [], [], None)
        judged["study"] = "topdown"
        return judged
    mid = _midpoint(days)
    seen, unseen = _split(trades, mid)
    x2 = simulate_topdown(daily, scale_costs(costs, 2))
    _, unseen_x2 = _split(x2, mid)
    random_r = random_topdown_avg_r(daily, costs, len(unseen), mid, PASS_RULE["random_seed"])
    judged = judge(seen, unseen, unseen_x2, random_r)
    judged["study"] = "topdown"
    judged["switch"] = "topdown.enabled"
    return judged


def _walk_day(minute: dict, spy_daily: list, costs: Costs, use_spy: bool) -> list[dict]:
    """day_pullback over real 5-minute sessions. ``use_spy`` applies the open filter."""
    from .regime import classify_regime
    from .signals import day_pullback

    cfg = Config()
    spy = sorted(spy_daily, key=lambda bar: bar.ts)
    grouped: dict[str, dict] = {}
    for symbol, bars in minute.items():
        by_day: dict = {}
        for bar in bars:
            by_day.setdefault(bar_session_date(bar.ts), []).append(bar)
        for day_bars in by_day.values():
            day_bars.sort(key=lambda bar: bar.ts)
        grouped[symbol] = by_day
    days = sorted({day for by_day in grouped.values() for day in by_day})
    trades = []
    for day in days:
        hist = [bar for bar in spy if bar_session_date(bar.ts) < day]
        if len(hist) < MIN_DAILY_BARS:
            continue
        regime = classify_regime(hist, cfg.regime)
        spy_today = grouped.get("SPY", {}).get(day, [])
        for symbol, by_day in grouped.items():
            if symbol == "SPY":
                continue
            bars = by_day.get(day) or []
            if len(bars) < 31:
                continue
            sig = day_pullback(symbol, bars[:30], regime, cfg, day)
            if sig is None:
                continue
            ok, _why = spy_open_decision(sig.side, spy_open_bias(spy_today, day), use_spy)
            if not ok:
                continue
            trade = _resolve_intraday(sig, bars[30:], costs, day)
            if trade is not None:
                trades.append(trade)
    return trades


def _resolve_intraday(sig, rest: list, costs: Costs, day: date) -> dict | None:
    if not rest:
        return None
    side = 1 if sig.side == "long" else -1
    fill = apply_entry(rest[0].open, side, costs)
    risk = abs(fill - sig.stop)
    if risk <= 0:
        return None
    target = fill + side * sig.target_r * risk
    stop = sig.stop
    exit_px = apply_stop_exit(rest[-1].close, side, costs)
    for bar in rest:
        if side == 1 and bar.low <= stop:
            exit_px = apply_stop_exit(bar.open if bar.open < stop else stop, side, costs)
            break
        if side == -1 and bar.high >= stop:
            exit_px = apply_stop_exit(bar.open if bar.open > stop else stop, side, costs)
            break
        if (side == 1 and bar.high >= target) or (side == -1 and bar.low <= target):
            exit_px = target
            break
    pnl_ps = pnl_per_share(side, fill, exit_px, costs)
    return {"entry_day": day, "pnl": pnl_ps, "r": pnl_ps / risk, "symbol": sig.symbol}


def _random_day_avg_r(minute: dict, n: int, midpoint: date, costs: Costs, seed: int) -> float | None:
    """Same count of unseen entries at bar 30, ignoring the pullback signal. Stop is 1.5% ."""
    if n <= 0:
        return None
    pool = []
    for symbol, bars in minute.items():
        if symbol == "SPY":
            continue
        by_day: dict = {}
        for bar in bars:
            by_day.setdefault(bar_session_date(bar.ts), []).append(bar)
        for day, day_bars in by_day.items():
            if day < midpoint or len(day_bars) < 31:
                continue
            day_bars.sort(key=lambda bar: bar.ts)
            pool.append((day, day_bars))
    if not pool:
        return None
    rng = random.Random(seed)
    picked = [pool[rng.randrange(len(pool))] for _ in range(n)]
    rs = []
    for day, day_bars in picked:
        rest = day_bars[30:]
        raw = rest[0].open
        fill = apply_entry(raw, 1, costs)
        stop = fill * 0.985
        risk = fill - stop
        target = fill + 2.75 * risk
        exit_px = apply_stop_exit(rest[-1].close, 1, costs)
        for bar in rest:
            if bar.low <= stop:
                exit_px = apply_stop_exit(bar.open if bar.open < stop else stop, 1, costs)
                break
            if bar.high >= target:
                exit_px = target
                break
        rs.append(pnl_per_share(1, fill, exit_px, costs) / risk)
    return _avg(rs)


def study_day(minute: dict, spy_daily: list, use_spy: bool, name: str) -> dict:
    costs = Costs()
    trades = _walk_day(minute, spy_daily, costs, use_spy)
    x2 = _walk_day(minute, spy_daily, scale_costs(costs, 2), use_spy)
    days = sorted({t["entry_day"] for t in trades})
    if not days:
        judged = judge([], [], [], None)
        judged["study"] = name
        judged["switch"] = "spy_open.enabled" if use_spy else "day.enabled"
        return judged
    mid = _midpoint(days)
    seen, unseen = _split(trades, mid)
    _, unseen_x2 = _split(x2, mid)
    random_r = _random_day_avg_r(minute, len(unseen), mid, costs, PASS_RULE["random_seed"])
    judged = judge(seen, unseen, unseen_x2, random_r)
    judged["study"] = name
    judged["switch"] = "spy_open.enabled" if use_spy else "day.enabled"
    judged["paper"] = False
    return judged


def not_run(study: str, reason: str) -> dict:
    return {"study": study, "status": "not_run", "reason": reason, "passed": False, "paper": False, "failures": [reason]}


def format_table(rows: list[dict]) -> str:
    lines = [
        f"pass_rule={PASS_RULE['id']} min_unseen={PASS_RULE['min_trades_unseen']} "
        f"pf>{PASS_RULE['unseen_pf_gt']} avg_r>{PASS_RULE['unseen_avg_r_gt']} "
        f"x2_pf>{PASS_RULE['unseen_x2_pf_gt']} beat_random seed={PASS_RULE['random_seed']}",
        f"{'study':<28} {'seen':>5} {'unseen':>6} {'pf':>8} {'avgR':>8} {'x2pf':>8} {'randR':>8} pass paper",
    ]
    for row in rows:
        if row.get("status") == "not_run":
            lines.append(f"{row['study']:<28} NOT RUN  {row.get('reason', '')}  pass=no paper=no")
            continue
        pf = row.get("unseen_pf")
        pf_s = "n/a" if pf is None else ("inf" if pf == float("inf") else f"{pf:.2f}")
        x2 = row.get("unseen_x2_pf")
        x2_s = "n/a" if x2 is None else ("inf" if x2 == float("inf") else f"{x2:.2f}")
        avg = row.get("unseen_avg_r")
        rnd = row.get("random_avg_r")
        lines.append(
            f"{row['study']:<28} {row.get('seen_trades', 0):>5} {row.get('unseen_trades', 0):>6} "
            f"{pf_s:>8} {'n/a' if avg is None else f'{avg:.3f}':>8} {x2_s:>8} "
            f"{'n/a' if rnd is None else f'{rnd:.3f}':>8} {str(bool(row.get('passed'))):>5} no"
        )
        if row.get("failures"):
            lines.append("  fail: " + "; ".join(row["failures"]))
    return "\n".join(lines)


def run_validation(now: datetime | None = None) -> dict:
    """Fetch IEX and score the frozen rule. Missing keys leave every new switch off."""
    from .datafeed import AlpacaData
    from .envfile import load_keys
    from .errors import MissingCredentials

    now = now or datetime.now(ET)
    news_reason = "no per-day headline archive; a live snapshot is not a backtest"
    try:
        api_key, secret = load_keys()
    except MissingCredentials:
        return {
            "pass_rule": PASS_RULE,
            "paper": False,
            "rows": [
                not_run("topdown", "MissingCredentials"),
                not_run("day_pullback", "MissingCredentials"),
                not_run("day_pullback_spy_open", "MissingCredentials"),
                not_run("day_pullback_news", news_reason),
                not_run("day_pullback_both", news_reason),
            ],
        }
    cfg = Config()
    data = AlpacaData(api_key, secret, cfg)
    symbols = ["SPY", *cfg.universe]
    end = now.astimezone(timezone.utc)
    try:
        daily = data.history(symbols, "day", datetime.combine(TOPDOWN_START, datetime.min.time(), tzinfo=ET), end, now)
        minute = data.history(symbols, "5", now - timedelta(days=INTRADAY_MONTHS * 31 + 5), end, now)
    except Exception as exc:
        reason = type(exc).__name__
        return {
            "pass_rule": PASS_RULE,
            "paper": False,
            "rows": [not_run(name, reason) for name in ("topdown", "day_pullback", "day_pullback_spy_open")]
            + [not_run("day_pullback_news", news_reason), not_run("day_pullback_both", news_reason)],
        }
    rows = [
        study_topdown(daily),
        study_day(minute, daily.get("SPY") or [], False, "day_pullback"),
        study_day(minute, daily.get("SPY") or [], True, "day_pullback_spy_open"),
        not_run("day_pullback_news", news_reason),
        not_run("day_pullback_both", news_reason),
    ]
    for row in rows:
        row["paper"] = False
    return {"pass_rule": PASS_RULE, "rows": rows, "paper": False}

