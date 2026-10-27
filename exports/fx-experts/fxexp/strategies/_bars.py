"""Helpers for intraday systems: resample completed M5 bars to completed N-minute bars."""
import pandas as pd

def resample(bars, minutes):
    """bars: completed M5 rows (UTC 'time', open/high/low/close/volume). Returns only completed
    N-minute bars (buckets aligned to UTC clock), indexed 0..n-1 with a 'time' column = bucket start."""
    if bars is None or len(bars) == 0: return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    df = bars.set_index("time")
    agg = df.resample(f"{minutes}min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(subset=["open"])
    last_end = df.index[-1] + pd.Timedelta(minutes=5)
    agg = agg[agg.index + pd.Timedelta(minutes=minutes) <= last_end]
    return agg.reset_index()
