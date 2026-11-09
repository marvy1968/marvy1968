"""Entry modules.

Live in an up regime: day pullback (long) and swing (long).
Live in a down regime: the same modules, short.
Flat in chop for those modules.

Log-only unless the config flag is on: scalp, VWAP ORB, VWAP reclaim,
Velez momentum, mean reversion, and the chop range-box.
No half-size scale-out and no 1R take-profit are produced here.
"""

from __future__ import annotations

from datetime import date, datetime

from .config import Config
from .indicators import atr, ema_series, overextended, pstdev, session_vwap_std, sma
from .types import Bar, MarketBundle, Regime, Signal


def _id(symbol: str, expert: str, side: str, ts: datetime) -> str:
    return f"{symbol}-{expert}-{side}-{ts.strftime('%Y%m%d%H%M')}"


def _mk(
    symbol: str,
    side: str,
    mode: str,
    expert: str,
    entry: float,
    stop: float,
    target: float | None,
    target_r: float,
    log_only: bool,
    reason: str,
    ts: datetime,
) -> Signal | None:
    if entry <= 0 or stop <= 0:
        return None
    if side == "long" and stop >= entry:
        return None
    if side == "short" and stop <= entry:
        return None
    if target is not None:
        if side == "long" and target <= entry:
            return None
        if side == "short" and target >= entry:
            return None
    return Signal(
        symbol=symbol,
        side=side,
        mode=mode,
        expert=expert,
        signal_id=_id(symbol, expert, side, ts),
        entry=entry,
        stop=stop,
        target=target,
        target_r=target_r,
        log_only=log_only,
        reason=reason,
        bar_ts=ts,
    )


def day_pullback(symbol: str, bars: list[Bar], regime: Regime, cfg: Config, session_date: date) -> Signal | None:
    if not cfg.day.enabled or regime.name not in ("up", "down"):
        return None
    if len(bars) < max(cfg.day.ema, cfg.day.atr_period) + 3:
        return None
    side = "long" if regime.name == "up" else "short"
    closes = [b.close for b in bars]
    emas = ema_series(closes, cfg.day.ema)
    if emas[-1] is None or emas[-3] is None or emas[-2] is None:
        return None
    width = atr(bars, cfg.day.atr_period)
    if width is None or width <= 0:
        return None
    vwap, std = session_vwap_std(bars, session_date)
    close = bars[-1].close
    if overextended(close, vwap, std, cfg.day.vwap_ext_std):
        return None
    if side == "long":
        rising = emas[-1] > emas[-3]
        touched = bars[-2].low <= emas[-2]
        reclaimed = close > emas[-1]
        with_vwap = vwap is not None and close > vwap
        if not (rising and touched and reclaimed and with_vwap):
            return None
        stop = close - cfg.day.atr_mult * width
    else:
        falling = emas[-1] < emas[-3]
        touched = bars[-2].high >= emas[-2]
        reclaimed = close < emas[-1]
        with_vwap = vwap is not None and close < vwap
        if not (falling and touched and reclaimed and with_vwap):
            return None
        stop = close + cfg.day.atr_mult * width
    return _mk(symbol, side, "day", "day_pullback", close, stop, None, cfg.day.target_r, False, "ema_pullback", bars[-1].ts)


def swing_ema(symbol: str, bars: list[Bar], regime: Regime, cfg: Config) -> Signal | None:
    if not cfg.swing.enabled or regime.name not in ("up", "down"):
        return None
    need = max(cfg.swing.ema_trend, cfg.regime.atr_period) + 2
    if len(bars) < need:
        return None
    side = "long" if regime.name == "up" else "short"
    closes = [b.close for b in bars]
    fast = ema_series(closes, cfg.swing.ema_exit)
    slow = ema_series(closes, cfg.swing.ema_trend)
    if fast[-1] is None or fast[-2] is None or slow[-1] is None:
        return None
    width = atr(bars, cfg.regime.atr_period)
    if width is None or width <= 0:
        return None
    close = bars[-1].close
    prev = bars[-2].close
    if side == "long":
        cross = prev <= fast[-2] and close > fast[-1] and close > slow[-1]
        stop = close - cfg.swing.atr_mult * width
    else:
        cross = prev >= fast[-2] and close < fast[-1] and close < slow[-1]
        stop = close + cfg.swing.atr_mult * width
    if not cross:
        return None
    risk = abs(close - stop)
    target = close + cfg.swing.target_r * risk if side == "long" else close - cfg.swing.target_r * risk
    return _mk(symbol, side, "swing", "swing_ema", close, stop, target, cfg.swing.target_r, False, "ema_cross", bars[-1].ts)


