#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from typing import Any

DB_FILE = "tracking.db"
OUTPUT_FILE = "learned_policy.json"

MIN_SAMPLE_MARKET = 8
MIN_SAMPLE_STRENGTH = 5


DEFAULT_POLICY = {
    "disabled_markets": [],
    "min_confidence": 0.62,
    "min_edge_spread": 0.02,
    "min_edge_ml": 0.015,
    "min_edge_total": 0.03,
    "max_kelly": 0.06,
    "allowed_strengths": ["A", "B"],
    "min_ev_percent": 0.0,
    "min_spread_conf_for_c": 0.67,
    "min_ml_conf_for_c": 0.65,
    "min_total_conf_for_c": 0.68,
    "suppress_suspicious_totals": True,
    "suppress_suspicious_all": True,
    "max_alerts_per_poll": 8,
    "max_alerts_per_game": 2,
    "max_alerts_per_market_per_game": 1
}


def load_rows() -> list[dict[str, Any]]:
    if not os.path.exists(DB_FILE):
        print("No tracking.db found")
        return []

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
    SELECT id, game, market, pick, line, odds, confidence, edge, strength,
           timestamp, result, profit, final_home_score, final_away_score
    FROM bets
    WHERE result IN ('win', 'loss')
    ORDER BY id ASC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def market_key(raw: str) -> str:
    m = str(raw or "").lower().strip()
    if m in ("spread", "spreads"):
        return "spread"
    if m in ("moneyline", "ml", "h2h"):
        return "ml"
    if m in ("total", "totals"):
        return "total"
    return m


def build_policy(rows: list[dict[str, Any]]) -> dict[str, Any]:
    policy = dict(DEFAULT_POLICY)

    if not rows:
        print("No graded bets yet")
        return policy

    by_market = defaultdict(list)
    by_market_strength = defaultdict(list)

    for r in rows:
        m = market_key(r.get("market"))
        by_market[m].append(r)
        by_market_strength[(m, str(r.get("strength", "D")).upper())].append(r)

    print("=== SELF LEARNING SUMMARY ===")
    print("graded_rows:", len(rows))

    # Market-level learning
    disabled_markets: list[str] = []
    for market, bucket in by_market.items():
        n = len(bucket)
        wins = sum(1 for r in bucket if r["result"] == "win")
        units = sum(float(r.get("profit") or 0.0) for r in bucket)
        avg_conf = sum(float(r.get("confidence") or 0.0) for r in bucket) / max(1, n)
        avg_edge = sum(float(r.get("edge") or 0.0) for r in bucket) / max(1, n)
        wr = wins / n if n else 0.0

        print(
            f"{market}: bets={n} wins={wins} "
            f"win_rate={round(wr*100,2)} net_units={round(units,2)} "
            f"avg_conf={round(avg_conf,4)} avg_edge={round(avg_edge,4)}"
        )

        if n >= MIN_SAMPLE_MARKET:
            # disable truly bad markets
            if wr < 0.45 and units <= -2:
                disabled_markets.append(market)

            # tighten weak markets
            if market == "spread":
                if wr < 0.52:
                    policy["min_edge_spread"] = max(policy["min_edge_spread"], round(avg_edge + 0.01, 4))
                elif wr > 0.58 and units > 1:
                    policy["min_edge_spread"] = max(0.015, round(avg_edge, 4))

            elif market == "ml":
                if wr < 0.50:
                    policy["min_edge_ml"] = max(policy["min_edge_ml"], round(avg_edge + 0.01, 4))
                elif wr > 0.57 and units > 1:
                    policy["min_edge_ml"] = max(0.01, round(avg_edge, 4))

            elif market == "total":
                if wr < 0.50:
                    policy["min_edge_total"] = max(policy["min_edge_total"], round(avg_edge + 0.01, 4))
                elif wr > 0.57 and units > 1:
                    policy["min_edge_total"] = max(0.02, round(avg_edge, 4))

    policy["disabled_markets"] = sorted(set(disabled_markets))

    # Strength-tier learning
    print("\n--- by market/strength ---")
    for (market, strength), bucket in sorted(by_market_strength.items()):
        n = len(bucket)
        wins = sum(1 for r in bucket if r["result"] == "win")
        units = sum(float(r.get("profit") or 0.0) for r in bucket)
        avg_conf = sum(float(r.get("confidence") or 0.0) for r in bucket) / max(1, n)
        wr = wins / n if n else 0.0

        print(
            f"{market}/{strength}: bets={n} wins={wins} "
            f"win_rate={round(wr*100,2)} net_units={round(units,2)} "
            f"avg_conf={round(avg_conf,4)}"
        )

        if n >= MIN_SAMPLE_STRENGTH and strength == "C":
            if market == "spread":
                if wr > 0.57 and units > 0:
                    policy["min_spread_conf_for_c"] = min(
                        policy["min_spread_conf_for_c"],
                        max(0.58, round(avg_conf, 4))
                    )
            elif market == "ml":
                if wr > 0.57 and units > 0:
                    policy["min_ml_conf_for_c"] = min(
                        policy["min_ml_conf_for_c"],
                        max(0.58, round(avg_conf, 4))
                    )
            elif market == "total":
                if wr > 0.57 and units > 0:
                    policy["min_total_conf_for_c"] = min(
                        policy["min_total_conf_for_c"],
                        max(0.60, round(avg_conf, 4))
                    )

    # Global confidence / kelly shaping
    total_bets = len(rows)
    total_wins = sum(1 for r in rows if r["result"] == "win")
    total_units = sum(float(r.get("profit") or 0.0) for r in rows)
    total_wr = total_wins / total_bets if total_bets else 0.0

    print("\n--- overall ---")
    print("overall_win_rate:", round(total_wr * 100.0, 2))
    print("overall_net_units:", round(total_units, 2))

    if total_bets >= 10:
        if total_wr < 0.50:
            policy["min_confidence"] = min(0.72, max(policy["min_confidence"], 0.66))
            policy["max_kelly"] = 0.04
        elif total_wr > 0.57 and total_units > 1:
            policy["min_confidence"] = max(0.58, policy["min_confidence"] - 0.02)
            policy["max_kelly"] = 0.06

    return policy


def save_policy(policy: dict[str, Any]) -> None:
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(policy, f, indent=2)
    print(f"\nSaved {OUTPUT_FILE}")


def main() -> None:
    rows = load_rows()
    policy = build_policy(rows)
    save_policy(policy)
    print("\n=== LEARNED POLICY ===")
    for k, v in policy.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
