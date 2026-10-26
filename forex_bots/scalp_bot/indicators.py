"""Causal (no look-ahead) indicators shared by live trading and the backtester."""
import numpy as np
import pandas as pd

NY = "America/New_York"
OPEN_MIN = 9 * 60 + 30


def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(s, n):
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def true_range(df):
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df, n=14):
    return wilder(true_range(df), n)


def rsi(close, n=2):
    d = close.diff()
    up = wilder(d.clip(lower=0), n)
    dn = wilder((-d).clip(lower=0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100 - 100 / (1 + up / dn)
    out = out.where(~((dn == 0) & (up > 0)), 100.0)
    out = out.where(~((dn == 0) & (up == 0)), 50.0)   # flat bars -> neutral
    return out


def adx(df, n=14):
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr = atr(df, n)
    pdi = 100 * wilder(plus, n) / tr
    mdi = 100 * wilder(minus, n) / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return wilder(dx.fillna(0), n)


def _completed_htf(df, tf):
    """Session-anchored (09:30) higher-timeframe bars built from 1-min bars.
    Returns (htf_frame, idx) where idx[k] is the row of the latest htf bar that was COMPLETE
    at the close of 1-min bar k (-1 if none). No look-ahead: a bar counts only once its last
    minute has closed."""
    b = df["mod"].to_numpy() // tf
    key = df["session"].to_numpy().astype(np.int64) * 1000 + b
    codes, _ = pd.factorize(key)            # chronological, because df is time-sorted
    g = df.groupby(codes, sort=True)
    h = pd.DataFrame({"high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last()})
    last_min = np.minimum((b + 1) * tf, 390) - 1
    is_last = df["mod"].to_numpy() == last_min
    return h, np.where(is_last, codes, codes - 1)


def _take(values, idx):
    out = np.full(len(idx), np.nan)
    ok = idx >= 0
    out[ok] = np.asarray(values, dtype=float)[idx[ok]]
    return out


def add_direction_votes(df):
    """+1 bullish / -1 bearish / 0 undecided, from three methods on three timeframes."""
    c = df["close"].to_numpy()
    # 60-min trend structure
    h, k = _completed_htf(df, 60)
    e20 = ema(h["close"], 20)
    slope = e20 - e20.shift(3)
    v = np.where((h["close"] > e20) & (slope > 0), 1.0, np.where((h["close"] < e20) & (slope < 0), -1.0, 0.0))
    df["dir60"] = np.nan_to_num(_take(v, k))
    # 15-min momentum
    h, k = _completed_htf(df, 15)
    macd = ema(h["close"], 12) - ema(h["close"], 26)
    hist = (macd - ema(macd, 9)).to_numpy()
    df["dir15"] = np.nan_to_num(np.sign(_take(hist, k)))
    # 5-min order flow
    h, k = _completed_htf(df, 5)
    fast, slow = _take(ema(h["close"], 9), k), _take(ema(h["close"], 21), k)
    vw = df["vwap"].to_numpy()
    df["dir5"] = np.where((fast > slow) & (c > vw), 1.0, np.where((fast < slow) & (c < vw), -1.0, 0.0))
    return df


def add_vol_filter(df):
    """Causal inputs for the v4 filter (uses only the current and earlier closed bars)."""
    tr = true_range(df)
    df["vx_atr_ratio"] = wilder(tr, 5) / wilder(tr, 30).replace(0, np.nan)
    v = df["volume"]
    base = v.rolling(30).mean().shift(5)
    df["vx_vol_ratio"] = v.rolling(5).mean() / base.where(base > 0)
    return df


def prepare_intraday(raw, or_min=15):
    """1-min bars (UTC index) -> regular-session bars with every column the experts need."""
    df = raw[["open", "high", "low", "close", "volume"]].astype(float).copy()
    # [FIX] sanitize: sort, de-duplicate, drop NaN / non-positive prices, repair high/low
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    px = df[["open", "high", "low", "close"]]
    df = df[px.notna().all(axis=1) & (px > 0).all(axis=1)].copy()
    df["high"] = df[["open", "high", "low", "close"]].max(axis=1)
    df["low"] = df[["open", "high", "low", "close"]].min(axis=1)
    df["volume"] = df["volume"].fillna(0).clip(lower=0)
    ny = df.index.tz_convert(NY)
    mod = np.asarray(ny.hour * 60 + ny.minute - OPEN_MIN)
    keep = (mod >= 0) & (mod < 390)
    df = df[keep]
    ny = ny[keep]
    mod = mod[keep]
    sess = np.asarray(ny.year * 10000 + ny.month * 100 + ny.day)
    df["mod"] = mod
    df["session"] = sess

    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]
    tp = (h + l + c) / 3
    cum_pv = (tp * v).groupby(sess).cumsum()
    cum_v = v.groupby(sess).cumsum()
    df["vwap"] = (cum_pv / cum_v.replace(0, np.nan)).fillna(c)

    df["ema9"], df["ema20"], df["ema50"] = ema(c, 9), ema(c, 20), ema(c, 50)
    df["atr"] = atr(df, 14)
    atr20 = atr(df, 20)
    df["rsi2"] = rsi(c, 2)

    sma20 = c.rolling(20).mean()
    sd = c.rolling(20).std(ddof=0)
    df["sma20"] = sma20
    df["bb_up"], df["bb_lo"] = sma20 + 2 * sd, sma20 - 2 * sd
    df["kc_up"], df["kc_lo"] = df["ema20"] + 1.5 * atr20, df["ema20"] - 1.5 * atr20
    sq = (df["bb_up"] < df["kc_up"]) & (df["bb_lo"] > df["kc_lo"])
    df["sq_on"] = sq
    df["sq_count"] = sq.astype(int).groupby((~sq).cumsum()).cumsum()
    mid = (h.rolling(20).max() + l.rolling(20).min()) / 2
    df["mom"] = ema(c - (mid + sma20) / 2, 3)

    df["vol_avg"] = v.rolling(20).mean().shift(1)

    in_or = mod < or_min
    df["or_high"] = h.where(in_or).groupby(sess).transform("max").where(~in_or)
    df["or_low"] = l.where(in_or).groupby(sess).transform("min").where(~in_or)
    df["day_open"] = o.groupby(sess).transform("first")
    return add_vol_filter(add_direction_votes(df))


def to_arrays(df):
    """DataFrame -> dict of numpy arrays (fast bar-by-bar access)."""
    a = {c: df[c].to_numpy() for c in df.columns}
    a["ts"] = df.index
    a["n"] = len(df)
    return a
