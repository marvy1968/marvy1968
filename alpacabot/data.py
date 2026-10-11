"""Historical bars from Alpaca's market-data API (free IEX feed works), cached on disk by symbol, timeframe and month.

Keys come from the environment (ALPACA_DATA_KEY_ID / ALPACA_DATA_SECRET_KEY; a paper-trading key pair is enough). Never commit them.
Intraday bars are converted to US/Eastern and cut to the regular session (09:30-16:00).
"""

import os
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

URL = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
NY = "America/New_York"
FRAME = {"1Min": "1m", "5Min": "5m", "1Day": "1d"}


KEY_NAMES = ("ALPACA_DATA_KEY_ID", "ALPACA_API_KEY", "ALPACA_KEY_ID", "APCA_API_KEY_ID")
SECRET_NAMES = ("ALPACA_DATA_SECRET_KEY", "ALPACA_SECRET_KEY", "ALPACA_API_SECRET", "ALPACA_SECRET", "APCA_API_SECRET_KEY")


def credentials() -> tuple[str, str]:
    """(key id, secret) from the environment under any of the common Alpaca variable names. Values are never logged or printed."""
    first = lambda names: next((os.environ[n].strip() for n in names if os.environ.get(n, "").strip()), "")  # noqa: E731
    return first(KEY_NAMES), first(SECRET_NAMES)


def _headers() -> dict:
    key, secret = credentials()
    if not key or not secret:
        raise RuntimeError("no Alpaca keys in the environment: set ALPACA_DATA_KEY_ID and ALPACA_DATA_SECRET_KEY (or ALPACA_API_KEY and ALPACA_SECRET_KEY) in /opt/marv-bot/.env")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _fetch(symbol: str, timeframe: str, start: str, end: str, feed: str = "iex", session: requests.Session | None = None) -> pd.DataFrame:
    s = session or requests.Session()
    rows, token = [], None
    while True:
        params = {"timeframe": timeframe, "start": start, "end": end, "limit": 10000, "adjustment": "all", "feed": feed}
        if token:
            params["page_token"] = token
        for attempt in range(4):
            r = s.get(URL.format(symbol=symbol), params=params, headers=_headers(), timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            break
        body = r.json()
        rows += body.get("bars") or []
        token = body.get("next_page_token")
        if not token:
            break
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows).rename(columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert(NY)
    return df.set_index("time")[["open", "high", "low", "close", "volume"]].astype(float).sort_index()


def regular_session(df: pd.DataFrame) -> pd.DataFrame:
    return df.between_time("09:30", "15:59") if len(df) else df


def months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def load(symbol: str, timeframe: str, start: date, end: date, cache: Path, feed: str = "iex") -> pd.DataFrame:
    """Bars for [start, end]; finished months are cached as parquet so reruns and killed runs resume."""
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    frames, today = [], date.today()
    for y, m in months(start, end):
        path = cache / f"{symbol}_{FRAME[timeframe]}_{y}{m:02d}.parquet"
        month_end = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        if path.exists() and month_end <= today:
            frames.append(pd.read_parquet(path))
            continue
        df = _fetch(symbol, timeframe, f"{y}-{m:02d}-01T00:00:00Z", f"{month_end:%Y-%m-%d}T00:00:00Z", feed)
        if month_end <= today:
            df.to_parquet(path)
        frames.append(df)
    out = pd.concat(frames) if frames else pd.DataFrame()
    out = out[~out.index.duplicated()].sort_index()
    out = out[(out.index.date >= start) & (out.index.date <= end)]
    return regular_session(out) if timeframe != "1Day" else out
