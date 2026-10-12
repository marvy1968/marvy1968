"""Sector & market heat map: the pre-trade direction gate.

Daily state per ETF, from completed daily bars only (the state used on day D comes from the close of D-1):
  BULLISH (+1)  EMA(9) > EMA(21) and 20-day return > +0.5 of its own 20-day volatility
  BEARISH (-1)  EMA(9) < EMA(21) and 20-day return < -0.5 of its own 20-day volatility
  NEUTRAL (0)   otherwise
Volatility-scaled, so XLU and XLK are judged on the same footing.

Gate columns added to every 5-minute day frame:
  g_long_ok    the symbol's sector is not BEARISH (longs into a falling sector are blocked)
  g_short_ok   the market (SPY) OR the symbol's sector is BEARISH (shorting is allowed only then)
  g_spy_red    SPY is red right now: below today's open and below its session VWAP (as of that completed bar)
"""

import numpy as np
import pandas as pd

from .indicators import ema, session_vwap

SECTOR = {"AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AMD": "XLK", "AMZN": "XLY", "TSLA": "XLY", "META": "XLC",
          "SPY": "SPY", "QQQ": "QQQ", "IWM": "IWM"}
MARKET = "SPY"
HEAT_ETFS = sorted(set(SECTOR.values()) | {MARKET})


def heat_state(daily: pd.DataFrame) -> pd.Series:
    """date -> -1/0/+1, usable on that date (computed from the PREVIOUS close)."""
    if daily is None or len(daily) < 25:
        return pd.Series(dtype=float)
    c = daily["close"].astype(float)
    e9, e21 = ema(c, 9), ema(c, 21)
    roc = c / c.shift(20) - 1
    vol = c.pct_change().rolling(20, min_periods=20).std() * np.sqrt(20)
    z = roc / vol.replace(0, np.nan)
    st = pd.Series(np.where((e9 > e21) & (z > 0.5), 1, np.where((e9 < e21) & (z < -0.5), -1, 0)), index=c.index, dtype=float)
    st[z.isna()] = 0
    st = st.shift(1)
    st.index = pd.Index([t.date() for t in st.index])
    return st.dropna()


def heat_map(daily: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
    return {sym: heat_state(daily.get(sym)) for sym in HEAT_ETFS}


def spy_red(spy_day: pd.DataFrame | None) -> pd.Series | None:
    if spy_day is None or not len(spy_day):
        return None
    vw = spy_day["f_vwap"] if "f_vwap" in spy_day else session_vwap(spy_day)
    return (spy_day["close"] < spy_day["open"].iloc[0]) & (spy_day["close"] < vw)


def apply(d: pd.DataFrame, symbol: str, heat: dict[str, pd.Series], spy_day: pd.DataFrame | None = None) -> pd.DataFrame:
    """Add g_* columns to one symbol's day frame (in place and returned)."""
    day = d.index[0].date()
    look = lambda etf: int(heat.get(etf, pd.Series(dtype=float)).get(day, 0) or 0)      # noqa: E731  (missing history = NEUTRAL)
    sector, market = look(SECTOR.get(symbol, MARKET)), look(MARKET)
    d["g_long_ok"] = sector != -1
    d["g_short_ok"] = (sector == -1) or (market == -1)
    red = spy_red(spy_day)
    d["g_spy_red"] = red.reindex(d.index, method="ffill").fillna(False).astype(bool).to_numpy() if red is not None else False
    return d


def add_gates(days: dict[str, list[pd.DataFrame]], daily: dict[str, pd.DataFrame]) -> dict[str, list[pd.DataFrame]]:
    """Backtest: add the gate columns to every day frame of every symbol."""
    heat = heat_map(daily)
    spy_days = {f.index[0].date(): f for f in days.get(MARKET, [])}
    for sym, frames in days.items():
        for f in frames:
            apply(f, sym, heat, spy_days.get(f.index[0].date()))
    return days
