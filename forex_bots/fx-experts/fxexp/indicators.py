"""Pure-numpy/pandas indicators on daily bars (columns open/high/low/close)."""
import numpy as np
import pandas as pd

def true_range(df):
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)

def wilder(series, n):
    """Wilder smoothing seeded with the simple mean of the first n values."""
    s = series.to_numpy(dtype=float); out = np.full(len(s), np.nan)
    vals = np.where(np.isnan(s), np.nan, s)
    start = np.argmax(~np.isnan(vals)) if (~np.isnan(vals)).any() else len(s)
    if start + n > len(s): return pd.Series(out, index=series.index)
    out[start + n - 1] = np.nanmean(vals[start:start + n])
    for i in range(start + n, len(s)):
        out[i] = (out[i - 1] * (n - 1) + vals[i]) / n
    return pd.Series(out, index=series.index)

def turtle_n(df, n=20):
    """Turtle N: N = (19*PDN + TR)/20 (Original Turtle Rules), seeded with a 20-day mean TR."""
    return wilder(true_range(df), n)

def adx(df, n=14):
    up = df["high"].diff(); dn = -df["low"].diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr = true_range(df); tr.iloc[0] = np.nan; pdm.iloc[0] = np.nan; mdm.iloc[0] = np.nan
    atr = wilder(tr, n); pdi = 100 * wilder(pdm, n) / atr; mdi = 100 * wilder(mdm, n) / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return wilder(dx, n)

def bollinger(close, n=20, k=2.0):
    mid = close.rolling(n).mean(); sd = close.rolling(n).std(ddof=0)  # Bollinger uses population sd
    return mid - k * sd, mid, mid + k * sd

def bandwidth(close, n=20, k=2.0):
    lo, mid, up = bollinger(close, n, k)
    return (up - lo) / mid

def parabolic_step(sar, ep, af, long, high, low, prev_high, prev_low, step=0.02, maxaf=0.20):
    """One Wilder Parabolic SAR update for the next bar given today's bar. Returns (sar, ep, af)."""
    new = sar + af * (ep - sar)
    if long:
        new = min(new, low, prev_low)
        if high > ep: ep, af = high, min(af + step, maxaf)
    else:
        new = max(new, high, prev_high)
        if low < ep: ep, af = low, min(af + step, maxaf)
    return new, ep, af

def atr(df, n=14):
    """Wilder ATR."""
    return wilder(true_range(df), n)

def ema(series, n):
    return series.ewm(span=n, adjust=False).mean()

def tharp_vol(df, n=10):
    """Tharp/Basso volatility: 10-day exponential moving average of the true range."""
    tr = true_range(df); tr.iloc[0] = (df["high"] - df["low"]).iloc[0]
    return ema(tr, n)
