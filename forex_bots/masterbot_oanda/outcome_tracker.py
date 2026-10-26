#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any

RESULT_LOG = "logs/marchedge_results.jsonl"


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
                pass
    return rows


def to_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def grade_total(row: dict[str, Any], final_home: int, final_away: int) -> bool | None:
    side = str(row.get("side", "")).lower()
    line = to_float(row.get("line"))
    if line is None:
        return None

    total = final_home + final_away

    if side == "over":
        return total > line
    if side == "under":
        return total < line
    return None


def grade_moneyline(row: dict[str, Any], final_home: int, final_away: int) -> bool | None:
    team = row.get("team")
    home_team = row.get("home_team")
    away_team = row.get("away_team")

    if team == home_team:
        return final_home > final_away
    if team == away_team:
        return final_away > final_home
    return None


def grade_spread(row: dict[str, Any], final_home: int, final_away: int) -> bool | None:
    team = row.get("team")
    home_team = row.get("home_team")
    away_team = row.get("away_team")
    line = to_float(row.get("line"))

    if line is None:
        return None

    if team == home_team:
        adjusted_margin = (final_home - final_away) + line
        return adjusted_margin > 0

    if team == away_team:
        adjusted_margin = (final_away - final_home) + line
        return adjusted_margin > 0

    return None


def grade_bet(row: dict[str, Any]) -> bool | None:
    market = str(row.get("market", "")).lower()

    try:
        final_home = int(row.get("final_home"))
        final_away = int(row.get("final_away"))
    except Exception:
        return None

    if market == "total":
        return grade_total(row, final_home, final_away)

    if market == "moneyline":
        return grade_moneyline(row, final_home, final_away)

    if market == "spread":
        return grade_spread(row, final_home, final_away)

    return None


def compute_clv(row: dict[str, Any]) -> float | None:
    market = str(row.get("market", "")).lower()
    side = str(row.get("side", "")).lower()
    team = row.get("team")
    home_team = row.get("home_team")
    away_team = row.get("away_team")

    open_line = to_float(row.get("line"))
    closing_line = to_float(row.get("closing_line"))

    if open_line is None or closing_line is None:
        return None

    if market == "total":
        if side == "over":
            return round(closing_line - open_line, 3)
        if side == "under":
            return round(open_line - closing_line, 3)
        return None

    if market == "spread":
        if team == home_team:
            return round(open_line - closing_line, 3)
        if team == away_team:
            return round(closing_line - open_line, 3)
        return None

    if market == "moneyline":
        # Moneyline CLV is more reliable in implied probability space
        return None

    return None


def evaluate_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    graded: list[dict[str, Any]] = []

    for row in rows:
        win = grade_bet(row)
        clv = compute_clv(row)

        out = dict(row)
        out["win"] = win
        out["clv"] = clv
        graded.append(out)

    return graded


def summarize_bucket(bucket_rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [r for r in bucket_rows if r.get("win") is not None]

    bets = len(settled)
    wins = sum(1 for r in settled if r["win"] is True)
    losses = sum(1 for r in settled if r["win"] is False)
    winrate = round(wins / bets, 3) if bets else 0.0

    clv_values = [r["clv"] for r in settled if r.get("clv") is not None]
    avg_clv = round(sum(clv_values) / len(clv_values), 3) if clv_values else None

    return {
        "bets": bets,
        "wins": wins,
        "losses": losses,
        "winrate": winrate,
        "avg_clv": avg_clv,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [r for r in results if r.get("win") is not None]

    overall = summarize_bucket(settled)

    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_strength: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_suspicious: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for r in settled:
        by_market[str(r.get("market", "unknown"))].append(r)
        by_strength[str(r.get("strength", "None"))].append(r)
        by_suspicious["suspicious" if r.get("suspicious") else "clean"].append(r)

    return {
        "bets": overall["bets"],
        "wins": overall["wins"],
        "losses": overall["losses"],
        "winrate": overall["winrate"],
        "avg_clv": overall["avg_clv"],
        "by_market": {k: summarize_bucket(v) for k, v in by_market.items()},
        "by_strength": {k: summarize_bucket(v) for k, v in by_strength.items()},
        "by_suspicious": {k: summarize_bucket(v) for k, v in by_suspicious.items()},
    }


if __name__ == "__main__":
    rows = load_jsonl(RESULT_LOG)
    graded = evaluate_results(rows)
    summary = summarize(graded)

    print("PHASE 17 RESULTS")
    print("source_log:", RESULT_LOG)
    for k, v in summary.items():
        print(f"{k}: {v}")
