"""Sector news for the owner's funnel ("news for each sector: negative -> short, positive -> long", drawing 2026-10-17).

Two readings of "the sector's news is negative" on a given day (both use only what was known by that day's close):
  trend  the sector ETF (XLK, XLF, ...) closes under its 50-day average and that average is lower than 10 days ago: the market's verdict
         on the sector's news, already in the price.
  news   Alpaca's news feed (Benzinga headlines + summaries) for the sector's 5 stocks over the last `days` sessions, each article scored
         with a finance word list (negative words minus positive words); the sector is negative when the summed score is below zero over
         at least `min_articles` articles. A word list is crude (no context, no sarcasm), so it is tested before it is trusted.
Articles are cached per month under the cache folder, so a re-run needs no new requests.
"""

import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"

NEG = {"downgrade", "downgrades", "downgraded", "miss", "misses", "missed", "cut", "cuts", "lowers", "lowered", "weak", "weaker",
       "weakness", "decline", "declines", "declined", "drop", "drops", "dropped", "fall", "falls", "fell", "plunge", "plunges", "plunged",
       "slump", "slumps", "loss", "losses", "lawsuit", "sued", "probe", "investigation", "recall", "recalls", "layoffs", "layoff",
       "warning", "warns", "warned", "bearish", "underperform", "sell", "short", "fraud", "fine", "fined", "halt", "halts", "delay",
       "delays", "delayed", "disappoint", "disappoints", "disappointing", "slowdown", "tariff", "tariffs", "ban", "bankruptcy", "default",
       "downside", "risk", "risks", "concern", "concerns", "pressure", "headwind", "headwinds", "slashes", "slashed", "resigns", "subpoena"}
POS = {"upgrade", "upgrades", "upgraded", "beat", "beats", "raise", "raises", "raised", "strong", "stronger", "strength", "rise", "rises",
       "rose", "jump", "jumps", "jumped", "surge", "surges", "surged", "soar", "soars", "soared", "record", "gain", "gains", "growth",
       "bullish", "outperform", "buy", "approval", "approved", "wins", "win", "boost", "boosts", "boosted", "tops", "exceeds", "expands",
       "expansion", "partnership", "upside", "rally", "rallies", "profit", "profits", "dividend", "buyback", "tailwind", "tailwinds"}
WORD = re.compile(r"[a-z]+")


def score(text: str) -> int:
    """Positive finance words minus negative ones in a headline + summary: below 0 = bad news."""
    words = WORD.findall((text or "").lower())
    return sum(w in POS for w in words) - sum(w in NEG for w in words)


def _month_iter(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def fetch(symbols: list[str], start: date, end: date, cache: Path, headers: dict | None = None, session=None) -> list[dict]:
    """[{"t": iso time, "symbols": [...], "s": score}] for articles on any of `symbols` between start and end (cached per month)."""
    from .data import _headers
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    s = session or requests.Session()
    out, today = [], date.today()
    tag = hashlib.sha1(",".join(sorted(symbols)).encode()).hexdigest()[:10]       # stable across runs (hash() is not)
    for y, m in _month_iter(start, end):
        path = cache / f"news_{tag}_{y}{m:02d}.json"
        month_end = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        if path.exists() and month_end <= today:
            out += json.loads(path.read_text())
            continue
        rows, token = [], None
        a = datetime(y, m, 1, tzinfo=timezone.utc)
        b = min(datetime(month_end.year, month_end.month, month_end.day, tzinfo=timezone.utc), datetime.now(timezone.utc))
        while True:
            params = {"symbols": ",".join(symbols), "start": a.strftime("%Y-%m-%dT%H:%M:%SZ"), "end": b.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "limit": 50, "sort": "asc", "include_content": "false"}
            if token:
                params["page_token"] = token
            for attempt in range(5):
                r = s.get(NEWS_URL, params=params, headers=headers or _headers(), timeout=60)
                if r.status_code == 429 or r.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                break
            body = r.json()
            for n in body.get("news") or []:
                rows.append({"t": n.get("created_at"), "symbols": n.get("symbols") or [],
                             "s": score(f"{n.get('headline', '')} {n.get('summary', '')}")})
            token = body.get("next_page_token")
            if not token:
                break
        if month_end <= today:
            path.write_text(json.dumps(rows))
        out += rows
    return [r for r in out if r["t"] and start.isoformat() <= r["t"][:10] <= end.isoformat()]


def sector_news_negative(articles: list[dict], sector_of: dict, days_index, look: int = 5, min_articles: int = 3) -> dict:
    """{sector ETF: pd.Series(bool) on days_index}: True when that sector's articles published in the `look` sessions up to a day's close
    (16:00 ET) sum to a negative score over at least `min_articles` articles."""
    days = pd.DatetimeIndex(sorted({pd.Timestamp(d).normalize().tz_localize(None) if pd.Timestamp(d).tzinfo else pd.Timestamp(d).normalize()
                                    for d in days_index}))
    by_sector: dict[str, list] = {}
    for a in articles:
        t = pd.Timestamp(a["t"]).tz_convert("America/New_York")
        day = (t + pd.Timedelta(hours=8)).normalize().tz_localize(None)        # after 16:00 counts for the NEXT session's decision
        for etf in {sector_of[s] for s in a["symbols"] if s in sector_of}:
            by_sector.setdefault(etf, []).append((day, a["s"]))
    out = {}
    for etf in set(sector_of.values()):
        rows = by_sector.get(etf, [])
        if not rows:
            out[etf] = pd.Series(False, index=days)
            continue
        df = pd.DataFrame(rows, columns=["day", "s"])
        g = df.groupby("day")["s"].agg(["sum", "count"]).reindex(pd.date_range(days.min(), days.max()), fill_value=0)
        g = g.reindex(days.union(g.index)).fillna(0)
        roll = g.rolling(f"{int(look * 7 / 5) + 1}D").sum()                     # ~`look` sessions of calendar days
        neg = (roll["sum"] < 0) & (roll["count"] >= min_articles)
        out[etf] = neg.reindex(days).fillna(False).astype(bool)
    return out


def sector_trend_negative(etf_bars: dict) -> dict:
    """{sector ETF: pd.Series(bool) by its own dates}: under a falling 50-day average."""
    out = {}
    for etf, d in etf_bars.items():
        c = d["close"].astype(float)
        m = c.rolling(50, min_periods=50).mean()
        s = (c < m) & (m < m.shift(10))
        s.index = pd.DatetimeIndex([pd.Timestamp(x).normalize().tz_localize(None) if pd.Timestamp(x).tzinfo else pd.Timestamp(x).normalize()
                                    for x in s.index])
        out[etf] = s
    return out


def per_stock_ok(sector_neg: dict, sector_of: dict, sym: str) -> pd.Series | None:
    """The short filter for one stock: its sector's series (None when the stock has no sector)."""
    return sector_neg.get(sector_of.get(sym, ""))
