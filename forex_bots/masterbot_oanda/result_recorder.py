#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from typing import Any

SNAPSHOT_LOG = "bet_snapshots.jsonl"
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
                continue
    return rows


def append_jsonl(path: str, row: dict[str, Any]) -> None:
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def normalize_game(game: str) -> str:
    return " ".join(str(game).lower().strip().split())


def make_game(home_team: str, away_team: str) -> str:
    return f"{away_team} @ {home_team}"


def already_recorded(
    game: str,
    market: str,
    side: str,
    line: Any,
    result_rows: list[dict[str, Any]],
) -> bool:
    ngame = normalize_game(game)

    for row in result_rows:
        if (
            normalize_game(row.get("game", "")) == ngame
            and str(row.get("market", "")) == str(market)
            and str(row.get("side", "")) == str(side)
            and str(row.get("line", None)) == str(line)
        ):
            return True

    return False


def infer_closing_line(
    bet: dict[str, Any],
    snapshot: dict[str, Any],
    closing_total: float | None,
    closing_home_spread: float | None,
    closing_away_spread: float | None,
    closing_home_ml: int | None,
    closing_away_ml: int | None,
) -> float | int | None:
    market = bet.get("market")
    team = bet.get("team")
    home_team = snapshot.get("home_team")
    away_team = snapshot.get("away_team")

    if market == "total":
        return closing_total

    if market == "spread":
        if team == home_team:
            return closing_home_spread
        if team == away_team:
            return closing_away_spread
        return None

    if market == "moneyline":
        if team == home_team:
            return closing_home_ml
        if team == away_team:
            return closing_away_ml
        return None

    return None


def build_result_row(
    snapshot: dict[str, Any],
    bet: dict[str, Any],
    final_home: int,
    final_away: int,
    closing_total: float | None,
    closing_home_spread: float | None,
    closing_away_spread: float | None,
    closing_home_ml: int | None,
    closing_away_ml: int | None,
) -> dict[str, Any]:
    snap_game = make_game(
        home_team=snapshot.get("home_team", ""),
        away_team=snapshot.get("away_team", ""),
    )

    closing_line = infer_closing_line(
        bet=bet,
        snapshot=snapshot,
        closing_total=closing_total,
        closing_home_spread=closing_home_spread,
        closing_away_spread=closing_away_spread,
        closing_home_ml=closing_home_ml,
        closing_away_ml=closing_away_ml,
    )

    return {
        "game": snap_game,
        "home_team": snapshot.get("home_team"),
        "away_team": snapshot.get("away_team"),
        "market": bet.get("market"),
        "side": bet.get("side"),
        "team": bet.get("team"),
        "line": bet.get("line"),
        "odds": bet.get("odds"),
        "model_prob": bet.get("model_prob"),
        "implied_prob": bet.get("implied_prob"),
        "edge": bet.get("edge_display"),
        "display_edge": bet.get("edge_display"),
        "ev": bet.get("ev"),
        "stake": bet.get("stake"),
        "strength": bet.get("strength"),
        "confidence": bet.get("confidence"),
        "score": bet.get("score"),
        "suspicious": bet.get("suspicious", False),
        "timestamp": snapshot.get("timestamp"),
        "final_home": int(final_home),
        "final_away": int(final_away),
        "closing_line": closing_line,
    }


def record_result_from_snapshots(
    game: str,
    final_home: int,
    final_away: int,
    closing_total: float | None = None,
    closing_home_spread: float | None = None,
    closing_away_spread: float | None = None,
    closing_home_ml: int | None = None,
    closing_away_ml: int | None = None,
) -> int:
    snapshots = load_jsonl(SNAPSHOT_LOG)
    existing_results = load_jsonl(RESULT_LOG)

    target_game = normalize_game(game)
    written = 0

    for snapshot in snapshots:
        snap_game = make_game(
            home_team=snapshot.get("home_team", ""),
            away_team=snapshot.get("away_team", ""),
        )

        if normalize_game(snap_game) != target_game:
            continue

        for bet in snapshot.get("top_bets", []):
            market = bet.get("market")
            side = bet.get("side")
            line = bet.get("line")

            if already_recorded(
                game=snap_game,
                market=market,
                side=side,
                line=line,
                result_rows=existing_results,
            ):
                continue

            row = build_result_row(
                snapshot=snapshot,
                bet=bet,
                final_home=final_home,
                final_away=final_away,
                closing_total=closing_total,
                closing_home_spread=closing_home_spread,
                closing_away_spread=closing_away_spread,
                closing_home_ml=closing_home_ml,
                closing_away_ml=closing_away_ml,
            )

            append_jsonl(RESULT_LOG, row)
            existing_results.append(row)
            written += 1

    return written


def record_last_snapshot_game(
    final_home: int,
    final_away: int,
    closing_total: float | None = None,
    closing_home_spread: float | None = None,
    closing_away_spread: float | None = None,
    closing_home_ml: int | None = None,
    closing_away_ml: int | None = None,
) -> tuple[str | None, int]:
    snapshots = load_jsonl(SNAPSHOT_LOG)
    if not snapshots:
        return None, 0

    last = snapshots[-1]
    game = make_game(
        home_team=last.get("home_team", ""),
        away_team=last.get("away_team", ""),
    )

    written = record_result_from_snapshots(
        game=game,
        final_home=final_home,
        final_away=final_away,
        closing_total=closing_total,
        closing_home_spread=closing_home_spread,
        closing_away_spread=closing_away_spread,
        closing_home_ml=closing_home_ml,
        closing_away_ml=closing_away_ml,
    )

    return game, written


if __name__ == "__main__":
    game, written = record_last_snapshot_game(
        final_home=78,
        final_away=72,
        closing_total=114.5,
        closing_home_spread=-4.5,
        closing_away_spread=4.5,
        closing_home_ml=-180,
        closing_away_ml=155,
    )

    print("RESULT RECORDER")
    print("game:", game)
    print("rows_written:", written)
