#!/usr/bin/env python3
import json
import os
from collections import defaultdict

from live_odds_engine import run_signal_pipeline

DATA_FILE = "backtest_data.jsonl"
BANKROLL = 500.0
BET_SIZE = 10.0

def load_data():
    if not os.path.exists(DATA_FILE):
        print("❌ No backtest_data.jsonl found")
        return []

    rows = []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue

    print(f"Loaded {len(rows)} rows")
    return rows

def check_bet_result(signal, row):
    market = str(signal.get("market", "")).lower()
    side = str(signal.get("side", "")).lower()
    line = signal.get("line", 0)

    final_home = row["final_home_score"]
    final_away = row["final_away_score"]

    try:
        line = float(line) if line is not None else 0.0
    except Exception:
        line = 0.0

    home_team = row["payload"]["home_team"].lower()
    away_team = row["payload"]["away_team"].lower()

    if market == "moneyline":
        if "home" in side or side == home_team:
            return final_home > final_away
        if "away" in side or side == away_team:
            return final_away > final_home

    if market == "spread":
        if "home" in side or side == home_team:
            return (final_home + line) > final_away
        if "away" in side or side == away_team:
            return (final_away + line) > final_home

    if market == "total":
        total = final_home + final_away
        if "over" in side:
            return total > line
        if "under" in side:
            return total < line

    return None

def run_backtest(data):
    stats = defaultdict(int)
    bankroll = BANKROLL

    for row in data:
        payload = row["payload"]

        try:
            edge, intel = run_signal_pipeline(payload)
        except Exception as e:
            stats["pipeline_errors"] += 1
            continue

        ranked = intel.get("ranked_signals", [])
        alerts = intel.get("alerts_to_send", [])

        stats["rows_seen"] += 1
        stats["ranked_signals_total"] += len(ranked)
        stats["alerts_total"] += len(alerts)

        for sig in ranked:
            stats[f"ranked_market_{sig.get('market')}"] += 1
            stats[f"ranked_strength_{sig.get('strength')}"] += 1

        for sig in alerts:
            result = check_bet_result(sig, row)
            if result is None:
                continue

            stats["total_bets"] += 1
            stats[f"bet_market_{sig.get('market')}"] += 1
            stats[f"bet_strength_{sig.get('strength')}"] += 1

            if result:
                stats["wins"] += 1
                bankroll += BET_SIZE
            else:
                stats["losses"] += 1
                bankroll -= BET_SIZE

    total = stats["total_bets"]
    wins = stats["wins"]
    losses = stats["losses"]
    win_rate = (wins / total * 100.0) if total else 0.0

    print("\n===== BACKTEST RESULTS =====")
    print(f"Rows Seen: {stats['rows_seen']}")
    print(f"Ranked Signals: {stats['ranked_signals_total']}")
    print(f"Alerts Sent: {stats['alerts_total']}")
    print(f"Total Bets: {total}")
    print(f"Wins: {wins}")
    print(f"Losses: {losses}")
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Final Bankroll: ${bankroll:.2f}")
    print(f"Pipeline Errors: {stats['pipeline_errors']}")

    print("\n--- Ranked Market Breakdown ---")
    for k, v in stats.items():
        if k.startswith("ranked_market_"):
            print(k, v)

    print("\n--- Ranked Strength Breakdown ---")
    for k, v in stats.items():
        if k.startswith("ranked_strength_"):
            print(k, v)

    print("\n--- Bet Market Breakdown ---")
    for k, v in stats.items():
        if k.startswith("bet_market_"):
            print(k, v)

    print("\n--- Bet Strength Breakdown ---")
    for k, v in stats.items():
        if k.startswith("bet_strength_"):
            print(k, v)

if __name__ == "__main__":
    data = load_data()
    run_backtest(data)
