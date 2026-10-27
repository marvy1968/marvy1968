import numpy as np
import pandas as pd

def daily(rows, start="2024-01-01"):
    """rows: list of (o,h,l,c) -> daily DataFrame on business days."""
    idx = pd.bdate_range(start, periods=len(rows))
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)

def flat_daily(n, px=1.1000, rng=0.0050, start="2024-01-01"):
    rows = [(px, px + rng / 2, px - rng / 2, px) for _ in range(n)]
    return daily(rows, start)

def h4_from_daily(d, paths=None):
    """Make 6 H4 bars per trading day starting 17:00 NY of the previous evening.
    paths: optional {date_index: [(o,h,l,c),...]} overrides."""
    out = []
    for i, (dt, r) in enumerate(d.iterrows()):
        start = (pd.Timestamp(dt.date()) - pd.Timedelta(days=1) + pd.Timedelta(hours=17)).tz_localize("America/New_York").tz_convert("UTC")
        bars = (paths or {}).get(i)
        if bars is None:
            bars = [(r.open, r.high, r.low, r.close)] + [(r.close, r.close, r.close, r.close)] * 5
        for k, (o, h, l, c) in enumerate(bars):
            out.append({"time": start + pd.Timedelta(hours=4 * k), "open": o, "high": h, "low": l, "close": c})
    return pd.DataFrame(out)

def choppy_daily(n, px=1.1000, rng=0.0050, start="2024-01-01"):
    """Range-bound oscillation (low ADX); the last bar is exactly px +- rng/2."""
    rows = []
    for i in range(n):
        off = (rng / 5) * (1 if i % 2 else -1)
        rows.append((px, px + rng / 2 + off, px - rng / 2 + off, px))
    rows[-1] = (px, px + rng / 2, px - rng / 2, px)
    return daily(rows, start)
