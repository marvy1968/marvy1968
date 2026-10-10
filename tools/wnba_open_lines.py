"""WNBA opening + closing lines from ESPN's core odds API (pregame books only; live books skipped) for every finished
game in Marv's WNBA box cache -> state/cache/wnba_open_lines.csv. Used for steam by tools/wnba_institutional_bt.py.
Opener = each book's own 'open' HOME spread / total; close = its 'close' (else current). Median across books.
Usage: .venv/bin/python tools/wnba_open_lines.py [first_season] [last_season]"""
import concurrent.futures as cf
import csv
import json
import statistics as st
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from marv import hybrid as H  # noqa: E402

URL = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/wnba/events/{g}/competitions/{g}/odds"


def fetch(g):
    for i in range(4):
        try:
            return g, json.load(urllib.request.urlopen(URL.format(g=g), timeout=20)).get("items", [])
        except Exception:  # noqa: BLE001
            time.sleep(1 + i)
    return g, []


def num(x):
    try:
        v = float(str(x).replace("+", "").replace("o", "").replace("u", "").strip())
        return v
    except (TypeError, ValueError):
        return None


def spread_of(block):
    ps = (block or {}).get("pointSpread") or {}
    v = num(ps.get("american") or ps.get("alternateDisplayValue"))
    return v if v is not None and abs(v) < 40 else None


def total_of(block):
    t = (block or {}).get("total") or {}
    v = num(t.get("american") or t.get("alternateDisplayValue"))
    return v if v is not None and v > 100 else None


def parse(items):
    so, sc, to, tc = [], [], [], []
    for x in items:
        if "live" in ((x.get("provider") or {}).get("name") or "").lower():
            continue
        h = x.get("homeTeamOdds") or {}
        a, b = spread_of(h.get("open")), spread_of(h.get("close") or h.get("current"))
        c, d = total_of(x.get("open")), total_of(x.get("close") or x.get("current"))
        for lst, v in ((so, a), (sc, b), (to, c), (tc, d)):
            if v is not None:
                lst.append(v)
    med = lambda v: st.median(v) if v else ""  # noqa: E731
    return med(so), med(sc), med(to), med(tc)


if __name__ == "__main__":
    a, b = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (2020, 2026)
    tg = H.bb_team_games(ROOT / "state" / "cache", "wnba", range(a, b + 1))
    gm = tg[tg.is_home == 1][["game_id", "season"]].drop_duplicates()
    season = dict(zip(gm.game_id, gm.season))
    out = ROOT / "state" / "cache" / "wnba_open_lines.csv"
    n = 0
    with out.open("w", newline="") as fh, cf.ThreadPoolExecutor(8) as ex:
        w = csv.writer(fh)
        w.writerow(["game_id", "season", "s_open", "s_close", "t_open", "t_close"])
        for g, items in ex.map(fetch, list(season)):
            r = parse(items)
            if any(v != "" for v in r):
                w.writerow([g, season[g], *r])
                n += 1
    print("games with lines", n, "of", len(season), "->", out)
