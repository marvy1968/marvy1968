#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

TRACK_FILE = Path("storage/tracker/alerts.jsonl")

def load_rows():
    if not TRACK_FILE.exists():
        return []
    rows = []
    for line in TRACK_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows

def summarize(rows):
    by_market = defaultdict(int)
    by_tier = defaultdict(int)
    by_sport = defaultdict(int)

    for r in rows:
        by_market[r.get("market", "unknown")] += 1
        by_tier[r.get("tier", "unknown")] += 1
        by_sport[r.get("sport_key", "unknown")] += 1

    print("TOTAL ALERTS:", len(rows))
    print()

    print("BY MARKET")
    for k, v in sorted(by_market.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {k}: {v}")
    print()

    print("BY TIER")
    for k, v in sorted(by_tier.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {k}: {v}")
    print()

    print("BY SPORT")
    for k, v in sorted(by_sport.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {k}: {v}")
    print()

    print("LAST 10")
    for r in rows[-10:]:
        print(
            f"{r.get('phase')} | {r.get('sport_key')} | {r.get('game')} | "
            f"{r.get('market')} | {r.get('selection')} | "
            f"edge={r.get('edge')} | conf={r.get('confidence')} | tier={r.get('tier')}"
        )

if __name__ == "__main__":
    summarize(load_rows())
