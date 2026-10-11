"""Causal indicators: every value at bar i uses only bars <= i (no look-ahead)."""

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 2) -> pd.Series:
    """Wilder RSI (n=2 is the short-term mean-reversion version)."""
    d = close.diff()
    up, dn = d.clip(lower=0), (-d).clip(lower=0)
    au = up.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    ad = dn.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = au / ad.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(ad != 0, 100.0).where(au.notna())


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """Volume-weighted average price since the open of each session (index must be a tz-aware DatetimeIndex)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    day = df.index.normalize()
    pv = (tp * df["volume"]).groupby(day).cumsum()
    v = df["volume"].groupby(day).cumsum()
    return pv / v.replace(0, np.nan)


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder's ADX (trend strength, direction-less)."""
    up, dn = df["high"].diff(), -df["low"].diff()
    plus = ((up > dn) & (up > 0)) * up
    minus = ((dn > up) & (dn > 0)) * dn
    a = atr(df, n)
    pdi = 100 * plus.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean() / a
    mdi = 100 * minus.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def macd_hist(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    line = ema(close, fast) - ema(close, slow)
    return line - line.ewm(span=signal, adjust=False, min_periods=signal).mean()


def stoch_fast(df: pd.DataFrame, k: int = 8, d: int = 3) -> pd.Series:
    """Fast stochastic %K(k) smoothed over d bars."""
    lo, hi = df["low"].rolling(k, min_periods=k).min(), df["high"].rolling(k, min_periods=k).max()
    raw = 100 * (df["close"] - lo) / (hi - lo).replace(0, np.nan)
    return raw.rolling(d, min_periods=d).mean()


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid, sd = close.rolling(n, min_periods=n).mean(), close.rolling(n, min_periods=n).std()
    return mid - k * sd, mid, mid + k * sd


def psar_below_price(df: pd.DataFrame, step: float = 0.02, max_step: float = 0.2) -> pd.Series:
    """Parabolic SAR bullish flag (True while the SAR sits below price). Iterative, causal."""
    h, l = df["high"].to_numpy(float), df["low"].to_numpy(float)
    n = len(df)
    out = np.zeros(n, dtype=bool)
    if n < 3:
        return pd.Series(out, index=df.index)
    bull, sar, ep, af = True, l[0], h[0], step
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if bull:
            sar = min(sar, l[i - 1], l[i - 2] if i > 1 else l[i - 1])
            if l[i] < sar:
                bull, sar, ep, af = False, ep, l[i], step
            elif h[i] > ep:
                ep, af = h[i], min(af + step, max_step)
        else:
            sar = max(sar, h[i - 1], h[i - 2] if i > 1 else h[i - 1])
            if h[i] > sar:
                bull, sar, ep, af = True, ep, h[i], step
            elif l[i] < ep:
                ep, af = l[i], min(af + step, max_step)
        out[i] = bull
    return pd.Series(out, index=df.index)
