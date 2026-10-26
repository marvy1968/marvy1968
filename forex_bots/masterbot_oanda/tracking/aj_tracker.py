#!/usr/bin/env python3
import json
from pathlib import Path
from collections import defaultdict

TRACK_FILE = Path("storage/tracker/alerts.jsonl")


def load_rows():
    if not TRACK_FILE.exists():
        return []
    rows = []
    with TRACK_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def is_aj(row):
    source = str(row.get("source", "")).lower()
    tier = str(row.get("tier", "")).upper()
    return source == "aj_edge" or tier.startswith("AJ-")


def active_rows(rows):
    out = []
    for r in rows:
        result = str(r.get("result", "")).lower()
        if result in ("", "pending", "open", "live"):
            out.append(r)
    return out


def dedupe(rows):
    best = {}
    for i, r in enumerate(rows):
        key = (
            str(r.get("game", "")),
            str(r.get("market", "")),
            str(r.get("selection", "")),
            str(r.get("tier", "")),
        )
        prev = best.get(key)
        if prev is None:
            best[key] = (i, r)
            continue
        prev_i, prev_r = prev
        prev_ts = str(prev_r.get("ts_utc", ""))
        curr_ts = str(r.get("ts_utc", ""))
        if curr_ts >= prev_ts:
            best[key] = (i, r)
    return [x[1] for x in sorted(best.values(), key=lambda z: z[0])]


def print_active_card(rows):
    rows = [r for r in rows if is_aj(r)]
    rows = active_rows(rows)
    rows = dedupe(rows)
    rows.sort(key=lambda r: (float(r.get("confidence", 0) or 0), float(r.get("edge", 0) or 0)), reverse=True)

    print("=== AJ ACTIVE CARD ===")
    print(f"total_active: {len(rows)}")
    print()

    for r in rows:
        print(f"--- {r.get('game', 'UNKNOWN')} ---")
        print(
            f"[{r.get('sport_key','')}] "
            f"{r.get('market','')} -> {r.get('selection','')} | "
            f"EDGE={r.get('edge')} | "
            f"CONF={r.get('confidence')} | "
            f"TIER={r.get('tier')} | "
            f"AJ_UNITS={r.get('aj_units', 'n/a')} | "
            f"odds={r.get('odds', 'n/a')} | "
            f"book={r.get('bookmaker', 'n/a')}"
        )
        print(f"ts={r.get('ts_utc','')}")
        print()


def print_summary(rows):
    rows = [r for r in rows if is_aj(r)]
    rows = dedupe(rows)

    print("=== AJ SUMMARY ===")
    print(f"unique_aj_alerts: {len(rows)}")

    by_tier = defaultdict(int)
    by_market = defaultdict(int)
    by_sport = defaultdict(int)

    settled = 0
    wins = 0
    losses = 0
    pushes = 0
    units = 0.0

    for r in rows:
        by_tier[str(r.get("tier", ""))] += 1
        by_market[str(r.get("market", ""))] += 1
        by_sport[str(r.get("sport_key", ""))] += 1

        result = str(r.get("result", "")).lower()
        if result in ("win", "loss", "push"):
            settled += 1
            if result == "win":
                wins += 1
            elif result == "loss":
                losses += 1
            elif result == "push":
                pushes += 1
            try:
                units += float(r.get("profit_units", 0) or 0)
            except Exception:
                pass

    print()
    print("BY TIER")
    for k, v in sorted(by_tier.items()):
        print(f"  {k}: {v}")

    print()
    print("BY MARKET")
    for k, v in sorted(by_market.items()):
        print(f"  {k}: {v}")

    print()
    print("BY SPORT")
    for k, v in sorted(by_sport.items()):
        print(f"  {k}: {v}")

    print()
    print(f"SETTLED: {settled}")
    print(f"WINS: {wins}")
    print(f"LOSSES: {losses}")
    print(f"PUSHES: {pushes}")
    print(f"UNITS: {round(units, 2)}")
    hit = round((wins / settled) * 100, 2) if settled else 0.0
    print(f"WIN RATE: {hit} %")


if __name__ == "__main__":
    rows = load_rows()
    print_active_card(rows)
    print_summary(rows)
