"""Real in-game Kalshi prices for NBA player props at each quarter break (public API, no account).

For a seeded random sample of games in the snapshot file: list the game's settled KXNBAPTS / KXNBAREB / KXNBAAST /
KXNBA3PT markets (historical endpoint), then pull each market's 1-minute candlesticks over the game and keep, for
every break (end of Q1, Q2, Q3), the LAST candle before play resumes: yes bid / yes ask close, volume during the
break. The quote kept is the one standing when play resumes (last candle at or before the break's end) with its age, so
quotes that were not refreshed after the period ended can be filtered out in the evaluation.

usage: python research/prop_pacer/fetch_kalshi.py SNAP.parquet OUT.jsonl [n_games] [seed]
Resumable: games already in OUT are skipped.
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


def get(path: str, params: dict, tries: int = 6) -> dict:
    for i in range(tries):
        wait = 0.3 - (time.time() - _last[0])  # ~3 requests a second: the historical endpoints rate-limit above that
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            r = session.get(API + path, params=params, timeout=20)
        except requests.RequestException:
            time.sleep(2 ** i)
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


def event_suffix(row) -> list[str]:
    d = datetime.fromisoformat(str(row["date"])[:10])
    day = f"{d:%y}{MON[d.month - 1]}{d:%d}"
    a, h = ABBR.get(row["away_abbr"], row["away_abbr"]), ABBR.get(row["home_abbr"], row["home_abbr"])
    return [f"{day}{a}{h}", f"{day}{h}{a}"]


def _norm(n: str) -> str:
    return " ".join(str(n).lower().replace(".", "").replace("'", "").replace("-", " ").split())


def game_prices(g: pd.DataFrame, q1: dict | None = None) -> list[dict]:
    """q1: {(normalised name, stat): value at the end of Q1}; rungs already reached by then are never bets, skipped."""
    row = g.iloc[0]
    breaks = g.groupby("k")[["break_start", "break_end"]].first()
    out = []
    for suffix in event_suffix(row):
        markets = []
        for series in SERIES:
            markets += [(series, m) for m in get("/historical/markets", {"event_ticker": f"{series}-{suffix}",
                                                                          "limit": 1000}).get("markets", [])]
        if markets:
            break
    if not markets:
        return []
    t0 = ts(breaks["break_start"].min()) - 120
    t1 = ts(breaks["break_end"].max()) + 120
    for series, m in markets:
        have = (q1 or {}).get((_norm(m["title"].split(":")[0]), SERIES[series]))
        if have is not None and m.get("floor_strike") is not None and have > m["floor_strike"]:
            continue
        c = get(f"/historical/markets/{m['ticker']}/candlesticks",
                {"start_ts": t0, "end_ts": t1, "period_interval": 1}).get("candlesticks", [])
        if not c:
            continue
        for k, b in breaks.iterrows():
            bs, be = ts(b["break_start"]), ts(b["break_end"])
            # Kalshi emits a candle only when something changes, so the quote standing when play resumes is the last
            # candle at or before the end of the break. Its age is kept so stale quotes can be filtered.
            before = [x for x in c if x["end_period_ts"] <= be]
            if not before:
                continue
            last = before[-1]
            inside = [x for x in before if x["end_period_ts"] >= bs]
            yb, ya = (last.get("yes_bid") or {}).get("close"), (last.get("yes_ask") or {}).get("close")
            vol = sum(float(x.get("volume") or x.get("volume_fp") or 0) for x in inside)
            out.append({"game_id": int(row["game_id"]), "k": int(k), "series": series, "stat": SERIES[series],
                        "ticker": m["ticker"], "title": m["title"], "player_name": m["title"].split(":")[0].strip(),
                        "strike": m.get("floor_strike"), "result": m.get("result"),
                        "yes_bid": None if yb is None else float(yb), "yes_ask": None if ya is None else float(ya),
                        "break_volume": vol, "price_ts": last["end_period_ts"], "age_s": be - last["end_period_ts"],
                        "since_period_end_s": last["end_period_ts"] - bs})
    return out


def main(snap: str, out: str, n: int = 300, seed: int = 7) -> None:
    df = pd.read_parquet(snap, columns=["game_id", "date", "season", "k", "home_abbr", "away_abbr", "break_start",
                                        "break_end", "name", "pts_sf", "reb_sf", "ast_sf", "fg3_sf"])
    df = df[df["season"] == df["season"].max()]
    games = sorted(df["game_id"].unique())
    random.Random(seed).shuffle(games)
    done = set()
    if Path(out).exists():
        done |= {json.loads(line)["game_id"] for line in open(out)}
    if Path(out + ".empty").exists():
        done |= {int(line.split()[1]) for line in open(out + ".empty") if line.startswith("empty")}
    todo = [g for g in games[:n] if g not in done]
    print(f"{len(todo)} games to fetch", flush=True)
    by = df.drop_duplicates(["game_id", "k"]).groupby("game_id")
    q1all = df[df["k"] == 1].groupby("game_id")
    for i, gid in enumerate(todo):
        q = q1all.get_group(gid)
        q1 = {(_norm(n), s): v for n, *vals in q[["name", "pts_sf", "reb_sf", "ast_sf", "fg3_sf"]].itertuples(index=False)
              for s, v in zip(("pts", "reb", "ast", "fg3"), vals)}
        rows = game_prices(by.get_group(gid), q1)
        with open(out, "a") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        if not rows:
            with open(out + ".empty", "a") as fh:
                fh.write(f"empty {gid}\n")
        print(f"{i + 1}/{len(todo)} game {gid}: {len(rows)} prices", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], *(int(x) for x in sys.argv[3:]))
