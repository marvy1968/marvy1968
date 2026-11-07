"""Real Kalshi prices for NBA player props: the PREGAME quote and the quote at each quarter break (public API).

For each game (seeded random sample of the snapshot file's games, or all of them): list the game's KXNBAPTS /
KXNBAREB / KXNBAAST / KXNBA3PT markets (every ladder rung), then read each market's 1-minute candlesticks from 3 hours
before tip-off to the end of the last break and keep
  pre_*    the quote standing at tip-off (last candle before the first play): the market's pregame view
  k=1,2,3  the quote standing when play resumes after each break (last candle at or before the break's end), with its
           age, so quotes that were not refreshed after the period ended can be filtered out.
Kalshi emits a candle only when something changes, so "last candle at or before t" is the quote at t.

Settled markets before Kalshi's historical cutoff live under /historical (one request per market). Newer markets use
the batch endpoint (as many markets per request as fit Kalshi's 10,000-candle cap).

usage: python research/prop_pacer/fetch_kalshi.py SNAP.parquet OUT.jsonl [n_games|all] [seed] [season]
Resumable: games already in OUT (or OUT.empty) are skipped.
"""

import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

API = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = {"KXNBAPTS": "pts", "KXNBAREB": "reb", "KXNBAAST": "ast", "KXNBA3PT": "fg3"}
ABBR = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS"}
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
session = requests.Session()
_last = [0.0]
_cutoff = [None]


def get(path: str, params: dict, tries: int = 8) -> dict:
    for _ in range(tries):
        wait = 0.36 - (time.time() - _last[0])  # under 3 requests a second: Kalshi rate-limits above that
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            r = session.get(API + path, params=params, timeout=30)
        except requests.RequestException:
            time.sleep(3)
            continue
        if r.status_code == 429:
            time.sleep(1.5)
            continue
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json()
    return {}


def ts(s: str) -> int:
    return int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())


def historical_cutoff() -> int:
    if _cutoff[0] is None:
        _cutoff[0] = ts(get("/historical/cutoff", {}).get("market_settled_ts", "2026-09-08T00:00:00Z"))
    return _cutoff[0]


def event_suffix(row) -> list[str]:
    d = datetime.fromisoformat(str(row["date"])[:10])
    day = f"{d:%y}{MON[d.month - 1]}{d:%d}"
    a, h = ABBR.get(row["away_abbr"], row["away_abbr"]), ABBR.get(row["home_abbr"], row["home_abbr"])
    return [f"{day}{a}{h}", f"{day}{h}{a}"]


def quote_at(candles: list[dict], t: int) -> dict | None:
    before = [x for x in candles if x["end_period_ts"] <= t]
    if not before:
        return None
    c = before[-1]
    # historical candles use "close", newer ones "close_dollars"
    yb = (c.get("yes_bid") or {}).get("close", (c.get("yes_bid") or {}).get("close_dollars"))
    ya = (c.get("yes_ask") or {}).get("close", (c.get("yes_ask") or {}).get("close_dollars"))
    return {"bid": None if yb is None else float(yb), "ask": None if ya is None else float(ya), "ts": c["end_period_ts"]}


def game_prices(g: pd.DataFrame) -> list[dict]:
    row = g.iloc[0]
    breaks = g.groupby("k")[["break_start", "break_end"]].first()
    tip = ts(row["tip"])
    historical = tip < historical_cutoff() - 86400
    markets = []
    for suffix in event_suffix(row):
        for series in SERIES:
            path = "/historical/markets" if historical else "/markets"
            markets += [(series, m) for m in get(path, {"event_ticker": f"{series}-{suffix}", "limit": 1000}).get("markets", [])]
        if markets:
            break
    if not markets:
        return []
    t0, t1 = tip - 3 * 3600, ts(breaks["break_end"].max()) + 120
    candles: dict[str, list] = {}
    if historical:
        for _, m in markets:
            candles[m["ticker"]] = get(f"/historical/markets/{m['ticker']}/candlesticks",
                                       {"start_ts": t0, "end_ts": t1, "period_interval": 1}).get("candlesticks", [])
    else:
        tickers = [m["ticker"] for _, m in markets]
        per = max(1, 9500 // max((t1 - t0) // 60, 1))  # Kalshi caps one batch at 10,000 candles
        for i in range(0, len(tickers), per):
            d = get("/markets/candlesticks", {"market_tickers": ",".join(tickers[i:i + per]), "start_ts": t0,
                                              "end_ts": t1, "period_interval": 1})
            for x in d.get("markets", []):
                candles[x.get("market_ticker") or x.get("ticker")] = x.get("candlesticks", [])
    out = []
    for series, m in markets:
        c = candles.get(m["ticker"]) or []
        if not c:
            continue
        pre = quote_at(c, tip)
        base = {"game_id": int(row["game_id"]), "series": series, "stat": SERIES[series], "ticker": m["ticker"],
                "title": m["title"], "player_name": m["title"].split(":")[0].strip(), "strike": m.get("floor_strike"),
                "result": m.get("result"), "pre_bid": pre and pre["bid"], "pre_ask": pre and pre["ask"],
                "pre_age_s": None if not pre else tip - pre["ts"]}
        for k, b in breaks.iterrows():
            bs, be = ts(b["break_start"]), ts(b["break_end"])
            q = quote_at(c, be)
            if not q:
                continue
            out.append({**base, "k": int(k), "yes_bid": q["bid"], "yes_ask": q["ask"], "price_ts": q["ts"],
                        "age_s": be - q["ts"], "since_period_end_s": q["ts"] - bs})
    return out


def main(snap: str, out: str, n="all", seed: int = 7, season: int | None = None) -> None:
    df = pd.read_parquet(snap, columns=["game_id", "date", "season", "k", "home_abbr", "away_abbr", "break_start",
                                        "break_end", "tip"])
    df = df[df["season"] == (int(season) if season else df["season"].max())]
    games = sorted(df["game_id"].unique())
    random.Random(int(seed)).shuffle(games)
    if n != "all":
        games = games[:int(n)]
    done = set()
    if Path(out).exists():
        done |= {json.loads(line)["game_id"] for line in open(out)}
    if Path(out + ".empty").exists():
        done |= {int(line.split()[1]) for line in open(out + ".empty") if line.startswith("empty")}
    todo = [g for g in games if g not in done]
    print(f"{len(todo)} games to fetch", flush=True)
    by = df.drop_duplicates(["game_id", "k"]).groupby("game_id")
    for i, gid in enumerate(todo):
        rows = game_prices(by.get_group(gid))
        with open(out, "a") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        if not rows:
            with open(out + ".empty", "a") as fh:
                fh.write(f"empty {gid}\n")
        print(f"{i + 1}/{len(todo)} game {gid}: {len(rows)} prices", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
