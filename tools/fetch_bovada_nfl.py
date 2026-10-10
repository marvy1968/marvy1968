"""Pull pregame Bovada NFL lines (ML, spread, total) from The Odds API historical endpoint, one snapshot
15 min before the first kickoff of each game day; each game keeps the snapshot taken before ITS kickoff.
Cache: state/cache/bovada_nfl_hist/<snapshot>.json (re-runs cost nothing). 30 credits per snapshot."""
import json, os, sys, time
from datetime import timedelta
from pathlib import Path
import pandas as pd, requests

CACHE = Path("/opt/marv-bot/state/cache/bovada_nfl_hist"); CACHE.mkdir(parents=True, exist_ok=True)
KEY = os.environ["ODDS_API_KEY"]
first, last = int(sys.argv[1]), int(sys.argv[2])
g = pd.read_csv("/opt/marv-bot/state/cache/nflverse_games.csv")
g = g[(g.season >= first) & (g.season <= last) & g.home_score.notna()].copy()
g["ko"] = pd.to_datetime(g.gameday + " " + g.gametime.fillna("13:00")).dt.tz_localize("America/New_York").dt.tz_convert("UTC")
days = sorted(g.groupby(g.ko.dt.tz_convert("America/New_York").dt.date).ko.min())
snaps = [(d - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ") for d in days]
print(len(snaps), "snapshots", file=sys.stderr)
left = None
for s in snaps:
    p = CACHE / f"{s}.json"
    if p.exists():
        continue
    for attempt in range(3):
        r = requests.get("https://api.the-odds-api.com/v4/historical/sports/americanfootball_nfl/odds",
                         params={"apiKey": KEY, "bookmakers": "bovada", "markets": "h2h,spreads,totals",
                                 "oddsFormat": "american", "date": s}, timeout=40)
        if r.status_code == 200:
            break
        time.sleep(3)
    if r.status_code != 200:
        print("fail", s, r.status_code, r.text[:200], file=sys.stderr); continue
    left = r.headers.get("x-requests-remaining")
    if left is not None and int(left) < 60000:
        print("credit floor hit, stopping", left, file=sys.stderr); break
    p.write_text(r.text)
print("credits left", left, file=sys.stderr)
