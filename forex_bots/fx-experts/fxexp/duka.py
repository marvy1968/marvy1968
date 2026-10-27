"""Convert Dukascopy CSV (ms epoch, OHLC, volume) to mid candles. If only one side is available,
shift it by half the typical OANDA spread."""
import os, glob
import pandas as pd
from .instruments import spread

def load_side(path):
    df = pd.read_csv(path)
    if df.empty: return None
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    if "volume" in df.columns: df = df[df["volume"] > 0]
    return df.set_index("time")[["open", "high", "low", "close"]]

def build_mid(pair, src_dirs, tf="h1"):
    code = pair.replace("_", "").lower(); sides = {}
    for side in ("bid", "ask"):
        parts = []
        for d in src_dirs:
            for f in sorted(glob.glob(os.path.join(d, f"{code}-{tf}-{side}-*.csv"))):
                try: x = load_side(f)
                except Exception: x = None
                if x is not None and len(x): parts.append(x)
        if parts:
            x = pd.concat(parts); sides[side] = x[~x.index.duplicated(keep="last")].sort_index()
    b, a = sides.get("bid"), sides.get("ask"); h = spread(pair) / 2
    if b is not None and a is not None:
        idx = b.index.intersection(a.index); cover = len(idx) / max(len(b), len(a))
        if cover > 0.97: m = (b.loc[idx] + a.loc[idx]) / 2; return m, "bid/ask mid"
        if cover > 0.5:   # hybrid: true mid where both sides exist, one side +- half spread elsewhere
            m = (b.loc[idx] + a.loc[idx]) / 2
            rest_a = (a.drop(idx, errors="ignore") - h); rest_b = (b.drop(idx, errors="ignore") + h)
            out = pd.concat([m, rest_a, rest_b]); out = out[~out.index.duplicated()].sort_index()
            return out, f"hybrid mid ({cover:.0%} true mid)"
    best = max([x for x in (b, a) if x is not None], key=len)
    if best is b: return best + h, "bid+half spread"
    return best - h, "ask-half spread"
