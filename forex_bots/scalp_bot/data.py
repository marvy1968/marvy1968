"""Alpaca market-data helpers: batched bar fetches, on-disk cache, live rolling bar cache."""
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

import config

log = logging.getLogger("scalpbot.data")


def make_data_client():
    return StockHistoricalDataClient(config.API_KEY, config.API_SECRET)


def fetch_bars(client, symbols, timeframe, start, end=None, feed="iex", adjustment="raw", chunk=25):
    """Returns {symbol: DataFrame(open, high, low, close, volume, ...)} with a UTC DatetimeIndex."""
    out = {}
    symbols = list(dict.fromkeys(symbols))
    for k in range(0, len(symbols), chunk):
        batch = symbols[k:k + chunk]
        req = StockBarsRequest(symbol_or_symbols=batch, timeframe=timeframe, start=start, end=end,
                               feed=DataFeed(feed), adjustment=Adjustment(adjustment))
        try:
            df = client.get_stock_bars(req).df
        except Exception as e:   # noqa: BLE001
            log.warning("bar fetch failed for %s: %s", batch, e)
            continue
        if df is None or df.empty:
            continue
        df = df.rename(columns={"vwap": "bar_vwap"})
        for sym, g in df.groupby(level="symbol"):
            out[sym] = g.droplevel("symbol").sort_index()
    return out


def cached_bars(client, symbols, timeframe, start, end, feed, adjustment, cache_dir="data_cache"):
    os.makedirs(cache_dir, exist_ok=True)
    key = json.dumps([sorted(symbols), str(timeframe), str(start), str(end), feed, adjustment])
    path = os.path.join(cache_dir, hashlib.md5(key.encode()).hexdigest()[:16] + ".pkl")
    if os.path.exists(path):
        return pd.read_pickle(path)
    data = fetch_bars(client, symbols, timeframe, start, end, feed, adjustment)
    pd.to_pickle(data, path)
    return data


class BarCache:
    """Keeps a few days of 1-min bars per symbol; appends the newest bars every minute."""

    def __init__(self, client, symbols, feed, keep_days=5):
        self.client, self.symbols, self.feed, self.keep_days = client, list(symbols), feed, keep_days
        self.data = {}

    def warmup(self, days=5):
        start = datetime.now(timezone.utc) - timedelta(days=days)
        self.data = fetch_bars(self.client, self.symbols, TimeFrame.Minute, start, feed=self.feed)

    def update(self, lookback_min=15):
        now = datetime.now(timezone.utc)
        new = fetch_bars(self.client, self.symbols, TimeFrame.Minute, now - timedelta(minutes=lookback_min),
                         feed=self.feed)
        cutoff = pd.Timestamp(now - timedelta(days=self.keep_days))
        for s, df in new.items():
            old = self.data.get(s)
            comb = df if old is None else pd.concat([old, df])
            comb = comb[~comb.index.duplicated(keep="last")].sort_index()
            self.data[s] = comb[comb.index >= cutoff]

    def completed(self, sym):
        """Only fully closed bars (drop the in-progress minute)."""
        df = self.data.get(sym)
        if df is None:
            return None
        return df[df.index < pd.Timestamp.now(tz="UTC").floor("min")]
