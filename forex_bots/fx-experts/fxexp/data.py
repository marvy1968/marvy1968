"""Candle loading and NY-17:00 daily aggregation (OANDA dailyAlignment=17, America/New_York)."""
import pandas as pd

NY = "America/New_York"

def load_csv(path):
    df = pd.read_csv(path)
    tcol = "timestamp" if "timestamp" in df.columns else df.columns[0]
    df["time"] = pd.to_datetime(df[tcol], utc=True)
    df = df[["time", "open", "high", "low", "close"]].sort_values("time").drop_duplicates("time")
    return df.reset_index(drop=True)

def trade_date(ts_utc):
    """FX trading day: a bar starting at/after 17:00 NY belongs to the next calendar date."""
    ny = ts_utc.dt.tz_convert(NY) if hasattr(ts_utc, "dt") else ts_utc.tz_convert(NY)
    return (ny + pd.Timedelta(hours=7)).dt.date if hasattr(ny, "dt") else (ny + pd.Timedelta(hours=7)).date()

def to_daily(intraday):
    df = intraday.copy(); df["date"] = trade_date(df["time"])
    g = df.groupby("date", sort=True)
    d = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last(), "nbars": g.size()})
    d = d[d["nbars"] >= 2]  # drop stray single-bar holiday fragments
    d.index = pd.to_datetime(d.index)
    return d
