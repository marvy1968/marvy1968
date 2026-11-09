#!/usr/bin/env python3
"""Fetch ESPN summaries for fresh college games into the pace-test caches.

Frozen spec: pace-off-test-spec-2026-11-09.md (amendment 2026-11-09).
Fresh window Nov 9-13, 2026, NCAAB + NCAAW Division I only (groups=50),
completed games only. Idempotent: get_json skips summaries already cached.

Usage:
  python3 fetch_fresh_summaries.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]
Defaults: yesterday only (start == end == yesterday), so a daily morning
cron fetches the previous day's games after they complete.
"""
from __future__ import annotations
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import h2h_backtest as men
import h2h_backtest_ncaaw as women


def fetch_league(label, fetch_sb, fetch_stats, start, end):
    total = 0
    per_day = {}
    d = start
    while d <= end:
        ds = d.strftime("%Y%m%d")
        try:
            games = fetch_sb(ds, "50")
        except Exception as e:  # noqa: BLE001
            print(f"{label} {d}: scoreboard failed: {e}")
            per_day[str(d)] = None
            d += timedelta(days=1)
            continue
        n = 0
        for g in games:
            try:
                fetch_stats(g["event_id"])
                n += 1
            except Exception as e:  # noqa: BLE001
                print(f"{label} {d} event {g['event_id']}: summary failed: {e}")
        per_day[str(d)] = n
        total += n
        d += timedelta(days=1)
    return total, per_day


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()
    today = date.today()
    end = date.fromisoformat(args.end) if args.end else today - timedelta(days=1)
    start = date.fromisoformat(args.start) if args.start else end
    print(f"fetching {start} .. {end}")
    n_m, per_m = fetch_league(
        "NCAAB",
        lambda ds, g: men.fetch_scoreboard(men.NCAAB_SITE, ds, g),
        lambda eid: men.fetch_game_stats(men.NCAAB_SITE, eid),
        start, end)
    n_w, per_w = fetch_league(
        "NCAAW",
        lambda ds, g: women.fetch_scoreboard(ds, g),
        lambda eid: women.fetch_game_stats(eid),
        start, end)
    print(f"NCAAB: {n_m} summaries {per_m}")
    print(f"NCAAW: {n_w} summaries {per_w}")
    print(f"total: {n_m + n_w}")


if __name__ == "__main__":
    main()
