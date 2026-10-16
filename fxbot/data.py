"""OANDA v20 candles (bid + ask), cached per month as parquet. PRACTICE host only: this package never talks to a live account.

Credentials come from the environment (/opt/marv-bot/.env): OANDA_API_TOKEN (or OANDA_TOKEN / OANDA_API_KEY). Never printed or logged.
"""

import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

PRACTICE = "https://api-fxpractice.oanda.com"
TOKEN_NAMES = ("OANDA_API_TOKEN", "OANDA_TOKEN", "OANDA_API_KEY", "OANDA_ACCESS_TOKEN")
# the 7 USD majors: P&L converts to USD exactly (quote USD: x1; base USD: / price)
MAJORS = ["EUR_USD", "GBP_USD", "AUD_USD", "NZD_USD", "USD_JPY", "USD_CAD", "USD_CHF"]
COLS = ["bo", "bh", "bl", "bc", "ao", "ah", "al", "ac", "volume"]


def token() -> str:
    return next((os.environ[n].strip() for n in TOKEN_NAMES if os.environ.get(n, "").strip()), "")


def _get(path: str, params: dict, tries: int = 5) -> dict:
    import requests
    tok = token()
    if not tok:
        raise RuntimeError("no OANDA token: add OANDA_API_TOKEN (practice) to /opt/marv-bot/.env")
    for k in range(tries):
        r = requests.get(PRACTICE + path, params=params, headers={"Authorization": f"Bearer {tok}"}, timeout=60)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** k)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return {}


def _to_frame(candles: list) -> pd.DataFrame:
    rows = []
    for c in candles:
        if not c.get("complete", True):
            continue
        b, a = c["bid"], c["ask"]
        rows.append({"time": pd.Timestamp(c["time"]), "bo": b["o"], "bh": b["h"], "bl": b["l"], "bc": b["c"],
                     "ao": a["o"], "ah": a["h"], "al": a["l"], "ac": a["c"], "volume": c.get("volume", 0)})
    if not rows:
        return pd.DataFrame(columns=COLS, index=pd.DatetimeIndex([], tz="UTC")).astype("float64")
    return pd.DataFrame(rows).set_index("time").astype("float64")


def fetch(instrument: str, granularity: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Pages forward 5000 candles at a time from start to end (UTC)."""
    out, t = [], start
    while t < end:
        js = _get(f"/v3/instruments/{instrument}/candles", {"granularity": granularity, "price": "BA", "count": 5000,
                                                            "from": t.strftime("%Y-%m-%dT%H:%M:%SZ")})
        df = _to_frame(js.get("candles", []))
        df = df[df.index < pd.Timestamp(end)]
        if not len(df):
            break
        out.append(df)
        nxt = df.index[-1].to_pydatetime() + timedelta(seconds=1)
        if nxt <= t:
            break
        t = nxt
        if len(js.get("candles", [])) < 5000:
            break
    return pd.concat(out) if out else _to_frame([])


def load(instrument: str, granularity: str, start: date, end: date, cache: Path) -> pd.DataFrame:
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    frames, today = [], date.today()
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        path = cache / f"fx_{instrument}_{granularity}_{y}{m:02d}.parquet"
        if path.exists() and nxt <= today:
            frames.append(pd.read_parquet(path))
        else:
            df = fetch(instrument, granularity, datetime(y, m, 1, tzinfo=timezone.utc),
                       min(datetime(nxt.year, nxt.month, 1, tzinfo=timezone.utc), datetime.now(timezone.utc)))
            if nxt <= today:
                df.to_parquet(path)
            frames.append(df)
        y, m = nxt.year, nxt.month
    frames = [f for f in frames if len(f)]
    df = pd.concat(frames).astype("float64") if frames else _to_frame([])
    df = df[~df.index.duplicated()].sort_index()
    return df[(df.index.date >= start) & (df.index.date <= end)]


def mid(df: pd.DataFrame) -> pd.DataFrame:
    """Mid-price OHLC (what the signals look at); fills use the bid/ask columns."""
    out = pd.DataFrame({k: (df["b" + k[0]] + df["a" + k[0]]) / 2 for k in ("open", "high", "low", "close")}, index=df.index)
    if "volume" in df:                              # OANDA tick volume (volume-confirmation tests)
        out["volume"] = df["volume"]
    return out


def usd_per_unit(instrument: str, price_move: float, price: float) -> float:
    """USD P&L of 1 unit for a price move, for the USD majors."""
    return price_move if instrument.endswith("_USD") else price_move / price
