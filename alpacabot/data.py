"""Historical bars from Alpaca's market-data API (free IEX feed works), cached on disk by symbol, timeframe and month.

Keys come from the environment (ALPACA_DATA_KEY_ID / ALPACA_DATA_SECRET_KEY; a paper-trading key pair is enough). Never commit them.
Intraday bars are converted to US/Eastern and cut to the regular session (09:30-16:00).
"""

import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

URL = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
NY = "America/New_York"
FRAME = {"1Min": "1m", "5Min": "5m", "1Day": "1d"}
COLS = ["open", "high", "low", "close", "volume"]
# SIP = every US exchange, history from 2016; Alpaca's free plan allows SIP history older than 15 minutes. IEX (one exchange) only has a
# short history on Alpaca, which left the 2016-2020 training years nearly empty.
FEED = os.environ.get("ALPACA_FEED", "sip")


def _empty() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="float64") for c in COLS}, index=pd.DatetimeIndex([], tz=NY, name="time"))


KEY_NAMES = ("ALPACA_DATA_KEY_ID", "ALPACA_API_KEY_ID", "ALPACA_API_KEY", "ALPACA_KEY_ID", "APCA_API_KEY_ID")
SECRET_NAMES = ("ALPACA_DATA_SECRET_KEY", "ALPACA_API_SECRET_KEY", "ALPACA_SECRET_KEY", "ALPACA_API_SECRET", "ALPACA_SECRET", "APCA_API_SECRET_KEY")


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
        return _empty()
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


def load(symbol: str, timeframe: str, start: date, end: date, cache: Path, feed: str | None = None) -> pd.DataFrame:
    """Bars for [start, end]; finished months are cached as parquet so reruns and killed runs resume."""
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    feed = feed or FEED
    tag = "" if feed == "iex" else f"_{feed}"            # old IEX cache files keep their names; SIP files are separate
    frames, today = [], date.today()
    latest = datetime.now(timezone.utc) - timedelta(minutes=20)   # the free plan may not query the last 15 minutes of SIP
    for y, m in months(start, end):
        path = cache / f"{symbol}_{FRAME[timeframe]}{tag}_{y}{m:02d}.parquet"
        month_end = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        if path.exists() and month_end <= today:
            frames.append(pd.read_parquet(path))
            continue
        end_ts = min(datetime(month_end.year, month_end.month, month_end.day, tzinfo=timezone.utc), latest)
        df = _fetch(symbol, timeframe, f"{y}-{m:02d}-01T00:00:00Z", end_ts.strftime("%Y-%m-%dT%H:%M:%SZ"), feed)
        if month_end <= today:
            df.to_parquet(path)
        frames.append(df)
    frames = [f for f in frames if len(f)]
    out = pd.concat(frames).astype("float64") if frames else _empty()     # an empty cached month must not turn prices into objects
    out = out[~out.index.duplicated()].sort_index()
    out = out[(out.index.date >= start) & (out.index.date <= end)]
    return regular_session(out) if timeframe != "1Day" else out


def coverage(symbols, timeframe: str, start: date, end: date, cache: Path, feed: str | None = None) -> str:
    """Bars per year per symbol: shows at a glance whether the history is really there."""
    lines = [f"{timeframe} bars per year ({feed or FEED})"]
    for sym in symbols:
        df = load(sym, timeframe, start, end, cache, feed)
        counts = df.groupby(df.index.year).size() if len(df) else pd.Series(dtype=int)
        lines.append(f"{sym:5s} " + "  ".join(f"{y}:{n}" for y, n in counts.items()))
    return "\n".join(lines)
