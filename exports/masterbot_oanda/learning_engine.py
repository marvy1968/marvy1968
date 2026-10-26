
#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(os.getenv("PERF_DB", "performance_tracking.db"))
POLICY_PATH = Path(os.getenv("LEARNED_POLICY_JSON", "learned_policy.json"))

MIN_SAMPLES_TO_ADJUST = float(os.getenv("LEARN_MIN_SAMPLES_TO_ADJUST", "100"))
MIN_SAMPLES_STRONG_ADJUST = float(os.getenv("LEARN_MIN_SAMPLES_STRONG_ADJUST", "250"))

BASE_MIN_EDGE = float(os.getenv("ENTRY_MIN_GAME_EDGE", "0.09"))
BASE_MIN_CONF = float(os.getenv("LEARN_BASE_MIN_CONF", "0.55"))
MAX_EDGE_SHIFT = float(os.getenv("LEARN_MAX_EDGE_SHIFT", "0.03"))
MAX_CONF_SHIFT = float(os.getenv("LEARN_MAX_CONF_SHIFT", "0.05"))

ROI_BAD = float(os.getenv("LEARN_ROI_BAD", "-0.05"))
ROI_GOOD = float(os.getenv("LEARN_ROI_GOOD", "0.05"))
WINRATE_BAD = float(os.getenv("LEARN_WINRATE_BAD", "0.48"))
WINRATE_GOOD = float(os.getenv("LEARN_WINRATE_GOOD", "0.55"))


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def save_json(path: Path, payload: Any) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def norm(v: Any) -> str:
    return " ".join(str(v or "").strip().lower().split())


def to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def bucket_key(sport_key: str, market: str) -> str:
    return f"{norm(sport_key)}|{norm(market)}"


def edge_band(display_edge: float) -> str:
    x = to_float(display_edge, 0.0)
    if x < 0.10:
        return "lt_0.10"
    if x < 0.20:
        return "0.10_0.19"
    if x < 0.30:
        return "0.20_0.29"
    if x < 0.40:
        return "0.30_0.39"
    return "ge_0.40"


def compute_adjustment(effective_samples: float, win_rate: float, roi_avg: float) -> dict[str, Any]:
    allow_adjust = effective_samples >= MIN_SAMPLES_TO_ADJUST
    strong_adjust = effective_samples >= MIN_SAMPLES_STRONG_ADJUST

    edge_shift = 0.0
    conf_shift = 0.0
    status = "observe_only"

    if not allow_adjust:
        return {
            "status": status,
            "allow_adjust": False,
            "strong_adjust": False,
            "edge_shift": 0.0,
            "conf_shift": 0.0,
            "recommended_min_edge": BASE_MIN_EDGE,
            "recommended_min_confidence": BASE_MIN_CONF,
        }

    if roi_avg <= ROI_BAD or win_rate <= WINRATE_BAD:
        status = "tighten"
        edge_shift = MAX_EDGE_SHIFT if strong_adjust else min(MAX_EDGE_SHIFT, 0.01)
        conf_shift = MAX_CONF_SHIFT if strong_adjust else min(MAX_CONF_SHIFT, 0.02)
    elif roi_avg >= ROI_GOOD and win_rate >= WINRATE_GOOD:
        status = "loosen"
        edge_shift = -MAX_EDGE_SHIFT if strong_adjust else -min(MAX_EDGE_SHIFT, 0.01)
        conf_shift = -MAX_CONF_SHIFT if strong_adjust else -min(MAX_CONF_SHIFT, 0.02)
    else:
        status = "hold"

    recommended_min_edge = max(0.01, BASE_MIN_EDGE + edge_shift)
    recommended_min_confidence = min(0.99, max(0.01, BASE_MIN_CONF + conf_shift))

    return {
        "status": status,
        "allow_adjust": True,
        "strong_adjust": strong_adjust,
        "edge_shift": round(edge_shift, 4),
        "conf_shift": round(conf_shift, 4),
        "recommended_min_edge": round(recommended_min_edge, 4),
        "recommended_min_confidence": round(recommended_min_confidence, 4),
    }