def donchian(symbol: str, bars: list[Bar], regime: Regime, cfg: Config) -> Signal | None:
    """55-day breakout, 1.5 ATR stop. The 20-day channel is an exit, not the target."""
    if not cfg.swing.enabled or regime.name not in ("up", "down"):
        return None
    entry_n = cfg.swing.donchian_entry
    if len(bars) < entry_n + cfg.regime.atr_period + 1:
        return None
    side = "long" if regime.name == "up" else "short"
    width = atr(bars, cfg.regime.atr_period)
    if width is None or width <= 0:
        return None
    window = bars[-(entry_n + 1):-1]
    close = bars[-1].close
    if side == "long":
        if close <= max(b.high for b in window):
            return None
        stop = close - cfg.swing.donchian_atr_mult * width
    else:
        if close >= min(b.low for b in window):
            return None
        stop = close + cfg.swing.donchian_atr_mult * width
    return _mk(
        symbol, side, "swing", "swing_donchian", close, stop, None, cfg.swing.target_r, False, "donchian_55", bars[-1].ts
    )


def mean_reversion(symbol: str, bars: list[Bar], cfg: Config) -> Signal | None:
    """Buy the reclaim of -2.5 sigma. Stop -3.5 sigma. Target the 50-day average. Log-only by default."""
    if len(bars) < 52:
        return None
    hist = bars[:-1]
    closes = [b.close for b in hist]
    ma = sma(closes, 50)
    sigma = pstdev(closes[-20:])
    if ma is None or sigma <= 0:
        return None
    band = ma - 2.5 * sigma
    if hist[-1].close >= band or bars[-1].close <= band:
        return None
    stop = ma - 3.5 * sigma
    target = ma
    sig = _mk(
        symbol,
        "long",
        "swing",
        "mean_reversion",
        bars[-1].close,
        stop,
        target,
        cfg.swing.target_r,
        not cfg.modules.mean_reversion,
        "reclaim_2.5s",
        bars[-1].ts,
    )
    return sig


def range_box(symbol: str, bars: list[Bar], regime: Regime, cfg: Config) -> Signal | None:
    """Mean-revert inside a 20-day box. Chop only. Log-only by default."""
    if regime.name != "chop" or len(bars) < 21:
        return None
    window = bars[-20:]
    hi = max(b.high for b in window)
    lo = min(b.low for b in window)
    width = hi - lo
    if width <= 0:
        return None
    close = bars[-1].close
    mid = (hi + lo) / 2.0
    log_only = not cfg.modules.range_box
    if close <= lo + 0.15 * width and close > bars[-2].close:
        return _mk(symbol, "long", "swing", "range_box", close, lo - 0.10 * width, mid, cfg.swing.target_r, log_only, "box_low", bars[-1].ts)
    if close >= hi - 0.15 * width and close < bars[-2].close:
        return _mk(symbol, "short", "swing", "range_box", close, hi + 0.10 * width, mid, cfg.swing.target_r, log_only, "box_high", bars[-1].ts)
    return None


def vwap_orb(symbol: str, bars: list[Bar], regime: Regime, cfg: Config, session_date: date) -> Signal | None:
    if regime.name not in ("up", "down") or len(bars) < 8:
        return None
    todays = [b for b in bars if _same_day(b, session_date)]
    if len(todays) < 4:
        return None
    opening = todays[:3]
    or_high = max(b.high for b in opening)
    or_low = min(b.low for b in opening)
    vwap, std = session_vwap_std(bars, session_date)
    close = bars[-1].close
    if overextended(close, vwap, std, cfg.day.vwap_ext_std):
        return None
    width = atr(bars, cfg.day.atr_period) or (or_high - or_low)
    log_only = not cfg.modules.vwap_orb
    if regime.name == "up" and close > or_high and vwap is not None and close > vwap:
        return _mk(symbol, "long", "day", "vwap_orb", close, or_low, None, cfg.day.target_r, log_only, "orb", bars[-1].ts)
    if regime.name == "down" and close < or_low and vwap is not None and close < vwap:
        return _mk(symbol, "short", "day", "vwap_orb", close, or_high, None, cfg.day.target_r, log_only, "orb", bars[-1].ts)
    return None


