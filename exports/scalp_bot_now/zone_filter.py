"""365-day zone filter for the 5 intraday experts (research).

For each stock and each session, using ONLY the 365 daily candles before that session:
  nodes     = volume-at-price levels (zones.profile_nodes: 50 bins, 3-bin smoothing, >= 3% of volume)
  BUY zone  = nearest node BELOW the prior close  +/- ZONE_ATR x daily ATR(14)
  SELL zone = nearest node ABOVE the prior close  +/- ZONE_ATR x daily ATR(14)
Experts may go long only while the 1-min close is inside the buy zone, short only inside the sell zone.
"""
import numpy as np
import pandas as pd

import zones

LOOKBACK = 365
ZONE_ATR = 0.5
MIN_HISTORY = 200


def _session_ints(index):
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_convert("America/New_York")
    return np.asarray(idx.year * 10000 + idx.month * 100 + idx.day)


def zone_table(daily):
    """daily OHLCV -> DataFrame indexed by session int: buy_lo, buy_hi, sell_lo, sell_hi (from PRIOR days only)."""
    d = daily.sort_index()
    d = d[~d.index.duplicated(keep="last")]
    sess = _session_ints(d.index)
    h, l, c, v = (d[k].to_numpy(float) for k in ("high", "low", "close", "volume"))
    atr = zones._atr(d).to_numpy()
    rows = {}
    for k in range(MIN_HISTORY, len(d)):
        w = slice(max(0, k - LOOKBACK), k)                   # strictly before session k
        nodes = zones.profile_nodes(h[w], l[w], c[w], v[w])
        pc, a = c[k - 1], atr[k - 1]
        if not np.isfinite(a) or not len(nodes):
            continue
        below, above = nodes[nodes < pc], nodes[nodes > pc]
        b = below.max() if len(below) else np.nan
        s = above.min() if len(above) else np.nan
        rows[int(sess[k])] = (b - ZONE_ATR * a, b + ZONE_ATR * a, s - ZONE_ATR * a, s + ZONE_ATR * a)
    return pd.DataFrame.from_dict(rows, orient="index", columns=["buy_lo", "buy_hi", "sell_lo", "sell_hi"])


def attach(arrays, tables):
    """Add zbuy_lo/zbuy_hi/zsell_lo/zsell_hi arrays (per 1-min bar) to each symbol's arrays."""
    for s, a in arrays.items():
        t = tables.get(s)
        n = a["n"]
        if t is None or t.empty:
            for k in ("zbuy_lo", "zbuy_hi", "zsell_lo", "zsell_hi"):
                a[k] = np.full(n, np.nan)
            continue
        r = t.reindex(a["session"].astype(int))
        a["zbuy_lo"], a["zbuy_hi"] = r["buy_lo"].to_numpy(), r["buy_hi"].to_numpy()
        a["zsell_lo"], a["zsell_hi"] = r["sell_lo"].to_numpy(), r["sell_hi"].to_numpy()


def build_tables(symbols, start, end, feed):
    from datetime import timedelta
    from alpaca.data.timeframe import TimeFrame
    from data import cached_bars, make_data_client
    from indicators import NY

    def utc(x):
        return pd.Timestamp(x, tz=NY).tz_convert("UTC").to_pydatetime()
    daily = cached_bars(make_data_client(), sorted(symbols), TimeFrame.Day, utc(start - timedelta(days=620)),
                        utc(end + timedelta(days=1)), feed, "all")
    return {s: zone_table(d) for s, d in daily.items()}
