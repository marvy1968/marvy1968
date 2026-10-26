#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any

MAIN_RESULTS_LOG = "logs/marchedge_results.jsonl"
TEAM_PROPS_RESULTS_LOG = "logs/team_props_results.jsonl"


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


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def settled_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("win") is not None]


def bucket_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    bets = len(rows)
    wins = sum(1 for r in rows if r.get("win") is True)
    losses = sum(1 for r in rows if r.get("win") is False)

    units_risked = round(sum(to_float(r.get("units_risked"), 0.0) for r in rows), 4)
    units_profit = round(sum(to_float(r.get("units_profit"), 0.0) for r in rows), 4)
    roi = round(units_profit / units_risked, 4) if units_risked > 0 else 0.0

    avg_edge = round(
        sum(to_float(r.get("edge"), 0.0) for r in rows) / bets, 4
    ) if bets else 0.0

    avg_confidence = round(
        sum(to_float(r.get("confidence"), 0.0) for r in rows) / bets, 2
    ) if bets else 0.0

    avg_kelly = round(
        sum(to_float(r.get("kelly_quarter"), 0.0) for r in rows) / bets, 4
    ) if bets else 0.0

    avg_clv_values = [to_float(r.get("clv"), None) for r in rows if r.get("clv") is not None]
    avg_clv_values = [x for x in avg_clv_values if x is not None]
    avg_clv = round(sum(avg_clv_values) / len(avg_clv_values), 3) if avg_clv_values else None

    return {
        "bets": bets,
        "wins": wins,
        "losses": losses,
        "winrate": round(wins / bets, 3) if bets else 0.0,
        "units_risked": units_risked,
        "units_profit": units_profit,
        "roi": roi,
        "avg_edge": avg_edge,
        "avg_confidence": avg_confidence,
        "avg_kelly": avg_kelly,
        "avg_clv": avg_clv,
    }


def edge_bucket(edge: float) -> str:
    if edge < 0.05:
        return "<5%"
    if edge < 0.10:
        return "5-10%"
    if edge < 0.15:
        return "10-15%"
    if edge < 0.20:
        return "15-20%"
    return "20%+"


def analyze_rows(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    rows = settled_rows(rows)

    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_strength: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_suspicious: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_edge_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for r in rows:
        by_market[str(r.get("market", "unknown"))].append(r)
        by_strength[str(r.get("strength", "None"))].append(r)

        suspicious_key = "suspicious" if bool(r.get("suspicious", False)) else "clean"
        by_suspicious[suspicious_key].append(r)

        eb = edge_bucket(to_float(r.get("edge"), 0.0))
        by_edge_bucket[eb].append(r)

    return {
        "label": label,
        "overall": bucket_summary(rows),
        "by_market": {k: bucket_summary(v) for k, v in sorted(by_market.items())},
        "by_strength": {k: bucket_summary(v) for k, v in sorted(by_strength.items())},
        "by_suspicious": {k: bucket_summary(v) for k, v in sorted(by_suspicious.items())},
        "by_edge_bucket": {k: bucket_summary(v) for k, v in sorted(by_edge_bucket.items())},
    }


def combine_rows(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for g in groups:
        out.extend(g)
    return out


def print_section(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def print_summary_block(title: str, data: dict[str, Any]) -> None:
    print(f"\n{title}")
    for k, v in data.items():
        print(f"{k}: {v}")


def print_analysis(analysis: dict[str, Any]) -> None:
    print_section(f"PHASE 26 LOG ANALYZER — {analysis['label']}")
    print_summary_block("OVERALL", analysis["overall"])
    print_summary_block("BY MARKET", analysis["by_market"])
    print_summary_block("BY STRENGTH", analysis["by_strength"])
    print_summary_block("BY SUSPICIOUS", analysis["by_suspicious"])
    print_summary_block("BY EDGE BUCKET", analysis["by_edge_bucket"])


def recommendations(combined_analysis: dict[str, Any]) -> list[str]:
    recs: list[str] = []

    overall = combined_analysis["overall"]
    if overall["bets"] == 0:
        recs.append("No settled bets yet. Let the bot collect results before tuning thresholds.")
        return recs

    by_strength = combined_analysis["by_strength"]
    if "C" in by_strength and by_strength["C"]["roi"] < 0:
        recs.append("C-grade plays are underperforming. Consider alerting only A/B plays.")
    if "B" in by_strength and by_strength["B"]["roi"] < 0 and "A" in by_strength and by_strength["A"]["roi"] > 0:
        recs.append("B-grade plays look weaker than A-grade. Consider raising the minimum strength threshold.")

    by_suspicious = combined_analysis["by_suspicious"]
    if "suspicious" in by_suspicious and by_suspicious["suspicious"]["bets"] > 0:
        if by_suspicious["suspicious"]["roi"] < by_suspicious.get("clean", {}).get("roi", 0):
            recs.append("Suspicious plays are underperforming clean plays. Tighten suspicious filters or suppress them.")

    by_edge_bucket = combined_analysis["by_edge_bucket"]
    if "<5%" in by_edge_bucket and by_edge_bucket["<5%"]["bets"] > 0 and by_edge_bucket["<5%"]["roi"] < 0:
        recs.append("Sub-5% edge bets look weak. Raise minimum edge threshold.")
    if "20%+" in by_edge_bucket and by_edge_bucket["20%+"]["bets"] > 0 and by_edge_bucket["20%+"]["roi"] < 0:
        recs.append("Very high-edge bets may be overconfident. Review calibration or suspicious-gap logic.")

    by_market = combined_analysis["by_market"]
    if "total" in by_market and by_market["total"]["bets"] > 0 and by_market["total"]["roi"] < 0:
        recs.append("Totals are underperforming. Tighten total-gap filters or increase total edge threshold.")
    if "spread" in by_market and by_market["spread"]["bets"] > 0 and by_market["spread"]["roi"] > overall["roi"]:
        recs.append("Spreads are outperforming overall. Consider prioritizing spread alerts.")

    if overall["avg_clv"] is not None and overall["avg_clv"] < 0:
        recs.append("Average CLV is negative. Entry timing may be poor — Phase 27 should focus on timing filters.")

    if not recs:
        recs.append("No obvious weakness yet. Keep collecting data before tightening the model.")

    return recs


if __name__ == "__main__":
    main_rows = load_jsonl(MAIN_RESULTS_LOG)
    prop_rows = load_jsonl(TEAM_PROPS_RESULTS_LOG)
    combined_rows = combine_rows(main_rows, prop_rows)

    main_analysis = analyze_rows(main_rows, "MAIN RESULTS")
    props_analysis = analyze_rows(prop_rows, "TEAM PROP RESULTS")
    combined_analysis = analyze_rows(combined_rows, "COMBINED RESULTS")

    print_analysis(main_analysis)
    print_analysis(props_analysis)
    print_analysis(combined_analysis)

    print_section("PHASE 26 RECOMMENDATIONS")
    for rec in recommendations(combined_analysis):
        print(f"- {rec}")
