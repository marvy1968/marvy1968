"""Sector regime heat map (BULL / BEAR / SIDEWAYS) and top-5 universe selection."""
import html
import numpy as np
import pandas as pd
import config
from indicators import ema, adx, NY

ANSI = {"BULL": "\033[42;30m", "BEAR": "\033[41;97m", "SIDEWAYS": "\033[43;30m"}
RESET = "\033[0m"


def _dates(index):
    return np.asarray(index.tz_convert(NY).date)


def daily_frame(d):
    d = d[["open", "high", "low", "close", "volume"]].astype(float)
    c = d["close"]
    f = pd.DataFrame(index=d.index)
    f["close"] = c
    f["ema20"], f["ema50"] = ema(c, 20), ema(c, 50)
    f["slope"] = f["ema20"] / f["ema20"].shift(5) - 1
    f["adx"] = adx(d, 14)
    f["ret5"], f["ret20"] = c.pct_change(5), c.pct_change(20)
    f["date"] = _dates(d.index)
    return f


def classify_row(r):
    vals = [r.close, r.ema20, r.ema50, r.slope, r.adx]
    if any(pd.isna(v) for v in vals):
        return "SIDEWAYS", 0.0
    pts = np.sign(r.close - r.ema20) + np.sign(r.ema20 - r.ema50) + np.sign(r.slope)
    score = float(pts / 3 * min(r.adx / 40, 1) * 100)
    trending = r.adx >= config.ADX_TREND_MIN
    if trending and r.close > r.ema20 > r.ema50 and r.slope > 0:
        return "BULL", score
    if trending and r.close < r.ema20 < r.ema50 and r.slope < 0:
        return "BEAR", score
    return "SIDEWAYS", score


def build_heatmap(frames, asof_date):
    """Uses only daily bars strictly before asof_date (no look-ahead)."""
    rows = []
    for sector, spec in config.SECTORS.items():
        f = frames.get(spec["etf"])
        row = dict(sector=sector, etf=spec["etf"], regime="SIDEWAYS", score=0.0,
                   adx=np.nan, ret5=np.nan, ret20=np.nan)
        if f is not None:
            f = f[f["date"] < asof_date]
            if len(f):
                r = f.iloc[-1]
                reg, score = classify_row(r)
                row.update(regime=reg, score=round(score, 1), adx=round(float(r.adx), 1),
                           ret5=float(r.ret5), ret20=float(r.ret20))
        row["live"] = row["regime"]
        rows.append(row)
    return pd.DataFrame(rows).set_index("sector")


def apply_intraday_overlay(regime, a, i):
    """[MOD] If the sector ETF is fighting its daily regime today, treat it as SIDEWAYS."""
    if a is None or i is None or i < 0:
        return regime
    c, vw, do = a["close"][i], a["vwap"][i], a["day_open"][i]
    chg = c / do - 1
    if regime == "BULL" and c < vw and chg < -config.OVERLAY_MOVE_PCT:
        return "SIDEWAYS"
    if regime == "BEAR" and c > vw and chg > config.OVERLAY_MOVE_PCT:
        return "SIDEWAYS"
    return regime


def print_heatmap(heat, universe=None):
    print("\n SECTOR REGIME HEAT MAP")
    print(" " + "-" * 78)
    for sector, r in heat.iterrows():
        reg = r["live"]
        tag = f"{ANSI[reg]} {reg:^9} {RESET}"
        syms = ",".join((universe or {}).get(sector, []))
        print(f" {sector:<17} {r['etf']:<5} {tag} score {r['score']:>6}  adx {r['adx']:>5}  {syms}")
    print(" " + "-" * 78)


def write_heatmap_html(heat, path, universe=None):
    tiles = []
    for sector, r in heat.iterrows():
        reg, s = r["live"], float(r["score"] or 0)
        alpha = 0.25 + 0.6 * min(abs(s) / 100, 1)
        color = {"BULL": f"rgba(22,163,74,{alpha})", "BEAR": f"rgba(220,38,38,{alpha})",
                 "SIDEWAYS": "rgba(202,138,4,0.35)"}[reg]
        syms = " ".join((universe or {}).get(sector, []))
        note = "" if r["live"] == r["regime"] else f" (daily {r['regime']})"
        tiles.append(
            f'<div class="t" style="background:{color}"><b>{html.escape(sector)}</b>'
            f'<span>{r["etf"]} · {reg}{note}</span><span>score {s:.0f} · ADX {r["adx"]}</span>'
            f'<small>{html.escape(syms)}</small></div>')
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="refresh" content="60">
<title>Sector Heat Map</title><style>
body{{font-family:system-ui,sans-serif;margin:16px;background:#111;color:#eee}}
.g{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px}}
.t{{padding:12px;border-radius:10px;display:flex;flex-direction:column;gap:4px}}
small{{opacity:.8}}</style></head><body><h2>S&amp;P 500 Sector Regimes</h2>
<div class="g">{''.join(tiles)}</div></body></html>"""
    with open(path, "w") as fh:
        fh.write(doc)


def select_universe(daily_by_symbol, asof_date, n):
    """Top-n per sector by 20-day average dollar volume, using data before asof_date."""
    out = {}
    for sector, spec in config.SECTORS.items():
        scored = []
        for s in spec["candidates"]:
            d = daily_by_symbol.get(s)
            if d is None or d.empty:
                continue
            d = d[_dates(d.index) < asof_date].tail(20)
            if len(d) >= 5:
                scored.append((float((d["close"] * d["volume"]).mean()), s))
        out[sector] = [s for _, s in sorted(scored, reverse=True)[:n]]
    return out