def vwap_reclaim(symbol: str, bars: list[Bar], regime: Regime, cfg: Config, session_date: date) -> Signal | None:
    if regime.name not in ("up", "down") or len(bars) < 6:
        return None
    vwap, std = session_vwap_std(bars, session_date)
    if vwap is None:
        return None
    if overextended(bars[-1].close, vwap, std, cfg.day.vwap_ext_std):
        return None
    width = atr(bars, cfg.day.atr_period)
    if width is None:
        return None
    log_only = not cfg.modules.vwap_reclaim
    prev, close = bars[-2].close, bars[-1].close
    if regime.name == "up" and prev < vwap <= close:
        return _mk(symbol, "long", "day", "vwap_reclaim", close, close - cfg.day.atr_mult * width, None, cfg.day.target_r, log_only, "vwap_reclaim", bars[-1].ts)
    if regime.name == "down" and prev > vwap >= close:
        return _mk(symbol, "short", "day", "vwap_reclaim", close, close + cfg.day.atr_mult * width, None, cfg.day.target_r, log_only, "vwap_reclaim", bars[-1].ts)
    return None


def velez(symbol: str, bars: list[Bar], regime: Regime, cfg: Config, session_date: date) -> Signal | None:
    """Wide momentum bar closing near its extreme, with the session VWAP."""
    if regime.name not in ("up", "down") or len(bars) < cfg.day.atr_period + 2:
        return None
    width = atr(bars[:-1], cfg.day.atr_period)
    if width is None or width <= 0:
        return None
    bar = bars[-1]
    if (bar.high - bar.low) < 1.5 * width:
        return None
    span = bar.high - bar.low
    vwap, std = session_vwap_std(bars, session_date)
    if overextended(bar.close, vwap, std, cfg.day.vwap_ext_std):
        return None
    log_only = not cfg.modules.velez
    if regime.name == "up" and vwap is not None and bar.close > vwap and (bar.high - bar.close) <= 0.25 * span:
        return _mk(symbol, "long", "day", "velez", bar.close, bar.low, None, cfg.day.target_r, log_only, "velez_mom", bar.ts)
    if regime.name == "down" and vwap is not None and bar.close < vwap and (bar.close - bar.low) <= 0.25 * span:
        return _mk(symbol, "short", "day", "velez", bar.close, bar.high, None, cfg.day.target_r, log_only, "velez_mom", bar.ts)
    return None


def scalp(symbol: str, bars: list[Bar], regime: Regime, cfg: Config, session_date: date) -> Signal | None:
    """Same reclaim idea on the scalp timeframe. Log-only unless scalp.enabled."""
    sig = vwap_reclaim(symbol, bars, regime, cfg, session_date)
    if sig is None:
        return None
    sig.mode = "scalp"
    sig.expert = "scalp"
    sig.log_only = not cfg.scalp.enabled
    sig.target_r = cfg.scalp.target_r
    sig.signal_id = _id(symbol, "scalp", sig.side, sig.bar_ts)
    return sig


def _same_day(bar: Bar, session_date: date) -> bool:
    from .clock import bar_session_date

    return bar_session_date(bar.ts) == session_date


def donchian_exit(bars: list[Bar], side: str, exit_n: int) -> bool:
    if len(bars) < exit_n + 1:
        return False
    window = bars[-(exit_n + 1):-1]
    close = bars[-1].close
    if side == "long":
        return close < min(b.low for b in window)
    return close > max(b.high for b in window)


def swing_hold_exit(bars: list[Bar], side: str, expert: str, opened_day: date, cfg: Config) -> str | None:
    if not bars:
        return None
    from .clock import bar_session_date

    held = _weekdays_between(opened_day, bar_session_date(bars[-1].ts))
    if expert == "topdown":
        closes = [b.close for b in bars]
        line = ema_series(closes, cfg.topdown.ema)
        if line[-1] is None:
            return None
        if side == "long" and bars[-1].close < line[-1]:
            return "ema"
        if side == "short" and bars[-1].close > line[-1]:
            return "ema"
        return None
    if held >= cfg.swing.max_hold_days:
        return "max_hold"
    if expert == "swing_donchian" and donchian_exit(bars, side, cfg.swing.donchian_exit):
        return "donchian"
    if expert == "swing_ema":
        closes = [b.close for b in bars]
        line = ema_series(closes, cfg.swing.ema_exit)
        if line[-1] is None:
            return None
        if side == "long" and bars[-1].close < line[-1]:
            return "ema"
        if side == "short" and bars[-1].close > line[-1]:
            return "ema"
    return None


