"""Small indicator set. Inputs are completed bars only."""

from __future__ import annotations

from datetime import date

from .clock import bar_session_date
from .types import Bar


def sma(values: list[float], n: int) -> float | None:
    if n <= 0 or len(values) < n:
        return None
    window = values[-n:]
    return sum(window) / n


def ema_series(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if n <= 0 or len(values) < n:
        return out
    k = 2.0 / (n + 1.0)
    acc = sum(values[:n]) / n
    out[n - 1] = acc
    for i in range(n, len(values)):
        acc = values[i] * k + acc * (1.0 - k)
        out[i] = acc
    return out


def atr(bars: list[Bar], period: int = 14) -> float | None:
    if period <= 0 or len(bars) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(bars)):
        high, low = bars[i].high, bars[i].low
        prev = bars[i - 1].close
        trs.append(max(high - low, abs(high - prev), abs(low - prev)))
    if len(trs) < period:
        return None
    val = sum(trs[:period]) / period
    for tr in trs[period:]:
        val = (val * (period - 1) + tr) / period
    return val


def pstdev(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return var ** 0.5


def session_vwap_std(bars: list[Bar], session_date: date) -> tuple[float | None, float | None]:
    """Session VWAP and the stdev of close-minus-VWAP. Used as a no-chase band."""
    todays = [b for b in bars if bar_session_date(b.ts) == session_date]
    if not todays:
        return None, None
    num = 0.0
    den = 0.0
    diffs: list[float] = []
    vwap = None
    for bar in todays:
        typical = (bar.high + bar.low + bar.close) / 3.0
        vol = bar.volume if bar.volume > 0 else 1.0
        num += typical * vol
        den += vol
        vwap = num / den
        diffs.append(bar.close - vwap)
    if vwap is None:
        return None, None
    return vwap, pstdev(diffs)


def overextended(close: float, vwap: float | None, std: float | None, max_std: float) -> bool:
    if vwap is None or std is None or std <= 0:
        return False
    return abs(close - vwap) > max_std * std


def rvol(bars: list[Bar], lookback: int = 20) -> float | None:
    """Last completed bar's volume divided by the mean of the prior `lookback` bars."""
    if len(bars) < lookback + 1:
        return None
    base = [b.volume for b in bars[-(lookback + 1):-1]]
    avg = sum(base) / len(base)
    if avg <= 0:
        return None
    return bars[-1].volume / avg
