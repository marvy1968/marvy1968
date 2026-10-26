#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any

TEAM_PROPS_LOG = "logs/team_props_bets.jsonl"
TEAM_PROPS_RESULTS = "logs/team_props_results.jsonl"


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


def append_jsonl(path: str, row: dict[str, Any]) -> None:
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def normalize_game(game: str) -> str:
    return " ".join(str(game).lower().strip().split())


def result_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        normalize_game(row.get("game", "")),
        str(row.get("team", "")),
        str(row.get("side", "")),
        str(row.get("line", None)),
    )


def existing_result_keys(rows: list[dict[str, Any]]) -> set[tuple[str, str, str, str]]:
    return {result_key(r) for r in rows}


def american_profit_for_one_unit_risk(odds: Any) -> float:
    odds_f = to_float(odds, 0.0)
    if odds_f == 0:
        return 0.0

    if odds_f > 0:
        return round(odds_f / 100.0, 4)

    return round(100.0 / abs(odds_f), 4)


def compute_clv(side: str, open_line: Any, closing_line: Any) -> float | None:
    open_f = to_float(open_line, None)
    close_f = to_float(closing_line, None)

    if open_f is None or close_f is None:
        return None

    side = str(side).lower()
    if side == "over":
        return round(close_f - open_f, 3)
    if side == "under":
        return round(open_f - close_f, 3)

    return None


def grade_team_total(side: str, final_team_score: int, line: Any) -> bool | None:
    line_f = to_float(line, None)
    if line_f is None:
        return None

    side = str(side).lower()
    if side == "over":
        return final_team_score > line_f
    if side == "under":
        return final_team_score < line_f

    return None


def record_team_prop_results(
    game: str,
    final_home: int,
    final_away: int,
    closing_home_team_total: float | None = None,
    closing_away_team_total: float | None = None,
) -> int:
    bets = load_jsonl(TEAM_PROPS_LOG)
    results = load_jsonl(TEAM_PROPS_RESULTS)
    seen = existing_result_keys(results)

    target_game = normalize_game(game)
    written = 0

    for bet in bets:
        if normalize_game(bet.get("game", "")) != target_game:
            continue

        key = result_key(bet)
        if key in seen:
            continue

        team = bet.get("team")
        home_team = bet.get("home_team")
        away_team = bet.get("away_team")

        if team == home_team:
            final_team_score = int(final_home)
            closing_line = closing_home_team_total
        elif team == away_team:
            final_team_score = int(final_away)
            closing_line = closing_away_team_total
        else:
            continue

        win = grade_team_total(
            side=bet.get("side"),
            final_team_score=final_team_score,
            line=bet.get("line"),
        )

        stake = to_float(bet.get("kelly_quarter"), 1.0)
        profit_per_unit = american_profit_for_one_unit_risk(bet.get("odds"))

        if win is True:
            units_profit = round(stake * profit_per_unit, 4)
        elif win is False:
            units_profit = round(-stake, 4)
        else:
            units_profit = 0.0

        row = {
            "timestamp": bet.get("timestamp"),
            "game": bet.get("game"),
            "home_team": home_team,
            "away_team": away_team,
            "market": bet.get("market", "team_total"),
            "team": team,
            "side": bet.get("side"),
            "line": bet.get("line"),
            "closing_line": closing_line,
            "odds": bet.get("odds"),
            "projected_final_raw": bet.get("projected_final_raw"),
            "projected_final_calibrated": bet.get("projected_final_calibrated"),
            "model_prob": bet.get("model_prob"),
            "implied_prob": bet.get("implied_prob"),
            "edge": bet.get("edge"),
            "confidence": bet.get("confidence"),
            "kelly_quarter": stake,
            "strength": bet.get("strength"),
            "suspicious": bet.get("suspicious", False),
            "phase": bet.get("phase"),
            "pace_bucket": bet.get("pace_bucket"),
            "one_possession_game": bet.get("one_possession_game"),
            "blowout_flag": bet.get("blowout_flag"),
            "summary": bet.get("summary"),
            "final_home": int(final_home),
            "final_away": int(final_away),
            "final_team_score": final_team_score,
            "win": win,
            "clv": compute_clv(bet.get("side"), bet.get("line"), closing_line),
            "units_risked": stake,
            "units_profit": units_profit,
        }

        append_jsonl(TEAM_PROPS_RESULTS, row)
        seen.add(key)
        written += 1

    return written


def summarize_team_prop_results() -> dict[str, Any]:
    rows = load_jsonl(TEAM_PROPS_RESULTS)
    settled = [r for r in rows if r.get("win") is not None]

    bets = len(settled)
    wins = sum(1 for r in settled if r["win"] is True)
    losses = sum(1 for r in settled if r["win"] is False)
    winrate = round(wins / bets, 3) if bets else 0.0

    clv_values = [to_float(r.get("clv"), None) for r in settled if r.get("clv") is not None]
    clv_values = [x for x in clv_values if x is not None]
    avg_clv = round(sum(clv_values) / len(clv_values), 3) if clv_values else None

    units_risked = round(sum(to_float(r.get("units_risked"), 0.0) for r in settled), 4)
    units_profit = round(sum(to_float(r.get("units_profit"), 0.0) for r in settled), 4)
    roi = round(units_profit / units_risked, 4) if units_risked > 0 else 0.0

    by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_strength: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in settled:
        by_team[str(row.get("team", "unknown"))].append(row)
        by_strength[str(row.get("strength", "None"))].append(row)

    def bucket_summary(bucket_rows: list[dict[str, Any]]) -> dict[str, Any]:
        b = len(bucket_rows)
        w = sum(1 for r in bucket_rows if r["win"] is True)
        l = sum(1 for r in bucket_rows if r["win"] is False)
        wr = round(w / b, 3) if b else 0.0
        ur = round(sum(to_float(r.get("units_risked"), 0.0) for r in bucket_rows), 4)
        up = round(sum(to_float(r.get("units_profit"), 0.0) for r in bucket_rows), 4)
        broi = round(up / ur, 4) if ur > 0 else 0.0
        return {
            "bets": b,
            "wins": w,
            "losses": l,
            "winrate": wr,
            "units_risked": ur,
            "units_profit": up,
            "roi": broi,
        }

    return {
        "bets": bets,
        "wins": wins,
        "losses": losses,
        "winrate": winrate,
        "avg_clv": avg_clv,
        "units_risked": units_risked,
        "units_profit": units_profit,
        "roi": roi,
        "by_team": {k: bucket_summary(v) for k, v in by_team.items()},
        "by_strength": {k: bucket_summary(v) for k, v in by_strength.items()},
    }


if __name__ == "__main__":
    written = record_team_prop_results(
        game="Auburn @ Duke",
        final_home=78,
        final_away=72,
        closing_home_team_total=60.5,
        closing_away_team_total=54.5,
    )

    print("TEAM PROPS SETTLEMENT")
    print("rows_written:", written)

    summary = summarize_team_prop_results()
    print("summary:", summary)
