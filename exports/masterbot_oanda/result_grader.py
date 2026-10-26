#!/usr/bin/env python3
from __future__ import annotations

from typing import Any
from bet_tracker import init_tracking_db, fetch_open_bets, settle_bet, summary_report

def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default

def _grade_total(side: str, line: float, home_score: float, away_score: float) -> tuple[str, float]:
    total = home_score + away_score
    if side == "over":
        if total > line:
            return "win", 0.91
        if total < line:
            return "loss", -1.0
        return "push", 0.0
    if side == "under":
        if total < line:
            return "win", 0.91
        if total > line:
            return "loss", -1.0
        return "push", 0.0
    return "loss", -1.0

def _grade_spread(selection: str, line: float, home_team: str, away_team: str, home_score: float, away_score: float) -> tuple[str, float]:
    selection_l = selection.lower()
    margin_home = home_score - away_score

    if home_team.lower() in selection_l:
        adjusted = margin_home + line
    elif away_team.lower() in selection_l:
        adjusted = -margin_home + line
    else:
        return "loss", -1.0

    if adjusted > 0:
        return "win", 0.91
    if adjusted < 0:
        return "loss", -1.0
    return "push", 0.0

def _grade_h2h(selection: str, home_team: str, away_team: str, home_score: float, away_score: float) -> tuple[str, float]:
    selection_l = selection.lower()
    if home_score == away_score:
        return "push", 0.0

    winner = home_team if home_score > away_score else away_team
    if winner.lower() in selection_l:
        return "win", 0.91
    return "loss", -1.0

def grade_open_bets_from_scores(score_rows: list[dict[str, Any]], sport_key: str) -> int:
    """
    score_rows must look like:
    [
      {
        "id": "...",
        "completed": True,
        "home_team": "...",
        "away_team": "...",
        "scores": {"home": 77, "away": 64}
      }
    ]
    """
    init_tracking_db()
    open_bets = fetch_open_bets(sport_key=sport_key)
    if not open_bets:
        return 0

    score_map = {str(r["id"]): r for r in score_rows}
    settled = 0

    for bet in open_bets:
        game_id = str(bet["game_id"])
        row = score_map.get(game_id)
        if not row:
            continue
        if not row.get("completed", False):
            continue

        home_team = str(row["home_team"])
        away_team = str(row["away_team"])
        home_score = _safe_float(row.get("scores", {}).get("home"))
        away_score = _safe_float(row.get("scores", {}).get("away"))

        market_type = str(bet["market_type"]).lower()
        selection = str(bet["selection"])
        side = str(bet["side"]).lower()
        line = _safe_float(bet["line"])

        if market_type == "total":
            result, pnl = _grade_total(side, line, home_score, away_score)
        elif market_type == "spread":
            result, pnl = _grade_spread(selection, line, home_team, away_team, home_score, away_score)
        elif market_type in {"h2h", "moneyline"}:
            result, pnl = _grade_h2h(selection, home_team, away_team, home_score, away_score)
        else:
            continue

        settle_bet(int(bet["id"]), result, pnl)
        settled += 1

    return settled


if __name__ == "__main__":
    print(summary_report())
