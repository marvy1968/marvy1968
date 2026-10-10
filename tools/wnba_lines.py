"""WNBA historical closing lines from ESPN's core odds API (median of pregame books, live books skipped) for every
finished game in Marv's WNBA box cache -> state/cache/wnba_lines_hist.csv (used by tools/hybrid_refine_bt.py).
Usage: .venv/bin/python tools/wnba_lines.py [first_season] [last_season]"""
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


if __name__ == "__main__":
    a, b = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (2018, 2026)
    tg = H.bb_team_games(ROOT / "state" / "cache", "wnba", range(a, b + 1))
    gm = tg[tg.is_home == 1][["game_id", "season"]].drop_duplicates()
    season = dict(zip(gm.game_id, gm.season))
    out = ROOT / "state" / "cache" / "wnba_lines_hist.csv"
    with out.open("w", newline="") as fh, cf.ThreadPoolExecutor(8) as ex:
        w = csv.writer(fh)
        w.writerow(["game_id", "season", "spread", "total", "n_books"])
        n = 0
        for g, items in ex.map(fetch, list(season)):
            it = [x for x in items if "live" not in ((x.get("provider") or {}).get("name") or "").lower()]
            sp = [x["spread"] for x in it if isinstance(x.get("spread"), (int, float))]          # home spread
            tt = [x["overUnder"] for x in it if isinstance(x.get("overUnder"), (int, float)) and x["overUnder"] > 100]
            if sp or tt:
                w.writerow([g, season[g], st.median(sp) if sp else "", st.median(tt) if tt else "", len(it)])
                n += 1
    print("games with lines", n, "->", out)