def _weekdays_between(start: date, end: date) -> int:
    if end <= start:
        return 0
    days = 0
    cursor = start
    from datetime import timedelta

    while cursor < end:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            days += 1
    return days


def topdown(symbol: str, bars: list[Bar], spy: list[Bar], cfg: Config) -> Signal | None:
    """Daily long: close > EMA20 > SMA50, 20-day return beats SPY by >= 3 points, SPY above its 200-day average.

    Stop is the 20-day EMA minus 1 ATR. The bracket target is 2R. The position
    also exits on a later daily close below the 20-day EMA.
    """
    from .clock import bar_session_date

    need = max(cfg.topdown.sma, cfg.topdown.ema, cfg.topdown.atr_period) + 21
    if len(bars) < need or len(spy) < cfg.topdown.spy_sma + 1:
        return None
    closes = [b.close for b in bars]
    ema = ema_series(closes, cfg.topdown.ema)
    slow = sma(closes, cfg.topdown.sma)
    width = atr(bars, cfg.topdown.atr_period)
    if ema[-1] is None or slow is None or width is None or width <= 0:
        return None
    spy_by_day = {bar_session_date(b.ts): b.close for b in spy}
    day = bar_session_date(bars[-1].ts)
    day20 = bar_session_date(bars[-21].ts)
    if day not in spy_by_day or day20 not in spy_by_day:
        return None
    spy_hist = [b for b in spy if bar_session_date(b.ts) <= day]
    spy_ma = sma([b.close for b in spy_hist], cfg.topdown.spy_sma)
    if spy_ma is None or spy_by_day[day] <= spy_ma:
        return None
    if not (closes[-1] > ema[-1] > slow):
        return None
    rs = (closes[-1] / closes[-21] - 1.0) - (spy_by_day[day] / spy_by_day[day20] - 1.0)
    if rs < cfg.topdown.rs_margin:
        return None
    stop = ema[-1] - cfg.topdown.atr_mult * width
    return _mk(
        symbol,
        "long",
        "swing",
        "topdown",
        closes[-1],
        stop,
        None,
        cfg.topdown.target_r,
        not cfg.topdown.enabled,
        "topdown",
        bars[-1].ts,
    )


def generate_signals(bundle: MarketBundle, cfg: Config, regime: Regime, session_date: date) -> list[Signal]:
    out: list[Signal] = []
    spy = bundle.daily.get("SPY") or []
    for symbol in cfg.universe:
        daily = bundle.daily.get(symbol) or []
        m5 = bundle.min5.get(symbol) or []
        m1 = bundle.min1.get(symbol) or []
        if daily:
            td = topdown(symbol, daily, spy, cfg)
            if td is not None:
                out.append(td)
            for fn in (
                lambda s=symbol, b=daily: swing_ema(s, b, regime, cfg),
                lambda s=symbol, b=daily: donchian(s, b, regime, cfg),
                lambda s=symbol, b=daily: mean_reversion(s, b, cfg),
                lambda s=symbol, b=daily: range_box(s, b, regime, cfg),
            ):
                sig = fn()
                if sig is not None:
                    out.append(sig)
        if m5:
            for fn in (
                lambda s=symbol, b=m5: day_pullback(s, b, regime, cfg, session_date),
                lambda s=symbol, b=m5: vwap_orb(s, b, regime, cfg, session_date),
                lambda s=symbol, b=m5: vwap_reclaim(s, b, regime, cfg, session_date),
                lambda s=symbol, b=m5: velez(s, b, regime, cfg, session_date),
            ):
                sig = fn()
                if sig is not None:
                    out.append(sig)
        if m1:
            sig = scalp(symbol, m1, regime, cfg, session_date)
            if sig is not None:
                out.append(sig)
    return out
