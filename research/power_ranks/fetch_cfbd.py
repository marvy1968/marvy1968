"""Build games.csv + teams.csv for power_ranks / blind_backtest from the CollegeFootballData API.

Run on the VM (has CFBD_API_KEY; this container cannot reach the API). The key is read from the environment, never printed.
    export $(grep CFBD_API_KEY /opt/marv-bot/.env | xargs)      # or however you load it
    python3 fetch_cfbd.py --years 2024 2025 2026 --out ./cfb_data
Output  games.csv : season,date,home,away,hg,ag,neutral,season_type,week   (completed games only)
        teams.csv : season,team,conference                                 (FBS teams, conference as of that season)
Postseason games are included (bowls/CFP carry real information); pass --regular-only to drop them.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
import requests

BASE = "https://api.collegefootballdata.com"


def _get(session, path, **params):
    r = session.get(f"{BASE}{path}", params=params, timeout=60)
    if r.status_code in (401, 403):
        raise SystemExit(f"CFBD refused the key ({r.status_code}); check CFBD_API_KEY")
    r.raise_for_status()
    return r.json()


def _pick(d, camel, snake, default=None):
    return d.get(camel, d.get(snake, default))


def fetch(years, session, regular_only=False):
    games, teams = [], []
    types = ["regular"] if regular_only else ["regular", "postseason"]
    for y in years:
        for t in teams_for(session, y):
            teams.append({"season": y, "team": t["school"] if "school" in t else t.get("team"),
                          "conference": t.get("conference")})
        for st in types:
            for g in _get(session, "/games", year=y, seasonType=st):
                hp, ap = _pick(g, "homePoints", "home_points"), _pick(g, "awayPoints", "away_points")
                if hp is None or ap is None:
                    continue  # not played / not final
                games.append({"season": y, "date": str(_pick(g, "startDate", "start_date", ""))[:10],
                              "home": _pick(g, "homeTeam", "home_team"), "away": _pick(g, "awayTeam", "away_team"),
                              "hg": hp, "ag": ap, "neutral": int(bool(_pick(g, "neutralSite", "neutral_site", False))),
                              "season_type": st, "week": g.get("week")})
    g = pd.DataFrame(games)
    t = pd.DataFrame(teams).dropna(subset=["team", "conference"]).drop_duplicates(["season", "team"])
    if not g.empty:
        g = g[g["date"].astype(str).str.len() == 10].sort_values(["date", "home"]).reset_index(drop=True)
    return g, t


def teams_for(session, year):
    return _get(session, "/teams/fbs", year=year)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--years", type=int, nargs="+", required=True)
    ap.add_argument("--out", default="cfb_data")
    ap.add_argument("--regular-only", action="store_true")
    a = ap.parse_args()
    key = os.environ.get("CFBD_API_KEY")
    if not key:
        raise SystemExit("CFBD_API_KEY is not set in the environment")
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {key}", "Accept": "application/json"})
    g, t = fetch(a.years, s, a.regular_only)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    g.to_csv(out / "games.csv", index=False); t.to_csv(out / "teams.csv", index=False)
    print(f"wrote {len(g)} completed games, {len(t)} team-seasons -> {out}", file=sys.stderr)
    print(g.groupby("season").size().to_string())


if __name__ == "__main__":
    main()
