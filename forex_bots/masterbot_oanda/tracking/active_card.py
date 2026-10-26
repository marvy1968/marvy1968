#!/usr/bin/env python3
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

TRACK_FILE = Path("storage/tracker/alerts.jsonl")


def load_alerts():
    if not TRACK_FILE.exists():
        return []

    alerts = []
    with TRACK_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                alerts.append(json.loads(line))
            except Exception:
                continue
    return alerts


def filter_active(alerts):
    active = []
    for a in alerts:
        result = str(a.get("result", "")).lower()
        if result in ("", "pending", "open", "live"):
            active.append(a)
    return active


def dedupe_alerts(alerts):
    best = {}

    for idx, a in enumerate(alerts):
        key = (
            str(a.get("phase", "")),
            str(a.get("game", "")),
            str(a.get("market", "")),
            str(a.get("selection", "")),
        )

        prev = best.get(key)
        if prev is None:
            best[key] = (idx, a)
            continue

        prev_idx, prev_a = prev
        prev_ts = str(prev_a.get("ts_utc", ""))
        curr_ts = str(a.get("ts_utc", ""))

        if curr_ts and prev_ts:
            if curr_ts >= prev_ts:
                best[key] = (idx, a)
        else:
            if idx >= prev_idx:
                best[key] = (idx, a)

    return [pair[1] for pair in sorted(best.values(), key=lambda x: x[0])]


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def latest_cycle_only(alerts, window_seconds=180):
    stamped = []
    for a in alerts:
        ts = parse_ts(str(a.get("ts_utc", "")))
        if ts is not None:
            stamped.append((ts, a))

    if not stamped:
        return alerts

    latest_ts = max(ts for ts, _ in stamped)
    out = []

    for ts, a in stamped:
        delta = (latest_ts - ts).total_seconds()
        if delta <= window_seconds:
            out.append(a)

    return out


def best_one_per_game(alerts):
    by_game = defaultdict(list)
    for a in alerts:
        game = str(a.get("game", "UNKNOWN"))
        by_game[game].append(a)

    out = []
    for game, items in by_game.items():
        items.sort(
            key=lambda x: (
                float(x.get("confidence", 0) or 0),
                float(x.get("edge", 0) or 0),
            ),
            reverse=True,
        )
        out.append(items[0])

    out.sort(
        key=lambda x: (
            float(x.get("confidence", 0) or 0),
            float(x.get("edge", 0) or 0),
        ),
        reverse=True,
    )
    return out


def main():
    alerts = load_alerts()
    active = filter_active(alerts)
    active = dedupe_alerts(active)
    active = latest_cycle_only(active, window_seconds=180)
    active = best_one_per_game(active)

    print("=== LATEST ACTIVE CARD ===")
    print(f"total_active: {len(active)}")
    print()

    for a in active:
        sport = a.get("sport_key", "")
        game = a.get("game", "UNKNOWN")
        market = a.get("market", "")
        selection = a.get("selection", "")
        edge = round(float(a.get("edge", 0) or 0), 2)
        conf = round(float(a.get("confidence", 0) or 0), 2)
        tier = a.get("tier", "")
        ts = a.get("ts_utc", "")

        print(f"--- {game} ---")
        print(
            f"[{sport}] {market} -> {selection} | "
            f"EDGE={edge} | CONF={conf} | TIER={tier}"
        )
        print(f"ts={ts}")
        print()


if __name__ == "__main__":
    main()
