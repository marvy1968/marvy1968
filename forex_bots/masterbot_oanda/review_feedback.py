#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import statistics
from collections import defaultdict
from typing import Any

#############################################
# CONFIG
#############################################

BET_LOG_JSONL = os.getenv("MARCHEDGE_BET_JSONL", "logs/marchedge_bets.jsonl")
REVIEW_REPORT_JSON = os.getenv("MARCHEDGE_REVIEW_JSON", "logs/review_report.json")
REVIEW_REPORT_CSV = os.getenv("MARCHEDGE_REVIEW_CSV", "logs/review_report.csv")

#############################################
# HELPERS
#############################################


def ensure_parent(path: str) -> None:
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)


def load_jsonl(path: str) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []

    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def save_json(path: str, data: Any) -> None:
    ensure_parent(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def save_csv(path: str, rows: list[dict[str, Any]]) -> None:
    ensure_parent(path)
    if not rows:
        return

    keys = list(rows[0].keys())
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def to_float(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return d


def bucket_edge(edge: float) -> str:
    if edge >= 0.20:
        return "20%+"
    if edge >= 0.10:
        return "10-20%"
    if edge >= 0.05:
        return "5-10%"
    return "under_5%"


#############################################
# CORE REVIEW
#############################################


def summarize_signals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)

    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_strength: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_suspicious: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_edge_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)

    edges = []
    display_edges = []
    confidences = []
    kellys = []
    scores = []

    for row in rows:
        market = str(row.get("market", "unknown"))
        strength = str(row.get("strength", "unknown"))
        suspicious = "suspicious" if bool(row.get("suspicious", False)) else "clean"
        edge = to_float(row.get("edge", 0.0))
        display_edge = to_float(row.get("display_edge", edge))
        confidence = to_float(row.get("confidence", 0.0))
        kelly = to_float(row.get("kelly_quarter", 0.0))
        score = to_float(row.get("score", 0.0))
        game = str(row.get("game", "unknown"))

        by_market[market].append(row)
        by_strength[strength].append(row)
        by_suspicious[suspicious].append(row)
        by_edge_bucket[bucket_edge(edge)].append(row)
        by_game[game].append(row)

        edges.append(edge)
        display_edges.append(display_edge)
        confidences.append(confidence)
        kellys.append(kelly)
        scores.append(score)

    def stats(arr: list[float]) -> dict[str, float]:
        if not arr:
            return {"avg": 0.0, "median": 0.0, "max": 0.0, "min": 0.0}
        return {
            "avg": round(sum(arr) / len(arr), 4),
            "median": round(statistics.median(arr), 4),
            "max": round(max(arr), 4),
            "min": round(min(arr), 4),
        }

    summary = {
        "total_signals": total,
        "edge_stats": stats(edges),
        "display_edge_stats": stats(display_edges),
        "confidence_stats": stats(confidences),
        "kelly_stats": stats(kellys),
        "score_stats": stats(scores),
        "market_breakdown": {
            k: {
                "count": len(v),
                "avg_edge": round(sum(to_float(r.get("edge", 0.0)) for r in v) / len(v), 4),
                "avg_confidence": round(sum(to_float(r.get("confidence", 0.0)) for r in v) / len(v), 2),
                "avg_kelly": round(sum(to_float(r.get("kelly_quarter", 0.0)) for r in v) / len(v), 4),
            }
            for k, v in by_market.items()
        },
        "strength_breakdown": {
            k: {
                "count": len(v),
                "avg_edge": round(sum(to_float(r.get("edge", 0.0)) for r in v) / len(v), 4),
                "avg_confidence": round(sum(to_float(r.get("confidence", 0.0)) for r in v) / len(v), 2),
            }
            for k, v in by_strength.items()
        },
        "suspicious_breakdown": {
            k: {
                "count": len(v),
                "avg_edge": round(sum(to_float(r.get("edge", 0.0)) for r in v) / len(v), 4),
                "avg_confidence": round(sum(to_float(r.get("confidence", 0.0)) for r in v) / len(v), 2),
            }
            for k, v in by_suspicious.items()
        },
        "edge_bucket_breakdown": {
            k: {
                "count": len(v),
                "avg_confidence": round(sum(to_float(r.get("confidence", 0.0)) for r in v) / len(v), 2),
                "avg_kelly": round(sum(to_float(r.get("kelly_quarter", 0.0)) for r in v) / len(v), 4),
            }
            for k, v in by_edge_bucket.items()
        },
        "games_with_most_signals": sorted(
            [{"game": game, "count": len(v)} for game, v in by_game.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:10],
    }

    return summary


def generate_feedback(summary: dict[str, Any]) -> list[str]:
    feedback: list[str] = []

    market_breakdown = summary.get("market_breakdown", {})
    suspicious_breakdown = summary.get("suspicious_breakdown", {})
    strength_breakdown = summary.get("strength_breakdown", {})

    totals = market_breakdown.get("total", {}).get("count", 0)
    spreads = market_breakdown.get("spread", {}).get("count", 0)
    moneylines = market_breakdown.get("moneyline", {}).get("count", 0)

    suspicious_count = suspicious_breakdown.get("suspicious", {}).get("count", 0)
    clean_count = suspicious_breakdown.get("clean", {}).get("count", 0)

    if totals > spreads + moneylines:
        feedback.append("Totals are dominating the signal mix. Consider stricter total-market calibration or higher total thresholds.")

    if suspicious_count > clean_count:
        feedback.append("Suspicious signals outnumber clean signals. Tighten calibration or suppress suspicious markets before alerting.")

    if strength_breakdown.get("D", {}).get("count", 0) > 0:
        feedback.append("D-grade signals are still being generated. Consider suppressing D alerts in production.")

    if strength_breakdown.get("A", {}).get("count", 0) == 0:
        feedback.append("No A-grade signals found. Review confidence and edge thresholds for over-filtering.")

    avg_kelly = summary.get("kelly_stats", {}).get("avg", 0.0)
    if avg_kelly > 0.10:
        feedback.append("Average Kelly is relatively high. Consider more conservative risk caps for live production.")

    avg_edge = summary.get("edge_stats", {}).get("avg", 0.0)
    if avg_edge > 0.20:
        feedback.append("Average edge is very high. This can be a sign of model overconfidence or insufficient anchoring.")

    if not feedback:
        feedback.append("Signal mix looks stable. Continue collecting data and move toward CLV/outcome tracking.")

    return feedback


def flatten_summary(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for section_name in ["market_breakdown", "strength_breakdown", "suspicious_breakdown", "edge_bucket_breakdown"]:
        section = summary.get(section_name, {})
        for key, values in section.items():
            row = {"section": section_name, "bucket": key}
            row.update(values)
            rows.append(row)

    return rows


#############################################
# PUBLIC ENTRY
#############################################


def build_review_report(log_path: str = BET_LOG_JSONL) -> dict[str, Any]:
    rows = load_jsonl(log_path)
    summary = summarize_signals(rows)
    feedback = generate_feedback(summary)

    report = {
        "source_log": log_path,
        "summary": summary,
        "feedback": feedback,
    }

    save_json(REVIEW_REPORT_JSON, report)
    save_csv(REVIEW_REPORT_CSV, flatten_summary(summary))

    return report


#############################################
# DEMO
#############################################

if __name__ == "__main__":
    report = build_review_report()

    print("PHASE 16 REVIEW REPORT")
    print("source_log:", report["source_log"])
    print("total_signals:", report["summary"]["total_signals"])
    print("market_breakdown:", report["summary"]["market_breakdown"])
    print("strength_breakdown:", report["summary"]["strength_breakdown"])
    print("suspicious_breakdown:", report["summary"]["suspicious_breakdown"])
    print("\nFEEDBACK")
    for line in report["feedback"]:
        print("-", line)