def fetch_live_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            sport_key,
            market,
            result,
            profit_units,
            confidence,
            display_edge,
            1.0 AS source_weight
        FROM tracked_bets
        WHERE result IS NOT NULL
          AND TRIM(result) <> ''
          AND LOWER(TRIM(result)) <> 'pending'
        """
    ).fetchall()


def fetch_historical_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    try:
        return conn.execute(
            """
            SELECT
                sport_key,
                market,
                result,
                profit_units,
                confidence,
                edge AS display_edge,
                source_weight
            FROM historical_bets
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def main() -> None:
    if not DB_PATH.exists():
        print(f"missing db: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    live_rows = fetch_live_rows(conn)
    historical_rows = fetch_historical_rows(conn)
    rows = list(live_rows) + list(historical_rows)

    if not rows:
        print("No settled or historical rows found.")
        conn.close()
        return

    print("--- Learning Engine Status ---")
    print(f"live rows: {len(live_rows)}")
    print(f"historical rows: {len(historical_rows)}")

    buckets: dict[str, dict[str, Any]] = {}
    edge_bands: dict[str, dict[str, Any]] = {}

    for r in rows:
        sport_key = norm(r["sport_key"]) or "unknown"
        market = norm(r["market"]) or "unknown"
        result = norm(r["result"])
        profit = to_float(r["profit_units"], 0.0)
        conf = to_float(r["confidence"], 0.0)
        disp_edge = to_float(r["display_edge"], 0.0)
        weight = to_float(r["source_weight"], 1.0)

        bkey = bucket_key(sport_key, market)
        band = edge_band(disp_edge)
        ebkey = f"{bkey}|{band}"

        if bkey not in buckets:
            buckets[bkey] = {
                "sport_key": sport_key,
                "market": market,
                "raw_samples": 0,
                "effective_samples": 0.0,
                "weighted_wins": 0.0,
                "weighted_losses": 0.0,
                "weighted_pushes": 0.0,
                "weighted_profit_units": 0.0,
                "avg_edge_sum": 0.0,
                "avg_conf_sum": 0.0,
            }

        if ebkey not in edge_bands:
            edge_bands[ebkey] = {
                "bucket_key": bkey,
                "edge_band": band,
                "raw_samples": 0,
                "effective_samples": 0.0,
                "weighted_wins": 0.0,
                "weighted_losses": 0.0,
                "weighted_pushes": 0.0,
                "weighted_profit_units": 0.0,
            }

        buckets[bkey]["raw_samples"] += 1
        buckets[bkey]["effective_samples"] += weight
        buckets[bkey]["weighted_profit_units"] += profit * weight
        buckets[bkey]["avg_edge_sum"] += disp_edge * weight
        buckets[bkey]["avg_conf_sum"] += conf * weight

        edge_bands[ebkey]["raw_samples"] += 1
        edge_bands[ebkey]["effective_samples"] += weight
        edge_bands[ebkey]["weighted_profit_units"] += profit * weight

        if result == "win":
            buckets[bkey]["weighted_wins"] += weight
            edge_bands[ebkey]["weighted_wins"] += weight
        elif result == "loss":
            buckets[bkey]["weighted_losses"] += weight
            edge_bands[ebkey]["weighted_losses"] += weight
        elif result == "push":
            buckets[bkey]["weighted_pushes"] += weight
            edge_bands[ebkey]["weighted_pushes"] += weight

    policy = {
        "meta": {
            "min_effective_samples_to_adjust": MIN_SAMPLES_TO_ADJUST,
            "min_effective_samples_strong_adjust": MIN_SAMPLES_STRONG_ADJUST,
            "base_min_edge": BASE_MIN_EDGE,
            "base_min_confidence": BASE_MIN_CONF,
        },
        "buckets": {},
        "edge_bands": {},
    }

    for bkey, stats in sorted(buckets.items()):
        raw_samples = int(stats["raw_samples"])
        effective_samples = float(stats["effective_samples"])
        wins = float(stats["weighted_wins"])
        losses = float(stats["weighted_losses"])
        pushes = float(stats["weighted_pushes"])
        profit_units = float(stats["weighted_profit_units"])

        graded = wins + losses
        win_rate = (wins / graded) if graded > 0 else 0.0
        roi_avg = (profit_units / effective_samples) if effective_samples > 0 else 0.0
        avg_edge = (stats["avg_edge_sum"] / effective_samples) if effective_samples > 0 else 0.0
        avg_conf = (stats["avg_conf_sum"] / effective_samples) if effective_samples > 0 else 0.0

        adj = compute_adjustment(effective_samples, win_rate, roi_avg)

        policy["buckets"][bkey] = {
            "sport_key": stats["sport_key"],
            "market": stats["market"],
            "raw_samples": raw_samples,
            "effective_samples": round(effective_samples, 2),
            "weighted_wins": round(wins, 2),
            "weighted_losses": round(losses, 2),
            "weighted_pushes": round(pushes, 2),
            "win_rate": round(win_rate, 4),
            "profit_units": round(profit_units, 4),
            "roi_avg_per_effective_bet": round(roi_avg, 4),
            "avg_display_edge": round(avg_edge, 4),
            "avg_confidence": round(avg_conf, 4),
            **adj,
        }

        print(
            f"Bucket: {bkey}\n"
            f"  Raw Samples: {raw_samples}\n"
            f"  Effective Samples: {round(effective_samples, 2)}\n"
            f"  Win Rate: {win_rate:.2%}\n"
            f"  ROI Avg: {roi_avg:.4f}\n"
            f"  Status: {adj['status']}\n"
            f"  Allow Adjust: {adj['allow_adjust']}"
        )

    for ebkey, stats in sorted(edge_bands.items()):
        effective_samples = float(stats["effective_samples"])
        wins = float(stats["weighted_wins"])
        losses = float(stats["weighted_losses"])
        pushes = float(stats["weighted_pushes"])
        profit_units = float(stats["weighted_profit_units"])
        graded = wins + losses
        win_rate = (wins / graded) if graded > 0 else 0.0
        roi_avg = (profit_units / effective_samples) if effective_samples > 0 else 0.0

        policy["edge_bands"][ebkey] = {
            "bucket_key": stats["bucket_key"],
            "edge_band": stats["edge_band"],
            "raw_samples": int(stats["raw_samples"]),
            "effective_samples": round(effective_samples, 2),
            "weighted_wins": round(wins, 2),
            "weighted_losses": round(losses, 2),
            "weighted_pushes": round(pushes, 2),
            "win_rate": round(win_rate, 4),
            "profit_units": round(profit_units, 4),
            "roi_avg_per_effective_bet": round(roi_avg, 4),
            "adjust_ready": effective_samples >= MIN_SAMPLES_TO_ADJUST,
        }

    save_json(POLICY_PATH, policy)
    conn.close()

    print(f"\nPolicy written to {POLICY_PATH}")


if __name__ == "__main__":
    main()
